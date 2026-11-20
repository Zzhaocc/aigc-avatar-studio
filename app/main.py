"""应用入口：建表 → 灌种子 → 恢复中断任务 → 暴露 API 与控制台。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import get_settings
from app.db import init_db
from app.pipeline.jobs import runner
from app.providers.manager import manager
from app.seed import seed_all

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    seed_all()
    manager.reset_stale()
    recovered = runner.recover()
    app.state.recovered_jobs = recovered
    app.state.settings = settings
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AIGC Avatar Studio",
        description=(
            "服务端 AI 数字人内容生产平台：模型服务生命周期管理 + 语音合成 + 字幕 + "
            "数字人视频渲染 + 任务队列 + FFmpeg 工具箱。内置离线引擎，无需 GPU 即可完整演示。"
        ),
        version=settings.version,
        lifespan=lifespan,
    )
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(router)

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app


app = create_app()
