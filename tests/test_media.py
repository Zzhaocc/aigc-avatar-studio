"""确定性离线媒体引擎的回归测试。

所有产物都放在仓库内的 ``data/test_media``，测试结束后由模块级 fixture
清理；不使用 ``tempfile.mkdtemp``（沙箱会创建 0700 目录，不利于后续访问）。
"""

from __future__ import annotations

import hashlib
import shutil
import wave
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")

from app.media.avatar import (  # noqa: E402 —— 依赖上面的 importorskip
    change_speed,
    extract_audio,
    probe,
    render_avatar_video,
    thumbnail,
)
from app.media.subtitles import build_srt, format_ts, parse_srt  # noqa: E402
from app.media.tts import (  # noqa: E402
    VoiceSpec,
    concat_wavs,
    make_reference_sample,
    synthesize,
)

PUNCTUATION = frozenset("，。！？,.!?;；:：")
SAMPLE_RATE = 24000


def expected_seconds(text: str, speed: float = 1.0) -> float:
    """与 tts.py 文档一致的时长模型，供断言使用。"""
    total = 0.0
    for char in text:
        if char.isspace():
            total += 0.05
        elif char in PUNCTUATION:
            total += 0.12
        else:
            total += 0.16 / speed
    return total


def read_wav(path: Path) -> tuple[wave._wave_params, bytes]:
    """读取 WAV 参数与原始帧数据。"""
    with wave.open(str(path), "rb") as handle:
        return handle.getparams(), handle.readframes(handle.getnframes())


def zero_crossing_rate(frames: bytes) -> int:
    """粗略的过零次数；基频/高频谐波越强，数值越大。"""
    samples = np.frombuffer(frames, dtype="<i2").astype(np.int32)
    signs = np.signbit(samples)
    return int(np.count_nonzero(signs[1:] != signs[:-1]))


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture(scope="module")
def workdir() -> Path:
    """模块级工作目录：仓库内 data/test_media，结束时整体清理。"""
    root = Path("data/test_media")
    root.mkdir(parents=True, exist_ok=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="module")
def sample_audio(workdir: Path) -> dict:
    """约 2.3 秒的短中文配音，供视频测试复用。"""
    text = "大家好，欢迎来到数字人演播室。"
    return synthesize(text, workdir / "avatar_voice.wav", VoiceSpec(speed=1.0))


@pytest.fixture(scope="module")
def avatar_video(workdir: Path, sample_audio: dict) -> dict:
    """渲染一次 360×640 @2fps 的视频，多个用例复用。"""
    if not has_ffmpeg():
        pytest.skip("ffmpeg/ffprobe 不可用，跳过视频相关测试")
    duration = float(sample_audio["duration"])
    segments = [
        {"start": 0.0, "end": duration, "text": "大家好，欢迎来到数字人演播室。"},
    ]
    return render_avatar_video(
        Path(sample_audio["path"]),
        segments,
        workdir / "avatar.mp4",
        title="AI 数字人",
        style="studio",
        width=360,
        height=640,
        fps=2,
    )


# --------------------------------------------------------------------------- #
# TTS
# --------------------------------------------------------------------------- #
def test_synthesize_valid_wav_and_duration(workdir: Path) -> None:
    text = "你好，AIGC 数字人！"
    speed = 1.25
    result = synthesize(text, workdir / "basic.wav", VoiceSpec(speed=speed))

    params, frames = read_wav(Path(result["path"]))
    assert params.nchannels == 1
    assert params.sampwidth == 2
    assert params.framerate == SAMPLE_RATE
    assert params.nframes > 0
    assert len(frames) == params.nframes * 2

    expected = expected_seconds(text, speed)
    assert abs(result["duration"] - expected) <= max(0.02, expected * 0.15)
    assert abs(params.nframes / params.framerate - expected) <= max(0.02, expected * 0.15)
    assert result["sample_rate"] == SAMPLE_RATE
    assert result["char_count"] == len(text)
    assert result["voice"] == {"pitch": 1.0, "speed": speed, "timbre": "warm"}
    assert result["size_bytes"] == Path(result["path"]).stat().st_size
    assert result["size_bytes"] == 44 + params.nframes * 2


