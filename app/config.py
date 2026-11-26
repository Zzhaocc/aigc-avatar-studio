"""全局配置：环境变量优先，默认值保证零配置可跑。

约定：
- 只读：启动时解析一次（``get_settings`` 带缓存），运行期不热更新；
- 数据目录单一来源：``AIGC_DATA_DIR`` 决定库文件、产物、模型目录、上传目录的位置，
  换部署形态只需要挂一个卷；
- 可审计：``describe()`` 只导出非敏感字段，用于启动日志与 /api/health。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app import __version__

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _items(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    """进程级配置快照。"""

    app_name: str = "AIGC Avatar Studio"
    version: str = __version__
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    debug: bool = field(default_factory=lambda: _bool("DEBUG", False))

    # ---- 存储：一个根目录派生全部子目录 ----
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./data/studio.db"))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("AIGC_DATA_DIR", str(PROJECT_ROOT / "data"))))

    # ---- 媒体 ----
    ffmpeg: str = field(default_factory=lambda: os.getenv("FFMPEG_BIN", "ffmpeg"))
    ffprobe: str = field(default_factory=lambda: os.getenv("FFPROBE_BIN", "ffprobe"))
    ffmpeg_timeout: float = field(default_factory=lambda: _float("AIGC_FFMPEG_TIMEOUT", 300.0))

    sample_rate: int = field(default_factory=lambda: _int("AIGC_SAMPLE_RATE", 24000))
    default_width: int = field(default_factory=lambda: _int("AIGC_WIDTH", 720))
    default_height: int = field(default_factory=lambda: _int("AIGC_HEIGHT", 1280))
    default_fps: int = field(default_factory=lambda: _int("AIGC_FPS", 2))

    # ---- 任务队列 ----
    max_workers: int = field(default_factory=lambda: _int("AIGC_MAX_WORKERS", 1))
    download_step_seconds: float = field(default_factory=lambda: _float("AIGC_DOWNLOAD_STEP", 0.08))

    # ---- 真实模型接入 ----
    # 单个远程推理请求的超时（秒）；AIGC_<MODEL_KEY>_URL 由 adapters 逐个读取
    remote_timeout: float = field(default_factory=lambda: _float("AIGC_REMOTE_TIMEOUT", 120.0))

    # ---- 接入与可观测 ----
    cors_origins: tuple[str, ...] = field(default_factory=lambda: _items("CORS_ORIGINS", ("*",)))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    log_format: str = field(default_factory=lambda: os.getenv("LOG_FORMAT", "text").lower())
    request_id_header: str = field(default_factory=lambda: os.getenv("REQUEST_ID_HEADER", "X-Request-ID"))
    docs_enabled: bool = field(default_factory=lambda: _bool("DOCS_ENABLED", True))

    # ---- 派生子目录（单一根目录，容器里只挂一个卷）----
    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def model_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def describe(self) -> dict[str, object]:
        """对外暴露的运行态摘要（不含敏感信息）。"""
        return {
            "app": self.app_name,
            "version": self.version,
            "environment": self.environment,
            "database": "sqlite" if self.sqlite else "external",
            "data_dir": str(self.data_dir),
            "sample_rate": self.sample_rate,
            "workers": max(1, self.max_workers),
            "log_level": self.log_level,
            "docs_enabled": self.docs_enabled,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    for path in (settings.data_dir, settings.media_dir, settings.model_dir, settings.upload_dir):
        path.mkdir(parents=True, exist_ok=True)
    return settings


def reset_settings_cache() -> None:
    """仅供测试：环境变量变更后重新解析配置。"""
    get_settings.cache_clear()
