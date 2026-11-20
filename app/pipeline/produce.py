"""从脚本到成片：切句 → 逐句 TTS → 拼音频 → 字幕 → 数字人视频 → 清单。

每一步都向 Reporter 报进度，并检查取消标记；
这样前端能看到"正在合成第 3/8 句"，取消也能在 1 秒内生效。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import get_settings
from app.pipeline.script import split_script

# 与 app/media/tts.concat_wavs 的句间静音保持一致；字幕时间轴依赖它
SEGMENT_GAP = 0.15


class JobCancelled(RuntimeError):
    """用户取消。"""


def produce(
    job_id: str,
    payload: dict[str, Any],
    voice: dict[str, Any],
    reporter: Any,
) -> dict[str, Any]:
    settings = get_settings()
    work_dir = settings.media_dir / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    title = payload.get("title") or "未命名作品"
    style = payload.get("style") or "studio"
    width = int(payload.get("width") or settings.default_width)
    height = int(payload.get("height") or settings.default_height)
    fps = int(payload.get("fps") or settings.default_fps)

    from app.providers.adapters import engine_for

    engine = engine_for(payload.get("model_key") or "local-engine")

    # 1) 切句
    seq = reporter.begin("script", "切分脚本")
    segments_text = split_script(payload.get("script") or "")
    reporter.finish(seq, f"切成 {len(segments_text)} 段，最长 {max(len(s) for s in segments_text)} 字")
    reporter.progress(6)

    # 2) 逐句合成
    seq = reporter.begin("tts", "语音合成")
    wav_paths: list[Path] = []
    durations: list[float] = []
    for index, text in enumerate(segments_text, start=1):
        reporter.check_cancel()
        wav_path = work_dir / f"seg_{index:03d}.wav"
        result = engine.synthesize(
            text,
            wav_path,
            pitch=float(voice.get("pitch", 1.0)),
            speed=float(voice.get("speed", 1.0)),
            timbre=str(voice.get("timbre", "warm")),
        )
        wav_paths.append(wav_path)
        duration = float(result.get("duration") or 0.0)
        if duration <= 0:
            duration = _wav_duration(wav_path)
        durations.append(duration)
        reporter.progress(6 + int(44 * index / len(segments_text)))
        reporter.detail(f"已合成 {index}/{len(segments_text)} 句 · {text[:12]}…")

    audio_path = work_dir / "voice.wav"
    merged = engine.concat(wav_paths, audio_path)
    reporter.finish(seq, f"音频 {merged['duration']:.1f}s，{len(wav_paths)} 段")
    reporter.progress(52)

    # 3) 字幕（时间轴 = 句长 + 句间静音）
    seq = reporter.begin("subtitle", "生成字幕")
    from app.media.subtitles import build_srt

    timeline: list[dict[str, Any]] = []
    cursor = 0.0
    for index, (text, duration) in enumerate(zip(segments_text, durations)):
        if index > 0:
            cursor += SEGMENT_GAP
        timeline.append({"text": text, "start": round(cursor, 3), "end": round(cursor + duration, 3), "duration": duration})
        cursor += duration
    srt = build_srt([{"text": item["text"], "duration": item["duration"]} for item in timeline], work_dir / "voice.srt")
    srt["segments"] = timeline
    reporter.finish(seq, f"{srt['count']} 条字幕，总时长 {cursor:.1f}s")
    reporter.progress(62)

    # 4) 数字人视频
    seq = reporter.begin("avatar", "数字人渲染")
    reporter.check_cancel()
    video_path = work_dir / "avatar.mp4"
    video = engine.render(
        audio_path,
        [{"start": item["start"], "end": item["end"], "text": item["text"]} for item in timeline],
        video_path,
        title=title,
        style=style,
        width=width,
        height=height,
        fps=fps,
    )
    reporter.finish(seq, f"{video.get('duration', 0):.1f}s · {width}×{height} · {video.get('frames', 0)} 帧")
    reporter.progress(92)

    # 5) 缩略图 + 清单
    seq = reporter.begin("manifest", "生成清单")
    thumb_path = work_dir / "cover.jpg"
    thumbnail: dict[str, Any] = {}
    try:
        from app.media.avatar import thumbnail as make_thumbnail

        thumbnail = make_thumbnail(video_path, thumb_path, at=min(1.0, max(0.0, cursor / 3)))
    except Exception as exc:  # noqa: BLE001 —— 缩略图失败不影响主产物
        reporter.detail(f"缩略图生成失败（忽略）：{exc}")

    manifest = {
        "job_id": job_id,
        "title": title,
        "engine": getattr(engine, "name", "unknown"),
        "voice": voice,
        "style": style,
        "resolution": [width, height],
        "fps": fps,
        "duration": round(cursor, 3),
        "segments": timeline,
        "files": {
            "audio": audio_path.name,
            "subtitle": "voice.srt",
            "video": video_path.name,
            "cover": thumb_path.name if thumbnail else None,
        },
    }
    manifest_path = work_dir / "manifest.json"
    import json

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    reporter.finish(seq, "清单已生成")
    reporter.progress(100)

    assets: list[dict[str, Any]] = [
        {
            "kind": "audio",
            "filename": audio_path.name,
            "path": audio_path,
            "mime": "audio/wav",
            "size_bytes": audio_path.stat().st_size,
            "duration": merged["duration"],
        },
        {
            "kind": "subtitle",
            "filename": out_name(srt["path"]),
            "path": Path(srt["path"]),
            "mime": "text/plain; charset=utf-8",
            "size_bytes": Path(srt["path"]).stat().st_size,
            "duration": None,
        },
        {
            "kind": "video",
            "filename": video_path.name,
            "path": video_path,
            "mime": "video/mp4",
            "size_bytes": video_path.stat().st_size,
            "duration": video.get("duration") or cursor,
        },
        {
            "kind": "manifest",
            "filename": manifest_path.name,
            "path": manifest_path,
            "mime": "application/json",
            "size_bytes": manifest_path.stat().st_size,
            "duration": None,
        },
    ]
    if thumbnail:
        assets.append(
            {
                "kind": "cover",
                "filename": thumb_path.name,
                "path": thumb_path,
                "mime": "image/jpeg",
                "size_bytes": thumb_path.stat().st_size,
                "duration": None,
            }
        )

    return {
        "duration": round(cursor, 3),
        "segment_count": len(segments_text),
        "engine": getattr(engine, "name", "unknown"),
        "resolution": [width, height],
        "style": style,
        "voice": voice,
        "assets": assets,
    }


def out_name(path: str | Path) -> str:
    return Path(path).name


def _wav_duration(path: Path) -> float:
    """标准库读取 WAV 时长，作为引擎未返回 duration 时的兜底。"""
    import wave

    with wave.open(str(path), "rb") as fh:
        return round(fh.getnframes() / float(fh.getframerate() or 1), 3)
