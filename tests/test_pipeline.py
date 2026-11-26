"""生产管线与任务队列测试。"""

from __future__ import annotations

import time

from sqlalchemy import select

from app.db import session_scope
from app.models import Asset, Job, ModelService, VoiceProfile
from app.pipeline.jobs import runner
from app.pipeline.script import estimate_duration, split_script


def test_split_script_by_sentence_and_length() -> None:
    text = "第一句话。第二句话！这是一句很长的话，需要被切成多个片段，因为合成时逐句处理更稳。最后一句？"
    segments = split_script(text, max_chars=28)
    assert len(segments) >= 4
    assert all(len(s) <= 28 for s in segments)
    assert any("第一句话" in s for s in segments)
    assert estimate_duration("你好，世界。") > 1.0


def test_split_script_rejects_empty() -> None:
    import pytest

    with pytest.raises(ValueError):
        split_script("   ")


def _first_voice_id() -> str:
    with session_scope() as db:
        voice = db.scalars(select(VoiceProfile).order_by(VoiceProfile.created_at)).first()
        assert voice is not None
        return voice.id


def _local_model_id() -> str:
    with session_scope() as db:
        model = db.scalars(select(ModelService).where(ModelService.key == "local-engine")).first()
        assert model is not None
        return model.id


def _create_job(script: str) -> str:
    from app.pipeline.jobs import Reporter  # noqa: F401  — 确保模块加载

    with session_scope() as db:
        job = Job(
            kind="produce",
            title="测试任务",
            script=script,
            status="queued",
            model_id=_local_model_id(),
            voice_id=_first_voice_id(),
            input={
                "script": script,
                "title": "测试任务",
                "style": "studio",
                "width": 360,
                "height": 640,
                "fps": 2,
                "model_key": "local-engine",
            },
        )
        db.add(job)
        db.flush()
        return job.id


def _wait(job_id: str, timeout: float = 60.0) -> Job:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job.status in {"succeeded", "failed", "cancelled"}:
                db.expunge(job)
                return job
        time.sleep(0.15)
    raise AssertionError(f"任务 {job_id} 超时")


def test_produce_job_end_to_end() -> None:
    job_id = _create_job("大家好，今天我们测试一条数字人视频。它应该能自动生成语音和字幕。")
    runner.submit(job_id)
    job = _wait(job_id)

    assert job.status == "succeeded", job.error
    assert job.progress == 100
    assert job.output["duration"] > 1.0
    assert job.output["segment_count"] >= 1

    with session_scope() as db:
        assets = db.scalars(select(Asset).where(Asset.job_id == job_id)).all()
        kinds = {a.kind for a in assets}
        assert {"audio", "subtitle", "video", "manifest"} <= kinds
        video = next(a for a in assets if a.kind == "video")
        assert video.size_bytes > 20_000
        from pathlib import Path

        assert Path(video.path).exists()
        steps = [s.name for s in db.get(Job, job_id).steps]
        assert any("tts" in s for s in steps)
        assert any("avatar" in s for s in steps)


def test_cancel_running_job() -> None:
    job_id = _create_job("这一句用来测试取消。" * 6)
    runner.submit(job_id)
    time.sleep(0.25)
    result = runner.cancel(job_id)
    assert result["ok"] is True
    job = _wait(job_id)
    assert job.status in {"cancelled", "succeeded"}
    if job.status == "cancelled":
        assert "取消" in (job.error or "")


def test_retry_creates_new_job() -> None:
    job_id = _create_job("重试测试。")
    runner.submit(job_id)
    _wait(job_id)
    new_id = runner.retry(job_id)
    assert new_id != job_id
    job = _wait(new_id)
    assert job.status == "succeeded"
    assert "重试" in job.title
