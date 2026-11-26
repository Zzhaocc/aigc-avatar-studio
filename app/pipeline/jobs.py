"""任务队列：单 worker 顺序执行（GPU 只有一张卡的真实约束），
支持进度、步骤、取消、重试与进程重启恢复。
"""

from __future__ import annotations

import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.logging import get_logger
from app.models import Asset, Job, JobStep, ModelService, VoiceProfile, new_id
from app.pipeline.produce import JobCancelled, produce
from app.providers.base import ProviderError

log = get_logger("app.pipeline")


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    """SQLite 取回的是 naive datetime，按 UTC 解释；其它库返回 aware 时原样使用。"""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _now() -> datetime:
    return datetime.now(UTC)


class Reporter:
    """把管线里的进度 / 步骤 / 取消检查落到数据库。"""

    def __init__(self, job_id: str, runner: JobRunner) -> None:
        self.job_id = job_id
        self.runner = runner
        self._last_seq = 0

    def begin(self, key: str, name: str) -> int:
        self._last_seq += 1
        seq = self._last_seq
        with session_scope() as db:
            db.add(JobStep(job_id=self.job_id, seq=seq, name=f"{name}（{key}）", status="running", detail=""))
        return seq

    def detail(self, text: str) -> None:
        with session_scope() as db:
            step = db.scalars(
                select(JobStep).where(JobStep.job_id == self.job_id, JobStep.seq == self._last_seq)
            ).first()
            if step is not None:
                step.detail = text[:500]

    def finish(self, seq: int, detail: str = "", status: str = "succeeded") -> None:
        with session_scope() as db:
            step = db.scalars(select(JobStep).where(JobStep.job_id == self.job_id, JobStep.seq == seq)).first()
            if step is None:
                return
            step.status = status
            step.detail = detail[:500]
            step.progress = 100
            step.latency_ms = int((_now() - _aware(step.created_at)).total_seconds() * 1000)

    def progress(self, pct: int) -> None:
        with session_scope() as db:
            job = db.get(Job, self.job_id)
            if job is not None:
                job.progress = max(job.progress, min(100, int(pct)))

    def check_cancel(self) -> None:
        if self.runner.is_cancelled(self.job_id):
            raise JobCancelled("任务已取消")


