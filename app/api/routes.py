"""REST 接口：模型服务 / 音色 / 生产任务 / 产物 / 工具箱 / 指标。"""

from __future__ import annotations

import base64
import binascii
import shutil
import struct
import time
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app import __version__
from app.api.schemas import ProduceRequest, ToolRequest, VoiceCloneRequest, VoiceCreate
from app.config import get_settings
from app.db import SessionLocal, get_db, ping
from app.models import Asset, Job, ModelService, VoiceProfile, new_id
from app.pipeline.jobs import cleanup_job_files, runner
from app.providers.catalog import DEMO_SCRIPTS, STYLES
from app.providers.manager import manager

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- #
# 序列化
# --------------------------------------------------------------------------- #
def _dt(value: datetime | None) -> str | None:
    """统一成 UTC ISO8601（带 Z）。SQLite 取回的是 naive datetime，按 UTC 处理。"""
    if value is None:
        return None
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _model_out(model: ModelService, health: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": model.id,
        "key": model.key,
        "name": model.name,
        "kind": model.kind,
        "version": model.version,
        "size_mb": model.size_mb,
        "status": model.status,
        "progress": model.progress,
        "port": model.port,
        "path": model.path,
        "description": model.description,
        "tags": model.tags or [],
        "builtin": model.builtin,
        "last_error": model.last_error,
        "installed_at": _dt(model.installed_at),
        "started_at": _dt(model.started_at),
        "health": health or manager.health(model),
    }


def _voice_out(voice: VoiceProfile) -> dict[str, Any]:
    return {
        "id": voice.id,
        "name": voice.name,
        "kind": voice.kind,
        "pitch": voice.pitch,
        "speed": voice.speed,
        "timbre": voice.timbre,
        "reference_duration": voice.reference_duration,
        "note": voice.note,
        "created_at": _dt(voice.created_at),
    }


def _asset_out(asset: Asset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "job_id": asset.job_id,
        "kind": asset.kind,
        "filename": asset.filename,
        "mime": asset.mime,
        "size_bytes": asset.size_bytes,
        "duration": asset.duration,
        "url": f"/api/assets/{asset.id}",
        "preview_url": f"/api/assets/{asset.id}?inline=1",
        "created_at": _dt(asset.created_at),
    }


def _job_out(job: Job, *, detail: bool = False) -> dict[str, Any]:
    data = {
        "id": job.id,
        "kind": job.kind,
        "title": job.title,
        "status": job.status,
        "progress": job.progress,
        "error": job.error,
        "model_id": job.model_id,
        "voice_id": job.voice_id,
        "duration_ms": job.duration_ms,
        "created_at": _dt(job.created_at),
        "started_at": _dt(job.started_at),
        "finished_at": _dt(job.finished_at),
        "output": job.output or {},
    }
    if detail:
        data["script"] = job.script
        data["input"] = job.input or {}
        data["steps"] = [
            {
                "seq": step.seq,
                "name": step.name,
                "status": step.status,
                "detail": step.detail,
                "progress": step.progress,
                "latency_ms": step.latency_ms,
            }
            for step in job.steps
        ]
        data["assets"] = [_asset_out(asset) for asset in job.assets]
    return data


# --------------------------------------------------------------------------- #
# 元信息
# --------------------------------------------------------------------------- #
@router.get("/health", tags=["meta"])
def health(request: Request) -> dict[str, Any]:
    """存活探针：进程能响应即返回 200，不查数据库、不调 ffmpeg。

    容器 / K8s 的 livenessProbe 指这里；依赖是否可用看 ``/api/ready``。
    """
    settings = get_settings()
    started_at = getattr(request.app.state, "started_at", None)
    return {
        "status": "ok",
        "version": __version__,
        "environment": settings.environment,
        "engine": "local-engine",
        "ffmpeg": settings.ffmpeg,
        "workers": max(1, settings.max_workers),
        "sample_rate": settings.sample_rate,
        "database": "sqlite" if settings.sqlite else "external",
        "docs_enabled": settings.docs_enabled,
        "uptime_s": round(time.time() - started_at, 3) if started_at else 0.0,
    }