def test_pitch_is_duration_independent_and_timbre_changes_f0(workdir: Path) -> None:
    text = "数字化语音合成测试"

    low = synthesize(text, workdir / "pitch_low.wav", VoiceSpec(pitch=0.75))
    high = synthesize(text, workdir / "pitch_high.wav", VoiceSpec(pitch=1.5))
    assert low["duration"] == high["duration"]
    assert low["size_bytes"] == high["size_bytes"]

    bright = synthesize(text, workdir / "timbre_bright.wav", VoiceSpec(timbre="bright"))
    deep = synthesize(text, workdir / "timbre_deep.wav", VoiceSpec(timbre="deep"))
    assert bright["duration"] == deep["duration"]

    bright_frames = read_wav(Path(bright["path"]))[1]
    deep_frames = read_wav(Path(deep["path"]))[1]
    # bright: 基频 ×1.15 且高次谐波更强；deep: ×0.85 且高次谐波更弱。
    assert zero_crossing_rate(bright_frames) > zero_crossing_rate(deep_frames)


def test_synthesis_is_deterministic(workdir: Path) -> None:
    text = "确定性测试，同样的输入得到同样的声音。"
    voice = VoiceSpec(pitch=1.1, speed=1.2, timbre="bright")
    first = synthesize(text, workdir / "deterministic_a.wav", voice)
    second = synthesize(text, workdir / "deterministic_b.wav", voice)

    first_frames = read_wav(Path(first["path"]))[1]
    second_frames = read_wav(Path(second["path"]))[1]
    assert len(first_frames) == len(second_frames) == first["size_bytes"] - 44
    assert hashlib.sha256(first_frames).hexdigest() == hashlib.sha256(second_frames).hexdigest()
    assert first["size_bytes"] == second["size_bytes"]


@pytest.mark.parametrize("bad_text", ["", "   ", "\n\t  "])
def test_empty_text_raises(workdir: Path, bad_text: str) -> None:
    with pytest.raises(ValueError):
        synthesize(bad_text, workdir / "empty.wav")


def test_reference_sample(workdir: Path) -> None:
    result = make_reference_sample(workdir / "reference.wav", seconds=0.8, seed=11)
    assert result["kind"] == "reference"
    assert result["char_count"] > 0
    assert abs(result["duration"] - 0.8) <= 0.01
    params, _ = read_wav(Path(result["path"]))
    assert params.nchannels == 1 and params.sampwidth == 2
    assert params.framerate == SAMPLE_RATE


def test_concat_wavs_adds_gaps(workdir: Path) -> None:
    first = synthesize("第一段", workdir / "concat_1.wav")
    second = synthesize("第二段", workdir / "concat_2.wav")
    result = concat_wavs(
        [Path(first["path"]), Path(second["path"])],
        workdir / "concat.wav",
    )

    expected = first["duration"] + second["duration"] + 0.15
    params, _ = read_wav(Path(result["path"]))
    assert params.nchannels == 1 and params.sampwidth == 2
    assert result["segments"] == 2
    assert abs(result["duration"] - expected) <= 0.05
    assert abs(params.nframes / params.framerate - expected) <= 0.05
    assert result["size_bytes"] == Path(result["path"]).stat().st_size

    with pytest.raises(ValueError):
        concat_wavs([], workdir / "concat_empty.wav")


# --------------------------------------------------------------------------- #
# 字幕
# --------------------------------------------------------------------------- #
def test_format_ts() -> None:
    assert format_ts(0) == "00:00:00,000"
    assert format_ts(1.25) == "00:00:01,250"
    assert format_ts(3661.999) == "01:01:01,999"
    assert format_ts(3661.9999) == "01:01:02,000"
    assert format_ts(-3.0) == "00:00:00,000"


