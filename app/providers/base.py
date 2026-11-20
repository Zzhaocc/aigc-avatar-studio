"""推理引擎的抽象边界。

平台只依赖这几个 Protocol：
- TTS：文本 → WAV
- Avatar：音频 + 时间轴 → MP4
- ASR：音频 → 文本

内置 LocalEngine 走离线实现；接真实模型（CosyVoice / MuseTalk / …）时
实现同样的三个方法即可，上层 pipeline、任务队列、API 都不用改。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ProviderError(RuntimeError):
    """推理失败。"""


class ModelNotReady(ProviderError):
    """模型未安装 / 未启动 / 未配置后端。"""


class TTSEngine(Protocol):
    name: str

    def synthesize(self, text: str, out_path: Path, *, pitch: float, speed: float, timbre: str) -> dict: ...

    def concat(self, paths: list[Path], out_path: Path) -> dict: ...


class AvatarEngine(Protocol):
    name: str

    def render(
        self,
        audio_path: Path,
        segments: list[dict],
        out_path: Path,
        *,
        title: str,
        style: str,
        width: int,
        height: int,
        fps: int,
    ) -> dict: ...


class ASREngine(Protocol):
    name: str

    def transcribe(self, audio_path: Path) -> dict: ...
