"""端到端演示：不需要起服务，直接跑引擎。

    python scripts/demo.py

流程：初始化 → 提交任务 → 实时打印步骤 → 展示产物 → 克隆音色 → 工具箱探测 → 指标
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.models import Asset, Job, ModelService, VoiceProfile  # noqa: E402
from app.pipeline.jobs import runner  # noqa: E402
from app.providers.catalog import DEMO_SCRIPTS  # noqa: E402
from app.providers.manager import manager  # noqa: E402
from app.seed import seed_all  # noqa: E402

LINE = "─" * 72


def section(title: str) -> None:
    print(f"\n{LINE}\n{title}\n{LINE}")


def main() -> int:
    init_db()
    seed_all()
    manager.reset_stale()
    runner.recover()

    with session_scope() as db:
        voice = db.scalars(select(VoiceProfile).order_by(VoiceProfile.created_at)).first()
        model = db.scalars(select(ModelService).where(ModelService.key == "local-engine")).first()
        models = db.scalars(select(ModelService)).all()
        voice_count = len(db.scalars(select(VoiceProfile)).all())
        print(f"内置引擎：{model.name}｜模型服务 {len(models)} 个｜音色 {voice_count} 个")

    script = DEMO_SCRIPTS[0]
    section("① 提交生产任务")
    print(f"标题：{script['title']}")
    print(f"文案：{script['text'][:48]}…")

    with session_scope() as db:
        job = Job(
            kind="produce",
            title=script["title"],
            script=script["text"],
            status="queued",
            model_id=model.id,
            voice_id=voice.id if voice else None,
            input={
                "script": script["text"],
                "title": script["title"],
                "style": "studio",
                "width": 540,
                "height": 960,
                "fps": 2,
                "model_key": "local-engine",
            },
        )
        db.add(job)
        db.flush()
        job_id = job.id
    print(f"job_id = {job_id}")

    started = time.time()
    runner.submit(job_id)
    seen: set[int] = set()
    status = "queued"
    while time.time() - started < 120:
        time.sleep(0.2)
        with session_scope() as db:
            current = db.get(Job, job_id)
            status, progress = current.status, current.progress
            for step in current.steps:
                if step.seq not in seen:
                    seen.add(step.seq)
                    print(f"  {step.seq}. {step.name} …")
            if status in {"succeeded", "failed", "cancelled"}:
                break
    print(f"状态：{status}｜进度：{progress}%｜耗时：{time.time() - started:.1f}s")

    if status != "succeeded":
        with session_scope() as db:
            print("错误：", db.get(Job, job_id).error)
        return 1

    section("② 产物")
    with session_scope() as db:
        job = db.get(Job, job_id)
        print(f"成片时长：{job.output['duration']}s｜分句：{job.output['segment_count']}｜引擎：{job.output['engine']}")
        print(f"分辨率：{job.output['resolution'][0]}×{job.output['resolution'][1]}｜风格：{job.output['style']}")
        assets = db.scalars(select(Asset).where(Asset.job_id == job_id)).all()
        for asset in assets:
            print(
                f"  {asset.kind:<9} {asset.filename:<16} {asset.size_bytes:>9,} B  {asset.duration or ''}  {asset.path}"
            )
        print("\n步骤耗时：")
        for step in job.steps:
            print(f"  {step.seq}. {step.name:<24} {step.latency_ms:>6} ms  {step.detail}")
        video = next(a for a in assets if a.kind == "video")

    section("③ 克隆音色（用内置样本做参考音频）")
    from app.api.routes import _analyze_reference
    from app.media.tts import make_reference_sample

    ref = Path("data/uploads/demo_reference.wav")
    ref.parent.mkdir(parents=True, exist_ok=True)
    make_reference_sample(ref, seconds=4.0)
    analysis = _analyze_reference(ref)
    print(f"参考音频 4.0s｜估算基频 {analysis['f0']}Hz｜音色 {analysis['timbre']}｜pitch {analysis['pitch']}")
    with session_scope() as db:
        cloned = VoiceProfile(
            name="演示克隆音色",
            kind="cloned",
            pitch=analysis["pitch"],
            speed=1.0,
            timbre=analysis["timbre"],
            reference_path=str(ref),
            reference_duration=analysis["duration"],
            note="scripts/demo.py 生成",
        )
        db.add(cloned)
    print("已写入音色库（可在控制台「音色」页看到）")

    section("④ 工具箱：探测成片 + 截封面")
    from app.media.avatar import probe, thumbnail

    info = probe(Path(video.path))
    print(f"probe：{info['duration']}s｜{info['bit_rate']} bps｜{[(s['type'], s['codec']) for s in info['streams']]}")
    cover = Path(video.path).parent / "demo_cover.jpg"
    thumb = thumbnail(Path(video.path), cover, at=1.0)
    print(f"封面：{thumb['width']}×{thumb['height']}｜{thumb['size_bytes']:,} B")

    section("⑤ 指标")
    from app.api.routes import metrics as metrics_fn

    with session_scope() as db:
        data = metrics_fn(db)
    jobs_meta = data["jobs"]
    models_meta = data["models"]
    print(f"任务：{jobs_meta['total']} 个，成功 {jobs_meta['succeeded']}，平均 {jobs_meta['avg_duration_ms']} ms")
    print(f"产物：{data['assets']['count']} 个，共 {data['assets']['total_bytes'] / 1048576:.2f} MB")
    print(f"模型：已安装 {models_meta['installed']}，运行 {models_meta['running']}，磁盘 {models_meta['disk_mb']} MB")

    print(f"\n成片路径：{Path(video.path).resolve()}")
    print("启动控制台：python -m uvicorn app.main:app --port 8000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
