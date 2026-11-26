"""离线数字人视频渲染与 ffmpeg 工具函数。

渲染不依赖任何模型：用 Pillow 逐帧画竖向渐变背景、头像圆盘（头/肩/
眼睛）、随音频包络跳动的嘴部均衡条、标题、字幕与水印，再用 ffmpeg
把 PNG 序列和 WAV 合成为 H.264 + AAC 的 MP4。

设计约束：

- 音频时长以 ``wave`` 读取的 WAV 为准，不依赖 ffprobe；
- 每帧 PNG 放在输出文件同级的临时目录，``finally`` 中清理；
- ffmpeg / ffprobe 的可执行路径与超时来自配置（``FFMPEG_BIN`` / ``FFPROBE_BIN`` /
  ``AIGC_FFMPEG_TIMEOUT``），换镜像、换版本不需要改代码；
- ffmpeg 失败统一转成 ``RuntimeError``，保留最后 800 字符 stderr；
- 找不到中文字体时退化为 Pillow 默认字体，绝不因字体缺失而崩溃。
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

from app.config import get_settings


def ffmpeg_bin() -> str:
    """当前生效的 ffmpeg 路径（每次读取，便于测试与容器覆盖）。"""
    return get_settings().ffmpeg


def ffprobe_bin() -> str:
    """当前生效的 ffprobe 路径。"""
    return get_settings().ffprobe


# 字体探测顺序：Windows 微软雅黑 → 黑体 → Linux Noto CJK → DejaVu → 默认。
_FONT_CANDIDATES: tuple[str, ...] = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

# 三套配色的竖向渐变；颜色均为 RGB。
_STYLE_PRESETS: dict[str, dict[str, tuple[int, int, int]]] = {
    "studio": {
        "top": (8, 16, 34),
        "bottom": (24, 58, 86),
        "accent": (96, 205, 255),
        "disc": (16, 38, 62),
        "body": (36, 78, 112),
        "skin": (247, 208, 182),
        "hair": (30, 26, 32),
        "title": (244, 250, 255),
        "subtitle": (255, 255, 255),
        "watermark": (174, 204, 224),
    },
    "news": {
        "top": (10, 18, 34),
        "bottom": (74, 20, 28),
        "accent": (255, 98, 82),
        "disc": (28, 30, 46),
        "body": (66, 52, 68),
        "skin": (245, 206, 180),
        "hair": (28, 24, 30),
        "title": (255, 246, 242),
        "subtitle": (255, 255, 255),
        "watermark": (232, 190, 184),
    },
    "warm": {
        "top": (58, 30, 24),
        "bottom": (152, 88, 56),
        "accent": (255, 196, 120),
        "disc": (76, 44, 38),
        "body": (124, 74, 58),
        "skin": (250, 210, 180),
        "hair": (44, 30, 26),
        "title": (255, 248, 238),
        "subtitle": (255, 255, 255),
        "watermark": (244, 210, 180),
    },
}


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _find_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """按固定顺序寻找字体；全部失败则返回 Pillow 默认字体。"""
    size = max(8, int(size))
    for candidate in _FONT_CANDIDATES:
        try:
            if Path(candidate).exists():
                return ImageFont.truetype(candidate, size=size)
        except (OSError, ValueError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 不支持 size 参数
        return ImageFont.load_default()


def _ascii_fallback(text: str) -> str:
    return "".join(char if ord(char) < 128 else "?" for char in text)


def _draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, int, int, int] | tuple[int, int, int],
    *,
    anchor: str = "mm",
    stroke_width: int = 0,
) -> None:
    """安全的文字绘制：缺字形/字体不支持中文时退化成 ASCII，不抛异常。"""
    try:
        draw.text(
            xy,
            text,
            font=font,
            fill=fill,
            anchor=anchor,
            stroke_width=stroke_width,
        )
    except Exception:  # noqa: BLE001 —— 字体缺失时允许静默降级
        try:
            draw.text(
                xy,
                _ascii_fallback(text),
                font=font,
                fill=fill,
                anchor=anchor,
                stroke_width=stroke_width,
            )
        except Exception:  # noqa: BLE001 —— 最终仍失败就不画文字
            pass


def _token(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> float:
    try:
        return float(draw.textlength(text, font=font))
    except Exception:  # noqa: BLE001
        return float(len(text)) * float(getattr(font, "size", 16))


def _wrap_text(text: str, max_chars: int = 16, max_lines: int = 2) -> list[str]:
    """按字符数硬折行（中文没有空格），最多两行，超出用省略号。"""
    clean = " ".join(str(text).split())
    if not clean:
        return []
    chunks = [clean[index : index + max_chars] for index in range(0, len(clean), max_chars)]
    lines = chunks[:max_lines]
    if len(chunks) > max_lines and lines:
        lines[-1] = lines[-1][: max(1, max_chars - 1)] + "…"
    return lines


def _read_wav(path: Path) -> tuple[np.ndarray, int, float]:
    """读取 WAV，返回 (mono float32 样本, 采样率, 时长)。"""
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"音频不存在：{source}")
    try:
        with wave.open(str(source), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            sample_rate = handle.getframerate()
            frame_count = handle.getnframes()
            raw = handle.readframes(frame_count)
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"不是有效的 WAV 文件：{source}") from exc

    if sample_rate <= 0 or frame_count <= 0:
        raise ValueError(f"WAV 为空或采样率非法：{source}")
    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"不支持的 WAV 采样位宽：{width * 8} bit")

    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data.astype(np.float32), sample_rate, frame_count / float(sample_rate)


def _frame_envelope(samples: np.ndarray, sample_rate: int, fps: int, frames: int) -> np.ndarray:
    """计算每帧的 RMS 包络并归一化到 0..1，用于驱动嘴部。"""
    frame_length = max(1, int(round(sample_rate / float(fps))))
    needed = frames * frame_length
    if samples.size < needed:
        padded = np.pad(samples, (0, needed - samples.size))
    else:
        padded = samples[:needed]
    blocks = padded.reshape(frames, frame_length).astype(np.float64)
    rms = np.sqrt(np.mean(blocks * blocks, axis=1))
    peak = float(np.max(rms)) if rms.size else 0.0
    if peak > 1e-9:
        rms = rms / peak
    rms = np.clip(rms, 0.0, 1.0)
    if rms.size >= 3:
        kernel = np.array([0.25, 0.5, 0.25], dtype=np.float64)
        rms = np.convolve(np.pad(rms, (1, 1), mode="edge"), kernel, mode="valid")
    return rms.astype(np.float32)


def _make_background(width: int, height: int, style: str) -> Image.Image:
    preset = _STYLE_PRESETS[style]
    top = np.array(preset["top"], dtype=np.float32)
    bottom = np.array(preset["bottom"], dtype=np.float32)
    ramp = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    colors = top[None, :] + (bottom - top)[None, :] * ramp  # (h, 3)
    array = np.broadcast_to(colors[:, None, :], (height, width, 3))
    return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), mode="RGB")


def _apply_grain(image: Image.Image, frame_index: int) -> Image.Image:
    """叠加确定性的轻胶片颗粒；每帧种子固定，保证可复现。"""
    array = np.asarray(image, dtype=np.int16)
    rng = np.random.default_rng(1000 + int(frame_index))
    noise = rng.integers(-3, 4, size=array.shape[:2], dtype=np.int16)[:, :, None]
    return Image.fromarray(np.clip(array + noise, 0, 255).astype(np.uint8), mode="RGB")


def _avatar_geometry(width: int, height: int) -> tuple[int, int, int]:
    """返回头像圆盘的 (cx, cy, radius)。"""
    radius = int(max(24, min(width * 0.36, height * 0.20)))
    return width // 2, int(height * 0.40), radius


def _build_static_layer(
    width: int,
    height: int,
    style: str,
    title: str,
    cx: int,
    cy: int,
    radius: int,
    fonts: dict[str, ImageFont.FreeTypeFont | ImageFont.ImageFont],
) -> Image.Image:
    """预先绘制不随时间变化的图层：头像、标题、水印。"""
    preset = _STYLE_PRESETS[style]
    accent = preset["accent"]
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 外发光
    glow_box = (cx - radius - 12, cy - radius - 12, cx + radius + 12, cy + radius + 12)
    draw.ellipse(glow_box, fill=(*accent, 30))

    disc_box = (cx - radius, cy - radius, cx + radius, cy + radius)
    head_radius = int(radius * 0.40)
    head_cy = cy - int(radius * 0.10)
    head_box = (
        cx - head_radius,
        head_cy - head_radius,
        cx + head_radius,
        head_cy + head_radius,
    )
    shoulder_box = (
        cx - int(radius * 0.68),
        cy + int(radius * 0.28),
        cx + int(radius * 0.68),
        cy + int(radius * 1.35),
    )

    avatar = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    art = ImageDraw.Draw(avatar)
    art.ellipse(disc_box, fill=(*preset["disc"], 255))
    art.ellipse(shoulder_box, fill=(*preset["body"], 255))
    art.rectangle(
        (
            cx - int(head_radius * 0.30),
            head_cy + int(head_radius * 0.62),
            cx + int(head_radius * 0.30),
            cy + int(radius * 0.45),
        ),
        fill=(*preset["skin"], 255),
    )
    art.ellipse(head_box, fill=(*preset["skin"], 255))
    # 上半圆当作头发（Pillow 角度：180° 左，270° 上，360° 右）
    art.pieslice(head_box, 180, 360, fill=(*preset["hair"], 255))
    art.ellipse(
        (cx - head_radius, head_cy - int(head_radius * 0.28), cx + head_radius, head_cy + int(head_radius * 0.34)),
        fill=(*preset["skin"], 255),
    )
    art.pieslice(head_box, 180, 360, fill=(*preset["hair"], 255))
    # 耳朵
    for sign in (-1, 1):
        ear_x = cx + sign * head_radius
        art.ellipse(
            (
                ear_x - int(head_radius * 0.12),
                head_cy - int(head_radius * 0.12),
                ear_x + int(head_radius * 0.12),
                head_cy + int(head_radius * 0.18),
            ),
            fill=(*preset["skin"], 255),
        )
    # 眼睛（位于发际线下方）
    eye_y = head_cy + int(head_radius * 0.04)
    eye_radius = max(2, int(head_radius * 0.075))
    for sign in (-1, 1):
        eye_x = cx + sign * int(head_radius * 0.30)
        art.ellipse(
            (eye_x - eye_radius, eye_y - eye_radius, eye_x + eye_radius, eye_y + eye_radius),
            fill=(*preset["hair"], 255),
        )
    # 鼻梁的一点阴影
    art.ellipse(
        (
            cx - max(1, int(head_radius * 0.045)),
            head_cy + int(head_radius * 0.10),
            cx + max(1, int(head_radius * 0.045)),
            head_cy + int(head_radius * 0.20),
        ),
        fill=(210, 168, 144, 120),
    )

    # 用圆盘遮罩裁掉肩膀/头发的溢出部分
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).ellipse(disc_box, fill=255)
    avatar.putalpha(ImageChops.multiply(avatar.getchannel("A"), mask))
    overlay.alpha_composite(avatar)
    draw.ellipse(disc_box, outline=(*accent, 190), width=max(2, radius // 42))

    # 标题
    title_y = int(height * 0.072)
    _draw_text(
        draw,
        (width / 2, title_y),
        str(title),
        fonts["title"],
        (*preset["title"], 255),
        anchor="mm",
        stroke_width=2,
    )
    # 标题下的强调线
    line_half = max(24, int(width * 0.09))
    draw.rounded_rectangle(
        (
            width // 2 - line_half,
            title_y + int(fonts_size(fonts["title"]) * 0.95),
            width // 2 + line_half,
            title_y + int(fonts_size(fonts["title"]) * 1.02),
        ),
        radius=2,
        fill=(*accent, 200),
    )

    # 水印
    _draw_text(
        draw,
        (width - int(width * 0.035), int(height * 0.038)),
        "AIGC · Studio",
        fonts["watermark"],
        (*preset["watermark"], 210),
        anchor="rt",
    )
    return overlay


def fonts_size(font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> int:
    """兼容不同 Pillow 版本的字体字号读取。"""
    return int(getattr(font, "size", 16))


def _draw_mouth(
    draw: ImageDraw.ImageDraw,
    cx: int,
    mouth_cy: int,
    head_radius: int,
    envelope: float,
    accent: tuple[int, int, int],
) -> None:
    """嘴部用 5 根均衡条表示，高度随当前帧音频包络变化。"""
    count = 5
    bar_width = max(2, int(head_radius * 0.11))
    gap = max(2, int(head_radius * 0.08))
    total = count * bar_width + (count - 1) * gap
    start = cx - total // 2
    pattern = (0.75, 1.0, 1.25, 0.95, 0.70)
    level = max(0.0, min(1.0, float(envelope)))
    for index in range(count):
        height = max(2, int(head_radius * (0.05 + 0.55 * level * pattern[index])))
        x0 = start + index * (bar_width + gap)
        top = mouth_cy - height // 2
        draw.rounded_rectangle(
            (x0, top, x0 + bar_width, top + height),
            radius=max(1, bar_width // 2),
            fill=(*accent, 255),
        )


def _segment_text_at(segments: list[dict], timestamp: float) -> str:
    """返回覆盖当前时间点的字幕文本；没有覆盖则为空串。"""
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        try:
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", start))
        except (TypeError, ValueError):
            continue
        if start <= timestamp < end:
            return str(segment.get("text") or "")
    return ""


def _draw_subtitle(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    preset: dict[str, tuple[int, int, int]],
) -> None:
    """绘制底部字幕面板，每行最多 16 个字符、最多两行。"""
    lines = _wrap_text(text, max_chars=16, max_lines=2)
    if not lines:
        return

    font_size = fonts_size(font)
    line_height = int(font_size * 1.35)
    padding = int(font_size * 0.55)
    margin = int(width * 0.05)
    panel_height = padding * 2 + line_height * len(lines)
    y1 = height - int(height * 0.045)
    y0 = y1 - panel_height

    draw.rounded_rectangle(
        (margin, y0, width - margin, y1),
        radius=max(8, padding),
        fill=(0, 0, 0, 132),
        outline=(*preset["accent"], 95),
        width=1,
    )
    for index, line in enumerate(lines):
        _draw_text(
            draw,
            (width / 2, y0 + padding + line_height * (index + 0.5)),
            line,
            font,
            (*preset["subtitle"], 255),
            anchor="mm",
        )


# --------------------------------------------------------------------------- #
# 子进程
# --------------------------------------------------------------------------- #
def _stderr_text(error: subprocess.CalledProcessError) -> str:
    raw = error.stderr
    if isinstance(raw, bytes | bytearray):
        return bytes(raw).decode("utf-8", errors="replace")
    return str(raw or "")


def _run_ffmpeg(command: list[str], action: str) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=get_settings().ffmpeg_timeout)
    except FileNotFoundError as exc:
        raise RuntimeError(f"{action}失败：找不到可执行文件 {command[0]}，请确认在 PATH 中或配置 FFMPEG_BIN") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{action}失败：执行超时（>{get_settings().ffmpeg_timeout:.0f}s）") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"{action}失败：{_stderr_text(exc)[-800:]}") from exc


def _run_capture(command: list[str], action: str) -> bytes:
    try:
        completed = subprocess.run(command, check=True, capture_output=True, timeout=get_settings().ffmpeg_timeout)
    except FileNotFoundError as exc:
        raise RuntimeError(f"{action}失败：找不到可执行文件 {command[0]}，请确认在 PATH 中或配置 FFPROBE_BIN") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{action}失败：执行超时（>{get_settings().ffmpeg_timeout:.0f}s）") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"{action}失败：{_stderr_text(exc)[-800:]}") from exc
    return completed.stdout or b""


# --------------------------------------------------------------------------- #
# MP3 兼容层
# --------------------------------------------------------------------------- #
# 部分精简版 ffmpeg 没有编译 libmp3lame，只能使用 Windows 的 mp3_mf 编码器。
# mp3_mf 写出的 Info 帧计数是错的（导致 ffprobe 时长偏小），这里在编码后
# 按真实 MPEG 帧数修补 Xing/Info 头，保证 probe() 的时长可信。
_MP3_BITRATES_V1 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
_MP3_BITRATES_V2 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0)
_MP3_SAMPLE_RATES = {
    3: (44100, 48000, 32000, 0),
    2: (22050, 24000, 16000, 0),
    0: (11025, 12000, 8000, 0),
}


def _mp3_frame_length(header: bytes) -> int:
    """根据 4 字节 MP3 帧头返回整帧字节数；非法头返回 0。"""
    if len(header) < 4 or header[0] != 0xFF or (header[1] & 0xE0) != 0xE0:
        return 0
    version = (header[1] >> 3) & 0x03
    layer = (header[1] >> 1) & 0x03
    bitrate_index = (header[2] >> 4) & 0x0F
    rate_index = (header[2] >> 2) & 0x03
    padding = (header[2] >> 1) & 0x01
    if layer != 1 or version not in _MP3_SAMPLE_RATES:
        return 0
    bitrate = (_MP3_BITRATES_V1 if version == 3 else _MP3_BITRATES_V2)[bitrate_index] * 1000
    sample_rate = _MP3_SAMPLE_RATES[version][rate_index]
    if not bitrate or not sample_rate:
        return 0
    coefficient = 144 if version == 3 else 72
    return coefficient * bitrate // sample_rate + padding


def _patch_mp3_info(path: Path) -> None:
    """修正 mp3_mf 生成的 Xing/Info 帧计数（失败时静默保留原文件）。"""
    try:
        data = bytearray(Path(path).read_bytes())
    except OSError:
        return
    if len(data) < 128:
        return

    start = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        tag_size = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) | ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
        start = 10 + tag_size
    while start + 4 <= len(data) and not _mp3_frame_length(bytes(data[start : start + 4])):
        start += 1
    if start + 4 > len(data):
        return

    tag = data.find(b"Info", start, min(len(data), start + 256))
    if tag < 0:
        tag = data.find(b"Xing", start, min(len(data), start + 256))
    if tag < 0 or tag + 16 > len(data):
        return
    flags = int.from_bytes(data[tag + 4 : tag + 8], "big")
    if not flags & 0x01:  # 没有 frame count 字段
        return

    position = start
    frames = 0
    while position + 4 <= len(data):
        length = _mp3_frame_length(bytes(data[position : position + 4]))
        if length <= 0 or position + length > len(data):
            break
        position += length
        frames += 1
    if frames <= 0:
        return

    data[tag + 8 : tag + 12] = frames.to_bytes(4, "big")
    if flags & 0x02:  # byte count 字段
        data[tag + 12 : tag + 16] = (len(data) - start).to_bytes(4, "big")
    try:
        Path(path).write_bytes(data)
    except OSError:
        return


def _run_mp3_ffmpeg(command: list[str], action: str) -> None:
    """优先 libmp3lame；该编码器缺失时退化到 mp3_mf 并修补 Info 头。"""
    try:
        _run_ffmpeg(command, action)
        return
    except RuntimeError as exc:
        message = str(exc)
        if "libmp3lame" not in message or ("Unknown encoder" not in message and "Encoder not found" not in message):
            raise

    fallback = list(command)
    codec_index = fallback.index("libmp3lame")
    fallback[codec_index] = "mp3_mf"
    # MediaFoundation 编码器在 44.1kHz 下最稳定。
    fallback[-1:-1] = ["-ar", "44100"]
    _run_ffmpeg(fallback, action)
    _patch_mp3_info(Path(command[-1]))


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# 对外 API
# --------------------------------------------------------------------------- #
def render_avatar_video(
    audio_path: Path,
    segments: list[dict],
    out_path: Path,
    *,
    title: str = "AI 数字人",
    style: str = "studio",
    width: int = 720,
    height: int = 1280,
    fps: int = 2,
) -> dict:
    """渲染数字人短视频。

    :param audio_path: 作为时长基准与音轨的 WAV 文件。
    :param segments: ``[{"start","end","text"}]``，用于当前时间点的字幕。
    :param out_path: 输出 MP4 路径（父目录自动创建）。
    :param title: 顶部标题。
    :param style: ``studio`` / ``news`` / ``warm``。
    :param width: 偶数宽度。
    :param height: 偶数高度。
    :param fps: 帧率，默认 2（离线演示足够且编码快）。
    :returns: ``{"path","duration","width","height","fps","frames","size_bytes","style","codec"}``。
    """
    source = Path(audio_path)
    if not source.exists():
        raise FileNotFoundError(f"音频不存在：{source}")
    if not isinstance(segments, list):
        raise ValueError("segments 必须是列表")
    if style not in _STYLE_PRESETS:
        raise ValueError("style 必须是 studio / news / warm 之一")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise ValueError("width/height 必须是正整数")
    if width % 2 or height % 2:
        raise ValueError("width/height 必须为偶数（libx264 + yuv420p 要求）")
    if not isinstance(fps, int) or isinstance(fps, bool) or not (1 <= fps <= 60):
        raise ValueError("fps 必须在 1..60 之间")

    samples, sample_rate, duration = _read_wav(source)
    frames = max(1, int(math.ceil(duration * fps - 1e-9)))
    envelope = _frame_envelope(samples, sample_rate, fps, frames)

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    unit = min(width, height)
    title_size = max(16, int(unit * 0.085))
    subtitle_size = max(14, min(int(unit * 0.068), int(width * 0.86 / 16.0)))
    watermark_size = max(10, int(unit * 0.032))
    fonts: dict[str, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {
        "title": _find_font(title_size),
        "subtitle": _find_font(subtitle_size),
        "watermark": _find_font(watermark_size),
    }

    cx, cy, radius = _avatar_geometry(width, height)
    head_cy = cy - int(radius * 0.10)
    head_radius = int(radius * 0.40)
    mouth_cy = head_cy + int(head_radius * 0.48)

    base = _make_background(width, height, style)
    static_layer = _build_static_layer(width, height, style, title, cx, cy, radius, fonts)
    preset = _STYLE_PRESETS[style]

    frames_dir = target.parent / f".{target.stem}_frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    try:
        for index in range(frames):
            timestamp = index / float(fps)
            frame = _apply_grain(base, index)
            frame = Image.alpha_composite(frame.convert("RGBA"), static_layer).convert("RGB")

            overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            _draw_mouth(draw, cx, mouth_cy, head_radius, float(envelope[index]), preset["accent"])
            subtitle_text = _segment_text_at(segments, timestamp)
            if subtitle_text:
                _draw_subtitle(draw, width, height, subtitle_text, fonts["subtitle"], preset)

            frame = Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")
            frame.save(frames_dir / f"frame_{index + 1:04d}.png", format="PNG")

        command = [
            ffmpeg_bin(),
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(frames_dir / "frame_%04d.png"),
            "-i",
            str(source),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(target),
        ]
        _run_ffmpeg(command, "数字人视频编码")
        if not target.exists():
            raise RuntimeError("数字人视频编码失败：ffmpeg 未生成输出文件")
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)

    return {
        "path": str(target),
        "duration": round(duration, 3),
        "width": width,
        "height": height,
        "fps": fps,
        "frames": frames,
        "size_bytes": target.stat().st_size,
        "style": style,
        "codec": "h264",
    }


def probe(path: Path) -> dict:
    """用 ffprobe 读取媒体信息。

    :returns: ``{"path","duration","size_bytes","bit_rate","streams"}``，
              其中 streams 每项为 ``{"type","codec","width","height"}``。
    """
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"媒体文件不存在：{source}")

    command = [
        ffprobe_bin(),
        "-v",
        "error",
        "-show_entries",
        "format=duration,size,bit_rate",
        "-show_entries",
        "stream=codec_type,codec_name,width,height",
        "-of",
        "json",
        str(source),
    ]
    stdout = _run_capture(command, "ffprobe 探测")
    try:
        payload = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe 输出无法解析：{exc}") from exc

    format_info = payload.get("format") or {}
    streams: list[dict] = []
    for item in payload.get("streams") or []:
        streams.append(
            {
                "type": item.get("codec_type"),
                "codec": item.get("codec_name"),
                "width": _as_int(item.get("width"), 0) or None,
                "height": _as_int(item.get("height"), 0) or None,
            }
        )

    size_bytes = _as_int(format_info.get("size"), 0) or source.stat().st_size
    return {
        "path": str(source),
        "duration": round(_as_float(format_info.get("duration"), 0.0), 3),
        "size_bytes": size_bytes,
        "bit_rate": _as_int(format_info.get("bit_rate"), 0),
        "streams": streams,
    }


def extract_audio(video_path: Path, out_path: Path, fmt: str = "mp3") -> dict:
    """从视频中提取音轨；``fmt`` 仅支持 mp3 / wav。"""
    source = Path(video_path)
    if not source.exists():
        raise FileNotFoundError(f"视频不存在：{source}")
    audio_format = str(fmt).lower()
    if audio_format not in {"mp3", "wav"}:
        raise ValueError("fmt 只支持 mp3 或 wav")

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    codec = "libmp3lame" if audio_format == "mp3" else "pcm_s16le"
    command = [
        ffmpeg_bin(),
        "-y",
        "-i",
        str(source),
        "-vn",
        "-acodec",
        codec,
        str(target),
    ]
    if audio_format == "mp3":
        _run_mp3_ffmpeg(command, "音轨提取")
    else:
        _run_ffmpeg(command, "音轨提取")
    if not target.exists():
        raise RuntimeError("音轨提取失败：ffmpeg 未生成输出文件")
    info = probe(target)
    return {
        "path": str(target),
        "duration": info["duration"],
        "size_bytes": target.stat().st_size,
        "format": audio_format,
    }


def thumbnail(video_path: Path, out_path: Path, at: float = 1.0) -> dict:
    """截取视频指定秒数的一帧作为 JPEG 封面。"""
    source = Path(video_path)
    if not source.exists():
        raise FileNotFoundError(f"视频不存在：{source}")
    try:
        timestamp = float(at)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"非法时间点：{at!r}") from exc
    if not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError("at 必须是非负有限秒数")

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_bin(),
        "-y",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(target),
    ]
    _run_ffmpeg(command, "缩略图截取")
    if not target.exists():
        raise RuntimeError("缩略图截取失败：ffmpeg 未生成输出文件")

    with Image.open(target) as image:
        width, height = image.size
    return {
        "path": str(target),
        "width": int(width),
        "height": int(height),
        "size_bytes": target.stat().st_size,
    }


def _atempo_chain(factor: float) -> list[float]:
    """把任意正倍速拆成若干个 atempo（每个限制在 0.5..2.0）。"""
    remaining = float(factor)
    chain: list[float] = []
    while remaining > 2.0 + 1e-9:
        chain.append(2.0)
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        chain.append(0.5)
        remaining /= 0.5
    chain.append(remaining)
    return chain


def _format_filter(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "1"


def change_speed(audio_path: Path, out_path: Path, factor: float) -> dict:
    """用 ffmpeg ``atempo`` 调整音频倍速；超出 0.5..2.0 时自动串联多级。"""
    source = Path(audio_path)
    if not source.exists():
        raise FileNotFoundError(f"音频不存在：{source}")
    try:
        speed = float(factor)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"非法倍速：{factor!r}") from exc
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("factor 必须是正数")

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    suffix = target.suffix.lower()
    if suffix == ".wav":
        codec = ["-acodec", "pcm_s16le"]
        use_mp3_fallback = False
    elif suffix in {".m4a", ".aac"}:
        codec = ["-acodec", "aac"]
        use_mp3_fallback = False
    else:
        codec = ["-acodec", "libmp3lame"]
        use_mp3_fallback = True

    chain = ",".join(f"atempo={_format_filter(value)}" for value in _atempo_chain(speed))
    command = [
        ffmpeg_bin(),
        "-y",
        "-i",
        str(source),
        "-vn",
        "-filter:a",
        chain,
        *codec,
        str(target),
    ]
    if use_mp3_fallback:
        _run_mp3_ffmpeg(command, "音频变速")
    else:
        _run_ffmpeg(command, "音频变速")
    if not target.exists():
        raise RuntimeError("音频变速失败：ffmpeg 未生成输出文件")

    info = probe(target)
    return {
        "path": str(target),
        "duration": info["duration"],
        "size_bytes": target.stat().st_size,
        "factor": speed,
    }


def compress_video(video_path: Path, out_path: Path, crf: int = 28) -> dict:
    """按 CRF 压缩视频（工具箱附加能力）。"""
    source = Path(video_path)
    if not source.exists():
        raise FileNotFoundError(f"视频不存在：{source}")
    if not isinstance(crf, int) or not (0 <= crf <= 51):
        raise ValueError("crf 必须在 0..51 之间")

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_bin(),
        "-y",
        "-i",
        str(source),
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-preset",
        "medium",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(target),
    ]
    _run_ffmpeg(command, "视频压缩")
    if not target.exists():
        raise RuntimeError("视频压缩失败：ffmpeg 未生成输出文件")

    info = probe(target)
    return {
        "path": str(target),
        "duration": info["duration"],
        "size_bytes": target.stat().st_size,
        "crf": crf,
    }


__all__ = [
    "change_speed",
    "compress_video",
    "extract_audio",
    "probe",
    "render_avatar_video",
    "thumbnail",
]
