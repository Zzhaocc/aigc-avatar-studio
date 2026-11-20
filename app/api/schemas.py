"""API 请求模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Style = Literal["studio", "news", "warm"]


class ProduceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script: str = Field(min_length=1, max_length=4000, description="口播文案")
    title: str = Field(default="未命名作品", max_length=120)
    voice_id: str | None = None
    model_id: str | None = Field(default=None, description="模型服务 id；缺省用内置引擎")
    style: Style = "studio"
    width: int = Field(default=720, ge=240, le=1920)
    height: int = Field(default=1280, ge=240, le=1920)
    fps: int = Field(default=2, ge=1, le=12)


class VoiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    pitch: float = Field(default=1.0, ge=0.5, le=2.0)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    timbre: Literal["warm", "bright", "deep"] = "warm"
    note: str = ""


class VoiceCloneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    audio_b64: str | None = Field(default=None, description="参考音频（WAV）base64；缺省时用内置样本演示")
    note: str = Field(default="", max_length=200)


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    args: dict[str, Any] = Field(default_factory=dict)
