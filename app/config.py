"""全局配置：环境变量优先，默认值保证零配置可跑。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    app_name: str = "AIGC Avatar Studio"
    version: str = "0.5.0"

    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./data/studio.db"))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("AIGC_DATA_DIR", str(PROJECT_ROOT / "data"))))

    media_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "media")
    model_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "models")
    upload_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "uploads")

    ffmpeg: str = field(default_factory=lambda: os.getenv("FFMPEG_BIN", "ffmpeg"))
    ffprobe: str = field(default_factory=lambda: os.getenv("FFPROBE_BIN", "ffprobe"))

    sample_rate: int = field(default_factory=lambda: _int("AIGC_SAMPLE_RATE", 24000))
    default_width: int = field(default_factory=lambda: _int("AIGC_WIDTH", 720))
    default_height: int = field(default_factory=lambda: _int("AIGC_HEIGHT", 1280))
    default_fps: int = field(default_factory=lambda: _int("AIGC_FPS", 2))

    max_workers: int = field(default_factory=lambda: _int("AIGC_MAX_WORKERS", 1))
    download_step_seconds: float = field(default_factory=lambda: _float("AIGC_DOWNLOAD_STEP", 0.08))
    api_prefix: str = "/api"

    @property
    def sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    for path in (settings.data_dir, settings.media_dir, settings.model_dir, settings.upload_dir):
        path.mkdir(parents=True, exist_ok=True)
    return settings
