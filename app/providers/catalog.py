"""模型目录与预设音色。

对齐 AIGCPanel 的"AI 模型"页：把 CosyVoice / IndexTTS / MuseTalk / Wav2Lip 这类
本地模型当成**可安装的服务**管理。本仓库不下载真实权重，只维护元数据与生命周期；
真正推理由 app/providers/adapters.py 里的适配器负责（默认走离线引擎）。
"""

from __future__ import annotations

from typing import Any

MODEL_CATALOG: list[dict[str, Any]] = [
    {
        "key": "local-engine",
        "name": "Local Engine（内置离线引擎）",
        "kind": "all",
        "version": "0.5.0",
        "size_mb": 0,
        "builtin": True,
        "description": "内置的确定性语音 + 数字人渲染引擎：无需 GPU、无需下载权重，"
        "用于演示与 CI。真实部署时替换为下面的模型服务即可。",
        "tags": ["offline", "numpy", "ffmpeg"],
    },
    {
        "key": "cosyvoice-300m",
        "name": "CosyVoice-300M",
        "kind": "tts",
        "version": "1.2.0",
        "size_mb": 1280,
        "builtin": False,
        "description": "零样本语音克隆 TTS，3 秒参考音频即可复刻音色。",
        "tags": ["tts", "voice-clone", "zh"],
    },
    {
        "key": "index-tts",
        "name": "IndexTTS",
        "kind": "tts",
        "version": "1.0.3",
        "size_mb": 2450,
        "builtin": False,
        "description": "零样本 TTS，中文韵律与情感控制较好。",
        "tags": ["tts", "voice-clone", "zh"],
    },
    {
        "key": "fish-speech",
        "name": "FishSpeech",
        "kind": "tts",
        "version": "1.5.0",
        "size_mb": 1820,
        "builtin": False,
        "description": "多语种 TTS，支持 13 种语言与短参考音频克隆。",
        "tags": ["tts", "multilingual"],
    },
    {
        "key": "funasr-paraformer",
        "name": "FunASR Paraformer",
        "kind": "asr",
        "version": "2.0.4",
        "size_mb": 920,
        "builtin": False,
        "description": "中文语音识别，用于把上传音频转成脚本或校对字幕。",
        "tags": ["asr", "zh"],
    },
    {
        "key": "musetalk",
        "name": "MuseTalk",
        "kind": "avatar",
        "version": "1.5",
        "size_mb": 3680,
        "builtin": False,
        "description": "实时口型同步，用一张人物图 + 音频生成数字人视频。",
        "tags": ["avatar", "lip-sync", "realtime"],
    },
    {
        "key": "wav2lip",
        "name": "Wav2Lip",
        "kind": "avatar",
        "version": "1.0",
        "size_mb": 420,
        "builtin": False,
        "description": "经典唇形同步模型，轻量、对任意人脸泛化好。",
        "tags": ["avatar", "lip-sync", "light"],
    },
    {
        "key": "latentsync",
        "name": "LatentSync",
        "kind": "avatar",
        "version": "1.5",
        "size_mb": 5120,
        "builtin": False,
        "description": "高分辨率唇形同步，口型细节与牙齿更自然，显存要求高。",
        "tags": ["avatar", "lip-sync", "hq"],
    },
]

PRESET_VOICES: list[dict[str, Any]] = [
    {
        "key": "xiaoya",
        "name": "小雅 · 温暖女声",
        "pitch": 1.12,
        "speed": 1.0,
        "timbre": "warm",
        "note": "默认音色，适合口播与产品介绍",
    },
    {
        "key": "azhe",
        "name": "阿哲 · 沉稳男声",
        "pitch": 0.86,
        "speed": 0.96,
        "timbre": "deep",
        "note": "适合企业宣传片与新闻播报",
    },
    {
        "key": "luna",
        "name": "Luna · 清亮女声",
        "pitch": 1.24,
        "speed": 1.08,
        "timbre": "bright",
        "note": "适合短视频与带货口播",
    },
    {
        "key": "anchor",
        "name": "主播 · 标准播报",
        "pitch": 1.0,
        "speed": 1.02,
        "timbre": "warm",
        "note": "语速稳定，适合课程与资讯",
    },
]

STYLES: dict[str, dict[str, Any]] = {
    "studio": {"label": "深色演播室", "bg": ["#081022", "#183a56"], "accent": "#60cdff"},
    "news": {"label": "新闻红蓝", "bg": ["#0a1222", "#4a141c"], "accent": "#ff6252"},
    "warm": {"label": "暖色口播", "bg": ["#3a1e18", "#985838"], "accent": "#ffc478"},
}

DEMO_SCRIPTS: list[dict[str, Any]] = [
    {
        "title": "新品介绍 · 30 秒",
        "text": "大家好，欢迎来到本期产品速览。今天介绍的是我们的新款智能音箱，"
        "它支持全屋语音控制，内置六个麦克风阵列，即使在厨房也能清晰唤醒。"
        "续航方面，充满电可以连续播放十二小时。现在下单还赠送一年音乐会员。",
    },
    {
        "title": "知识口播 · 45 秒",
        "text": "什么是数字人？简单来说，就是用人工智能生成一个可以说话、有表情、"
        "能持续出镜的虚拟形象。它不需要真人反复录制，只要输入文字，"
        "就能自动生成语音、口型和字幕，大幅降低内容生产的成本。",
    },
]
