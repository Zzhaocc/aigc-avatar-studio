"""持久化模型。

设计要点：
1. ModelService 是**有状态资源**（未安装→下载→已安装→启动→运行），
   和生产环境的模型服务一一对应，状态机由 Manager 统一维护；
2. Job / JobStep 分离，前端能显示"当前做到第几步、每步多久"；
3. Asset 指向磁盘真实文件，API 直接流式返回，不做 base64。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ModelService(Base):
    """一个可安装/启动的模型服务。"""

    __tablename__ = "model_services"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("mdl"))
    key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), index=True)  # tts|asr|avatar|llm
    version: Mapped[str] = mapped_column(String(40), default="1.0.0")
    size_mb: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="not_installed", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    path: Mapped[str] = mapped_column(String(400), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    installed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VoiceProfile(Base):
    """音色：预设音色 + 克隆音色（含参考音频特征）。"""

    __tablename__ = "voice_profiles"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("voi"))
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20), default="preset")  # preset|cloned
    pitch: Mapped[float] = mapped_column(Float, default=1.0)
    speed: Mapped[float] = mapped_column(Float, default=1.0)
    timbre: Mapped[str] = mapped_column(String(20), default="warm")
    reference_path: Mapped[str | None] = mapped_column(String(400), nullable=True)
    reference_duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    """一次内容生产任务（脚本 → 语音 → 字幕 → 数字人视频）。"""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("job"))
    kind: Mapped[str] = mapped_column(String(20), default="produce")
    title: Mapped[str] = mapped_column(String(200), default="")
    script: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    voice_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    steps: Mapped[list["JobStep"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobStep.seq"
    )
    assets: Mapped[list["Asset"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobStep(Base):
    """任务步骤：script → tts → subtitle → avatar → manifest。"""

    __tablename__ = "job_steps"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("stp"))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    detail: Mapped[str] = mapped_column(Text, default="")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    job: Mapped["Job"] = relationship(back_populates="steps")


class Asset(Base):
    """产物：音频 / 字幕 / 视频 / 清单。"""

    __tablename__ = "assets"
    __table_args__ = (Index("ix_asset_job_kind", "job_id", "kind"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ast"))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # audio|subtitle|video|manifest|thumbnail
    filename: Mapped[str] = mapped_column(String(200))
    path: Mapped[str] = mapped_column(String(500))
    mime: Mapped[str] = mapped_column(String(80), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    job: Mapped["Job"] = relationship(back_populates="assets")