class JobRunner:
    def __init__(self) -> None:
        settings = get_settings()
        self.executor = ThreadPoolExecutor(max_workers=max(1, settings.max_workers), thread_name_prefix="job")
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def submit(self, job_id: str) -> None:
        log.info("job_submitted", extra={"job_id": job_id})
        self.executor.submit(self._run, job_id)

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancelled

    def _forget_cancel(self, job_id: str) -> None:
        """任务结束后从取消集合里移除，避免长跑进程里集合无限增长。"""
        with self._lock:
            self._cancelled.discard(job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            self._cancelled.add(job_id)
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                self._forget_cancel(job_id)
                return {"ok": False, "reason": "任务不存在"}
            if job.status in {"succeeded", "failed", "cancelled"}:
                self._forget_cancel(job_id)
                return {"ok": False, "reason": f"任务已结束（{job.status}）"}
            job.cancel_requested = True
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = _now()
                job.error = "排队中取消"
                self._forget_cancel(job_id)
                log.info("job_cancelled", extra={"job_id": job_id, "stage": "queued"})
                return {"ok": True, "status": "cancelled"}
        log.info("job_cancel_requested", extra={"job_id": job_id})
        return {"ok": True, "status": "cancelling"}

    def retry(self, job_id: str) -> str:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            clone = Job(
                id=new_id("job"),
                kind=job.kind,
                title=f"{job.title}（重试）",
                script=job.script,
                status="queued",
                input=job.input,
                model_id=job.model_id,
                voice_id=job.voice_id,
            )
            db.add(clone)
            db.flush()
            new_id_value = clone.id
        self.submit(new_id_value)
        return new_id_value

    def recover(self) -> int:
        """进程重启：未完成的任务标记失败，避免永远 running。"""
        with session_scope() as db:
            rows = db.scalars(select(Job).where(Job.status.in_(["queued", "running"]))).all()
            for job in rows:
                job.status = "failed"
                job.error = "进程重启，任务中断（可重试）"
                job.finished_at = _now()
            return len(rows)

    # ------------------------------------------------------------------ #
    def _run(self, job_id: str) -> None:
        """执行入口：无论成功、失败还是取消，结束后都清掉取消标记。"""
        started = time.perf_counter()
        try:
            self._execute(job_id, started)
        finally:
            self._forget_cancel(job_id)

    def _execute(self, job_id: str, started: float) -> None:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                log.warning("job_missing", extra={"job_id": job_id})
                return
            if job.cancel_requested or self.is_cancelled(job_id):
                job.status = "cancelled"
                job.finished_at = _now()
                log.info("job_cancelled", extra={"job_id": job_id, "stage": "before_start"})
                return
            voice = db.get(VoiceProfile, job.voice_id) if job.voice_id else None
            model = db.get(ModelService, job.model_id) if job.model_id else None
            payload = dict(job.input or {})
            payload["model_key"] = model.key if model else "local-engine"
            voice_data = (
                {"id": voice.id, "name": voice.name, "pitch": voice.pitch, "speed": voice.speed, "timbre": voice.timbre}
                if voice
                else {"id": None, "name": "默认音色", "pitch": 1.0, "speed": 1.0, "timbre": "warm"}
            )
            job.status = "running"
            job.started_at = _now()
            job.progress = 1

        log.info("job_started", extra={"job_id": job_id, "model_key": payload["model_key"]})
        reporter = Reporter(job_id, self)
        try:
            result = produce(job_id, payload, voice_data, reporter)
            with session_scope() as db:
                for item in result["assets"]:
                    db.add(
                        Asset(
                            id=new_id("ast"),
                            job_id=job_id,
                            kind=item["kind"],
                            filename=item["filename"],
                            path=str(item["path"]),
                            mime=item["mime"],
                            size_bytes=int(item.get("size_bytes") or 0),
                            duration=item.get("duration"),
                        )
                    )
                db.flush()
                assets = db.scalars(select(Asset).where(Asset.job_id == job_id)).all()
                summary = {k: v for k, v in result.items() if k != "assets"}
                summary["assets"] = [
                    {
                        "id": a.id,
                        "kind": a.kind,
                        "filename": a.filename,
                        "size_bytes": a.size_bytes,
                        "duration": a.duration,
                    }
                    for a in assets
                ]
                job = db.get(Job, job_id)
                job.status = "succeeded"
                job.progress = 100
                job.output = summary
                job.finished_at = _now()
                job.duration_ms = int((time.perf_counter() - started) * 1000)

            log.info(
                "job_succeeded",
                extra={
                    "job_id": job_id,
                    "assets": len(assets),
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                },
            )

        except JobCancelled:
            self._mark_cancelled(job_id, started, reporter)
            log.info("job_cancelled", extra={"job_id": job_id, "stage": "running"})
        except ProviderError as exc:
            self._fail(job_id, started, str(exc), reporter)
            log.warning("job_failed", extra={"job_id": job_id, "error": str(exc)[:200]})
        except Exception as exc:  # noqa: BLE001 —— 任何异常都要落到 job.error，不能让线程静默死掉
            log.exception("job_failed", extra={"job_id": job_id})
            self._fail(job_id, started, f"{type(exc).__name__}: {exc}", reporter)

    # ------------------------------------------------------------------ #
    def _mark_cancelled(self, job_id: str, started: float, reporter: Reporter) -> None:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                job.status = "cancelled"
                job.error = "用户取消"
                job.finished_at = _now()
                job.duration_ms = int((time.perf_counter() - started) * 1000)
                for step in job.steps:
                    if step.status == "running":
                        step.status = "cancelled"
                        step.detail = "任务取消时中断"

    def _fail(self, job_id: str, started: float, message: str, reporter: Reporter) -> None:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                job.status = "failed"
                job.error = message[:1000]
                job.finished_at = _now()
                job.duration_ms = int((time.perf_counter() - started) * 1000)
                for step in job.steps:
                    if step.status == "running":
                        step.status = "failed"
                        step.detail = message[:300]


runner = JobRunner()


def cleanup_job_files(job_id: str) -> int:
    """删除任务产物目录（保留 DB 记录，文件不可再下载）。

    整目录清理：逐个删文件再删目录会因排序问题残留空目录。
    """
    settings = get_settings()
    target = settings.media_dir / job_id
    if not target.exists():
        return 0
    removed = sum(1 for path in target.rglob("*") if path.is_file())
    shutil.rmtree(target, ignore_errors=True)
    return removed
