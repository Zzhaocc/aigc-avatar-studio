"""引擎适配器：内置离线引擎 + 远程模型服务模板。

LocalEngine 用 numpy 合成语音、用 Pillow + ffmpeg 渲染数字人视频，
所以**没有 GPU、没有模型权重、没有 API key 也能跑出真实可播放的文件**。

RemoteHTTPEngine 是给真实模型服务留的模板：CosyVoice / IndexTTS 这类服务
通常暴露 POST /tts（返回 wav）或 POST /infer，MuseTalk 暴露 POST /lip-sync。
把环境变量 AIGC_<MODEL_KEY>_URL 指向服务地址即可切换。
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from app.providers.base import ModelNotReady, ProviderError


class LocalEngine:
    """内置离线引擎：确定性、跨平台、无需外部依赖（除 ffmpeg）。"""

    name = "local-engine"

    def __init__(self) -> None:
        from app.media import avatar, tts  # 延迟导入，避免媒体依赖影响 API 启动

        self._tts = tts
        self._avatar = avatar

    # ---- TTS ----
    def synthesize(self, text: str, out_path: Path, *, pitch: float, speed: float, timbre: str) -> dict:
        from app.media.tts import VoiceSpec

        try:
            return self._tts.synthesize(text, out_path, VoiceSpec(pitch=pitch, speed=speed, timbre=timbre))
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"语音合成失败：{exc}") from exc

    def concat(self, paths: list[Path], out_path: Path) -> dict:
        try:
            return self._tts.concat_wavs(paths, out_path)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"音频拼接失败：{exc}") from exc

    # ---- 数字人 ----
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
    ) -> dict:
        try:
            return self._avatar.render_avatar_video(
                audio_path,
                segments,
                out_path,
                title=title,
                style=style,
                width=width,
                height=height,
                fps=fps,
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"数字人渲染失败：{exc}") from exc

    # ---- ASR ----
    def transcribe(self, audio_path: Path) -> dict:
        """离线引擎没有真实 ASR：返回占位结果，明确标注 degraded。"""
        return {
            "text": "",
            "segments": [],
            "engine": self.name,
            "degraded": True,
            "note": "内置引擎不含 ASR，请安装 FunASR 并配置 AIGC_FUNASR_PARA FORMER_URL",
        }


class RemoteHTTPEngine:
    """真实模型服务的 HTTP 适配器模板（未配置时不会实例化）。

    统一约定：
        POST {base}/tts      {"text","pitch","speed","timbre"} → audio/wav
        POST {base}/lip-sync {"audio","segments","style"}      → video/mp4
    """

    def __init__(self, base_url: str, name: str = "remote") -> None:
        self.base_url = base_url.rstrip("/")
        self.name = name
        self.timeout = float(os.getenv("AIGC_REMOTE_TIMEOUT", "120"))

    def synthesize(self, text: str, out_path: Path, *, pitch: float, speed: float, timbre: str) -> dict:
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                resp = client.post(
                    f"{self.base_url}/tts",
                    json={"text": text, "pitch": pitch, "speed": speed, "timbre": timbre},
                )
                resp.raise_for_status()
                out_path.write_bytes(resp.content)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"远程 TTS 失败（{self.base_url}）：{exc}") from exc
        return {"path": str(out_path), "duration": None, "engine": self.name, "size_bytes": out_path.stat().st_size}

    def concat(self, paths: list[Path], out_path: Path) -> dict:
        raise ProviderError("远程引擎请在服务端完成拼接，或使用 LocalEngine.concat")

    def render(self, audio_path: Path, segments: list[dict], out_path: Path, **kwargs) -> dict:
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                with audio_path.open("rb") as fh:
                    resp = client.post(
                        f"{self.base_url}/lip-sync",
                        files={"audio": (audio_path.name, fh, "audio/wav")},
                        data={"style": kwargs.get("style", "studio"), "segments": str(len(segments))},
                    )
                resp.raise_for_status()
                out_path.write_bytes(resp.content)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"远程数字人渲染失败（{self.base_url}）：{exc}") from exc
        return {"path": str(out_path), "engine": self.name, "size_bytes": out_path.stat().st_size}


def engine_for(model_key: str) -> LocalEngine | RemoteHTTPEngine:
    """按模型 key 返回引擎：内置引擎直接可用，其余需显式配置后端地址。"""
    if model_key in {"local-engine", "", None}:
        return LocalEngine()

    env_key = "AIGC_" + model_key.upper().replace("-", "_") + "_URL"
    base_url = os.getenv(env_key)
    if not base_url:
        raise ModelNotReady(
            f"{model_key} 未配置推理后端：安装/启动只管理服务生命周期，"
            f"真正推理需要把 {env_key} 指向该模型服务的地址（或改用 local-engine）"
        )
    return RemoteHTTPEngine(base_url, name=model_key)
