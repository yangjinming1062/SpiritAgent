import json
import math
from pathlib import Path

from infrastructure.constants import STT_MAX_AUDIO_BYTES
from infrastructure.video_processing.ffmpeg import VideoProcessError, _binary, _run, ffmpeg_stdout


def probe_audio(path: Path) -> float:
    proc = _run(
        [
            _binary("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type:format=duration",
            "-of",
            "json",
            str(path),
        ],
    )
    if proc.returncode:
        raise VideoProcessError("无法读取音频", internal="audio probe failed")
    try:
        data = json.loads(proc.stdout)
        streams = data.get("streams", [])
        duration = float(data.get("format", {}).get("duration", 0))
        if (
            not math.isfinite(duration)
            or duration <= 0
            or not any(stream.get("codec_type") == "audio" for stream in streams)
            or any(stream.get("codec_type") == "video" for stream in streams)
        ):
            raise ValueError("invalid audio")
    except (ValueError, TypeError, AttributeError) as exc:
        raise VideoProcessError("文件不是有效的纯音频", internal="invalid audio stream or duration") from exc
    return duration


def audio_wav_bytes(path: Path) -> bytes:
    duration = probe_audio(path)
    if duration * 16000 * 2 + 128 > STT_MAX_AUDIO_BYTES:
        raise VideoProcessError("音频转换后超过输入体积上限", internal="converted audio exceeds byte limit")
    data = ffmpeg_stdout(
        ["-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"],
        failure_message="音频解码失败",
    )
    if len(data) > STT_MAX_AUDIO_BYTES:
        raise VideoProcessError("音频转换后超过输入体积上限", internal="converted audio exceeds byte limit")
    return data