@router.get("/ready", tags=["meta"])
def ready() -> JSONResponse:
    """就绪探针：数据库、ffmpeg/ffprobe、数据目录都可用才返回 200。

    返回 503 时应先摘流量：此时发起生产任务必然失败或产物写不进去。
    """
    settings = get_settings()
    checks = {
        "database": ping(),
        "ffmpeg": shutil.which(settings.ffmpeg) is not None,
        "ffprobe": shutil.which(settings.ffprobe) is not None,
        "data_dir": _writable(settings.data_dir),
    }
    ok = all(checks.values())
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ready" if ok else "degraded", "checks": checks, "version": __version__},
    )


def _writable(path: Path) -> bool:
    """用一次真实写入判断目录可写（比检查权限位可靠）。"""
    probe = path / ".ready_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


@router.get("/catalog", tags=["meta"])
def catalog(db: Session = Depends(get_db)) -> dict[str, Any]:
    models = db.scalars(
        select(ModelService).order_by(ModelService.builtin.desc(), ModelService.kind, ModelService.name)
    ).all()
    voices = db.scalars(select(VoiceProfile).order_by(VoiceProfile.created_at)).all()
    return {
        "version": __version__,
        "models": [_model_out(m) for m in models],
        "voices": [_voice_out(v) for v in voices],
        "styles": [{"key": k, **v} for k, v in STYLES.items()],
        "demo_scripts": DEMO_SCRIPTS,
        "resolution_presets": [
            {"label": "竖屏 720×1280", "width": 720, "height": 1280},
            {"label": "竖屏 1080×1920", "width": 1080, "height": 1920},
            {"label": "横屏 1280×720", "width": 1280, "height": 720},
        ],
    }


# --------------------------------------------------------------------------- #
# 模型服务
# --------------------------------------------------------------------------- #
@router.get("/models", tags=["models"])
def list_models(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(ModelService).order_by(ModelService.builtin.desc(), ModelService.kind)).all()
    return {"items": [_model_out(m) for m in rows]}


