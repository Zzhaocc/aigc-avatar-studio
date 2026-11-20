"""确定性离线媒体引擎。

包含三个子模块：

- :mod:`app.media.tts`       —— 纯 numpy 的参数化语音合成与 WAV 拼接；
- :mod:`app.media.subtitles` —— SRT 字幕的生成与解析；
- :mod:`app.media.avatar`    —— Pillow 逐帧绘制 + ffmpeg 编码的数字人视频。

所有函数都是确定性的纯计算（除显式写文件与调用 ffmpeg 外），
不依赖网络、模型权重或随机全局状态。
"""

from __future__ import annotations

from .avatar import (
    change_speed,
    compress_video,
    extract_audio,
    probe,
    render_avatar_video,
    thumbnail,
)
from .subtitles import build_srt, format_ts, parse_srt
from .tts import VoiceSpec, concat_wavs, make_reference_sample, synthesize

__all__ = [
    "VoiceSpec",
    "build_srt",
    "change_speed",
    "compress_video",
    "concat_wavs",
    "extract_audio",
    "format_ts",
    "make_reference_sample",
    "parse_srt",
    "probe",
    "render_avatar_video",
    "synthesize",
    "thumbnail",
]