def test_srt_roundtrip_and_timing(workdir: Path) -> None:
    segments = [
        {"text": "你好", "duration": 1.0},
        {"text": "   ", "duration": 0.5},  # 空文本跳过，但时间轴继续
        {"text": "世界", "duration": -2.0},  # 负时长按 0 处理
        {"text": "第三句", "duration": 0.75},
    ]
    srt_path = workdir / "timeline.srt"
    result = build_srt(segments, srt_path)

    assert result["count"] == 3
    assert abs(result["duration"] - 2.25) <= 1e-6
    assert result["path"] == str(srt_path)
    raw = srt_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")  # 无 BOM
    assert b"00:00:00,000 --> 00:00:01,000" in raw

    body = srt_path.read_text(encoding="utf-8")
    assert body == result["text"]
    cues = parse_srt(body)
    assert [cue["index"] for cue in cues] == [1, 2, 3]
    assert [cue["text"] for cue in cues] == ["你好", "世界", "第三句"]
    starts = [cue["start"] for cue in cues]
    ends = [cue["end"] for cue in cues]
    assert starts == sorted(starts)
    assert ends == sorted(ends)
    assert starts == [0.0, 1.5, 1.5]
    assert ends == [1.0, 1.5, 2.25]
    assert len(result["text"]) <= 20000


def test_parse_srt_tolerates_dot_millis_and_bom() -> None:
    text = "\ufeff1\r\n00:00:00.500 --> 00:00:01.250\r\n测试字幕\r\n"
    cues = parse_srt(text)
    assert len(cues) == 1
    assert cues[0]["index"] == 1
    assert cues[0]["start"] == 0.5
    assert cues[0]["end"] == 1.25
    assert cues[0]["text"] == "测试字幕"


# --------------------------------------------------------------------------- #
# 视频与媒体工具
# --------------------------------------------------------------------------- #
def test_render_avatar_video(avatar_video: dict, sample_audio: dict) -> None:
    path = Path(avatar_video["path"])
    assert path.exists()
    assert avatar_video["codec"] == "h264"
    assert avatar_video["width"] == 360
    assert avatar_video["height"] == 640
    assert avatar_video["fps"] == 2
    assert avatar_video["frames"] >= 4
    assert avatar_video["size_bytes"] > 20 * 1024

    info = probe(path)
    assert abs(info["duration"] - float(sample_audio["duration"])) <= 0.6
    assert info["size_bytes"] == avatar_video["size_bytes"]
    video_streams = [stream for stream in info["streams"] if stream["type"] == "video"]
    audio_streams = [stream for stream in info["streams"] if stream["type"] == "audio"]
    assert video_streams and video_streams[0]["codec"] == "h264"
    assert video_streams[0]["width"] == 360 and video_streams[0]["height"] == 640
    assert audio_streams


def test_extract_audio_thumbnail_and_speed(
    workdir: Path,
    avatar_video: dict,
    sample_audio: dict,
) -> None:
    video_path = Path(avatar_video["path"])

    mp3_path = workdir / "extracted.mp3"
    extracted = extract_audio(video_path, mp3_path, fmt="mp3")
    assert mp3_path.exists()
    assert extracted["format"] == "mp3"
    assert extracted["size_bytes"] == mp3_path.stat().st_size
    mp3_info = probe(mp3_path)
    assert mp3_info["duration"] > 0
    assert any(stream["type"] == "audio" for stream in mp3_info["streams"])

    jpg_path = workdir / "cover.jpg"
    cover = thumbnail(video_path, jpg_path, at=0.5)
    assert jpg_path.exists()
    assert cover["size_bytes"] > 1024
    assert (cover["width"], cover["height"]) == (360, 640)

    fast_path = workdir / "fast.mp3"
    fast = change_speed(Path(sample_audio["path"]), fast_path, 1.5)
    assert fast_path.exists()
    assert fast["factor"] == 1.5
    expected = float(sample_audio["duration"]) / 1.5
    assert abs(fast["duration"] - expected) <= 0.3


def test_probe_missing_file_raises(workdir: Path) -> None:
    with pytest.raises(FileNotFoundError):
        probe(workdir / "not_found.mp4")