@router.get("/models/{model_id}", tags=["models"])
def get_model(model_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    model = db.get(ModelService, model_id)
    if model is None:
        raise HTTPException(404, "模型不存在")
    return _model_out(model)


def _act(model_id: str, action: str) -> dict[str, Any]:
    try:
        result = getattr(manager, action)(model_id)
    except KeyError as exc:
        raise HTTPException(404, "模型不存在") from exc
    if not result.get("ok"):
        raise HTTPException(409, result.get("reason", "操作失败"))
    with SessionLocal() as db:
        model = db.get(ModelService, model_id)
        return {"ok": True, **result, "model": _model_out(model) if model else None}


@router.post("/models/{model_id}/install", tags=["models"])
def install_model(model_id: str) -> dict[str, Any]:
    return _act(model_id, "install")


@router.post("/models/{model_id}/start", tags=["models"])
def start_model(model_id: str) -> dict[str, Any]:
    return _act(model_id, "start")


@router.post("/models/{model_id}/stop", tags=["models"])
def stop_model(model_id: str) -> dict[str, Any]:
    return _act(model_id, "stop")


@router.post("/models/{model_id}/uninstall", tags=["models"])
def uninstall_model(model_id: str) -> dict[str, Any]:
    return _act(model_id, "uninstall")


@router.get("/models/{model_id}/health", tags=["models"])
def model_health(model_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    model = db.get(ModelService, model_id)
    if model is None:
        raise HTTPException(404, "模型不存在")
    return {"model_id": model_id, **manager.health(model)}


# --------------------------------------------------------------------------- #
# 音色
# --------------------------------------------------------------------------- #
@router.get("/voices", tags=["voices"])
def list_voices(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(VoiceProfile).order_by(VoiceProfile.created_at)).all()
    return {"items": [_voice_out(v) for v in rows]}


@router.post("/voices", status_code=201, tags=["voices"])
def create_voice(body: VoiceCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    voice = VoiceProfile(
        name=body.name, kind="custom", pitch=body.pitch, speed=body.speed, timbre=body.timbre, note=body.note
    )
    db.add(voice)
    db.commit()
    db.refresh(voice)
    return _voice_out(voice)


@router.post("/voices/clone", status_code=201, tags=["voices"])
def clone_voice(body: VoiceCloneRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """上传参考音频 → 分析音高/音色 → 生成可用的克隆音色。"""
    settings = get_settings()
    voice_id = new_id("voi")
    reference = settings.upload_dir / f"{voice_id}.wav"

    if body.audio_b64:
        try:
            payload = base64.b64decode(body.audio_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(422, f"audio_b64 不是合法 base64：{exc}") from exc
        reference.write_bytes(payload)
    else:
        from app.media.tts import make_reference_sample

        make_reference_sample(reference, seconds=4.0)

    try:
        analysis = _analyze_reference(reference)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"参考音频无法解析（需要 WAV）：{exc}") from exc

    voice = VoiceProfile(
        id=voice_id,
        name=body.name,
        kind="cloned",
        pitch=analysis["pitch"],
        speed=1.0,
        timbre=analysis["timbre"],
        reference_path=str(reference),
        reference_duration=analysis["duration"],
        note=body.note or f"从参考音频分析：{analysis['duration']:.1f}s，估算基频 {analysis['f0']:.0f}Hz",
    )
    db.add(voice)
    db.commit()
    db.refresh(voice)
    return {**_voice_out(voice), "analysis": analysis}


def _analyze_reference(path: Path) -> dict[str, Any]:
    """用标准库读 WAV，估算基频（过零率）与音色倾向。"""
    with wave.open(str(path), "rb") as fh:
        frames = fh.readframes(min(fh.getnframes(), fh.getframerate() * 3))
        rate = fh.getframerate()
        width = fh.getsampwidth()
        channels = fh.getnchannels()
        total = fh.getnframes()
    if width != 2:
        raise ValueError("仅支持 16-bit PCM WAV")
    count = len(frames) // (2 * channels)
    samples = struct.unpack("<" + "h" * count, frames[: count * 2])
    if not samples:
        raise ValueError("音频为空")

    crossings = sum(1 for i in range(1, len(samples)) if (samples[i - 1] < 0) != (samples[i] < 0))
    duration = total / float(rate or 1)
    zcr = crossings / max(1, len(samples) - 1)
    f0 = max(60.0, min(400.0, zcr * rate / 2))
    timbre = "bright" if zcr > 0.12 else "deep" if zcr < 0.05 else "warm"
    return {
        "duration": round(duration, 3),
        "f0": round(f0, 1),
        "zcr": round(zcr, 4),
        "pitch": round(max(0.5, min(2.0, f0 / 165.0)), 3),
        "timbre": timbre,
        "sample_rate": rate,
    }


@router.delete("/voices/{voice_id}", status_code=204, tags=["voices"])
def delete_voice(voice_id: str, db: Session = Depends(get_db)) -> None:
    voice = db.get(VoiceProfile, voice_id)
    if voice is None:
        raise HTTPException(404, "音色不存在")
    if voice.kind == "preset":
        raise HTTPException(409, "预设音色不能删除")
    reference = Path(voice.reference_path) if voice.reference_path else None
    db.delete(voice)
    db.commit()
    # 克隆音色的参考音频跟着删掉，避免 uploads/ 越积越多
    if reference is not None:
        reference.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# 生产任务
# --------------------------------------------------------------------------- #
@router.post("/produce", status_code=202, tags=["jobs"])
def produce(body: ProduceRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    model = db.get(ModelService, body.model_id) if body.model_id else None
    if model is None:
        model = db.scalars(select(ModelService).where(ModelService.key == "local-engine")).first()
    if model is None:
        raise HTTPException(500, "内置引擎缺失，请重新初始化数据库")

    if not model.builtin:
        if model.status != "running":
            raise HTTPException(409, f"{model.name} 未运行，请先安装并启动")
        import os

        env_key = "AIGC_" + model.key.upper().replace("-", "_") + "_URL"
        if not os.getenv(env_key):
            raise HTTPException(
                409,
                f"{model.name} 只完成了服务管理：推理需要设置 {env_key} 指向真实模型服务，"
                "或改用内置引擎（local-engine）",
            )

    voice = db.get(VoiceProfile, body.voice_id) if body.voice_id else None
    if voice is None:
        voice = db.scalars(select(VoiceProfile).order_by(VoiceProfile.created_at)).first()
    if voice is None:
        raise HTTPException(409, "没有可用音色，请先初始化预设音色")

    job = Job(
        kind="produce",
        title=body.title,
        script=body.script,
        status="queued",
        model_id=model.id,
        voice_id=voice.id,
        input={
            "script": body.script,
            "title": body.title,
            "style": body.style,
            "width": body.width,
            "height": body.height,
            "fps": body.fps,
            "voice": _voice_out(voice),
            "model_key": model.key,
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    runner.submit(job.id)
    return {"job_id": job.id, "status": job.status, "poll": f"/api/jobs/{job.id}"}


@router.post("/demo/run", status_code=202, tags=["jobs"])
def demo_run(db: Session = Depends(get_db)) -> dict[str, Any]:
    """一键示例：用内置脚本 + 默认音色跑一条完整任务。"""
    script = DEMO_SCRIPTS[0]
    first_voice = db.scalars(select(VoiceProfile).order_by(VoiceProfile.created_at)).first()
    body = ProduceRequest(
        script=script["text"],
        title=script["title"],
        voice_id=first_voice.id if first_voice else None,
        style="studio",
    )
    return produce(body, db)


@router.get("/jobs", tags=["jobs"])
def list_jobs(
    status: str | None = None,
    limit: int = Query(30, le=200),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(Job).order_by(desc(Job.created_at)).limit(limit)
    if status:
        stmt = stmt.where(Job.status == status)
    rows = db.scalars(stmt).all()
    return {"items": [_job_out(j) for j in rows]}


@router.get("/jobs/{job_id}", tags=["jobs"])
def get_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return _job_out(job, detail=True)


@router.post("/jobs/{job_id}/cancel", tags=["jobs"])
def cancel_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    if db.get(Job, job_id) is None:
        raise HTTPException(404, "任务不存在")
    result = runner.cancel(job_id)
    if not result.get("ok"):
        raise HTTPException(409, result.get("reason", "取消失败"))
    return {"ok": True, "status": result["status"]}


@router.post("/jobs/{job_id}/retry", status_code=202, tags=["jobs"])
def retry_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    if db.get(Job, job_id) is None:
        raise HTTPException(404, "任务不存在")
    new_job_id = runner.retry(job_id)
    return {"job_id": new_job_id, "status": "queued", "poll": f"/api/jobs/{new_job_id}"}


@router.delete("/jobs/{job_id}", status_code=204, tags=["jobs"])
def delete_job(job_id: str, db: Session = Depends(get_db)) -> None:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    if job.status in {"running", "queued"}:
        raise HTTPException(409, "任务未结束，先取消再删除")
    cleanup_job_files(job_id)
    db.delete(job)
    db.commit()


# --------------------------------------------------------------------------- #
# 产物
# --------------------------------------------------------------------------- #
@router.get("/assets", tags=["assets"])
def list_assets(
    job_id: str | None = None,
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(Asset).order_by(desc(Asset.created_at)).limit(limit)
    if job_id:
        stmt = stmt.where(Asset.job_id == job_id)
    rows = db.scalars(stmt).all()
    return {"items": [_asset_out(a) for a in rows]}


def _asset_or_404(asset_id: str, db: Session) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, "产物不存在")
    settings = get_settings()
    path = Path(asset.path).resolve()
    # 用 is_relative_to 而不是字符串前缀：避免 /data-evil 这类同前缀目录绕过校验
    if not path.is_relative_to(settings.data_dir.resolve()):
        raise HTTPException(403, "非法的产物路径")
    if not path.exists():
        raise HTTPException(410, "产物文件已被清理")
    return asset


@router.get("/assets/{asset_id}", tags=["assets"])
def download_asset(asset_id: str, inline: int = 0, db: Session = Depends(get_db)) -> FileResponse:
    asset = _asset_or_404(asset_id, db)
    return FileResponse(
        asset.path,
        media_type=asset.mime,
        filename=asset.filename,
        content_disposition_type="inline" if inline else "attachment",
    )


# --------------------------------------------------------------------------- #
# 工具箱（真实调用 ffmpeg）
# --------------------------------------------------------------------------- #
TOOLS = {
    "probe": "读取媒体信息（时长/编码/分辨率）",
    "extract_audio": "从视频中提取音轨（mp3）",
    "thumbnail": "截取一帧作为封面图",
    "change_speed": "调整音频倍速（atempo）",
}


@router.get("/tools", tags=["tools"])
def list_tools() -> dict[str, Any]:
    return {"items": [{"name": k, "description": v} for k, v in TOOLS.items()]}


@router.post("/tools/{tool_name}", tags=["tools"])
def run_tool(tool_name: str, body: ToolRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    if tool_name not in TOOLS:
        raise HTTPException(404, f"未知工具：{tool_name}")
    asset = _asset_or_404(body.asset_id, db)
    source = Path(asset.path)
    job_dir = source.parent

    try:
        if tool_name == "probe":
            from app.media.avatar import probe

            return {"tool": tool_name, "result": probe(source), "asset": None}

        if tool_name == "extract_audio":
            from app.media.avatar import extract_audio

            out = job_dir / f"extracted_{asset.id}.mp3"
            result = extract_audio(source, out, fmt="mp3")
            kind, mime = "audio_extracted", "audio/mpeg"

        elif tool_name == "thumbnail":
            from app.media.avatar import thumbnail

            at = float(body.args.get("at", 1.0))
            out = job_dir / f"thumb_{asset.id}.jpg"
            result = thumbnail(source, out, at=at)
            kind, mime = "thumbnail", "image/jpeg"

        elif tool_name == "change_speed":
            from app.media.avatar import change_speed

            factor = float(body.args.get("factor", 1.25))
            if not 0.5 <= factor <= 2.0:
                raise HTTPException(422, "factor 必须在 0.5–2.0 之间")
            out = job_dir / f"speed_{factor}_{asset.id}.mp3"
            result = change_speed(source, out, factor=factor)
            kind, mime = "audio_speed", "audio/mpeg"

        else:
            raise HTTPException(404, f"未知工具：{tool_name}")

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"工具执行失败：{exc}") from exc

    derived = Asset(
        id=new_id("ast"),
        job_id=asset.job_id,
        kind=kind,
        filename=out.name,
        path=str(out),
        mime=mime,
        size_bytes=out.stat().st_size,
        duration=result.get("duration"),
    )
    db.add(derived)
    db.commit()
    db.refresh(derived)
    return {"tool": tool_name, "result": result, "asset": _asset_out(derived)}


# --------------------------------------------------------------------------- #
# 指标
# --------------------------------------------------------------------------- #
@router.get("/metrics", tags=["metrics"])
def metrics(db: Session = Depends(get_db)) -> dict[str, Any]:
    jobs = db.scalars(select(Job)).all()
    assets = db.scalars(select(Asset)).all()
    models = db.scalars(select(ModelService)).all()
    done = [j for j in jobs if j.status == "succeeded"]
    failed = [j for j in jobs if j.status == "failed"]
    durations = [j.duration_ms for j in done if j.duration_ms]
    return {
        "jobs": {
            "total": len(jobs),
            "succeeded": len(done),
            "failed": len(failed),
            "cancelled": sum(1 for j in jobs if j.status == "cancelled"),
            "running": sum(1 for j in jobs if j.status == "running"),
            "queued": sum(1 for j in jobs if j.status == "queued"),
            "success_rate": round(len(done) / len(jobs), 3) if jobs else None,
            "avg_duration_ms": int(sum(durations) / len(durations)) if durations else None,
        },
        "assets": {
            "count": len(assets),
            "total_bytes": sum(a.size_bytes for a in assets),
            "video_count": sum(1 for a in assets if a.kind == "video"),
            "audio_seconds": round(sum(a.duration or 0 for a in assets if a.kind == "audio"), 1),
        },
        "models": {
            "total": len(models),
            "installed": sum(1 for m in models if m.status in {"installed", "running", "stopped"}),
            "running": sum(1 for m in models if m.status == "running"),
            "disk_mb": sum(m.size_mb for m in models if m.status in {"installed", "running", "stopped"}),
        },
        "recent": [_job_out(j) for j in sorted(jobs, key=lambda x: x.created_at, reverse=True)[:5]],
    }
