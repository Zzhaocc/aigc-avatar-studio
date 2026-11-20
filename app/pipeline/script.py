"""脚本切分：把一段文案切成适合逐句合成的片段。

为什么要切：TTS 一次合成整段，出错要重来、字幕对不齐、进度不可见；
切成 ≤28 字的片段后可以逐句合成、逐句出字幕、逐句报进度。
"""

from __future__ import annotations

import re

MAX_SEGMENT_CHARS = 28
SENTENCE_END = "。！？!?；;\n"
SOFT_BREAK = "，,、：: "


def split_script(text: str, max_chars: int = MAX_SEGMENT_CHARS) -> list[str]:
    """按句末标点切句，超长句再按逗号/空格切，最后按长度硬切。"""
    raw = re.sub(r"[ \t\u3000]+", " ", (text or "").strip())
    if not raw:
        raise ValueError("脚本为空：至少输入一句话")

    sentences: list[str] = []
    buf = ""
    for ch in raw:
        buf += ch
        if ch in SENTENCE_END:
            if buf.strip(" " + SENTENCE_END):
                sentences.append(buf.strip())
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())

    segments: list[str] = []
    for sentence in sentences:
        if len(sentence) <= max_chars:
            segments.append(sentence)
            continue
        current = ""
        for part in re.split(r"(?<=[，,、：:])", sentence):
            if not part:
                continue
            if len(current) + len(part) <= max_chars:
                current += part
                continue
            if current:
                segments.append(current)
            current = part
        if current:
            segments.append(current)

    # 仍然超长的（没有标点的英文/数字串）按长度硬切
    final: list[str] = []
    for seg in segments:
        while len(seg) > max_chars:
            final.append(seg[:max_chars])
            seg = seg[max_chars:]
        if seg.strip():
            final.append(seg.strip())
    return final


def estimate_duration(text: str, speed: float = 1.0) -> float:
    """粗估时长，用于前端预览与任务排队时间估算（真实时长以合成结果为准）。"""
    units = len(text)
    pauses = sum(1 for ch in text if ch in "，。！？,.!?;；:：")
    return round((units * 0.16 + pauses * 0.12) / max(0.5, speed), 2)
