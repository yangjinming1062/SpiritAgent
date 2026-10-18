import io
import json
import math
import tempfile
from pathlib import Path

from mutagen import File as AudioFile

from infrastructure.constants import STT_MAX_AUDIO_BYTES
from infrastructure.video_processing.ffmpeg import (
    FFPROBE_TIMEOUT_SECONDS,
    VideoProcessError,
    _binary,
    _run,
    ffmpeg_stdout,
)


def audio_bytes_info(data: bytes) -> tuple[float, str]:
    """校验合成音频，返回实际时长与文件扩展名。"""
    audio = AudioFile(io.BytesIO(data))
    if audio is None or not math.isfinite(audio.info.length) or audio.info.length <= 0:
        raise ValueError("生成的语音无效")
    extension = {
        "audio/mp3": "mp3",
        "audio/mpeg": "mp3",
        "audio/wav": "wav",
        "audio/ogg": "ogg",
        "audio/flac": "flac",
    }.get(audio.mime[0])
    if extension is None:
        raise ValueError("生成的语音格式不受支持")
    return float(audio.info.length), extension


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
        timeout=FFPROBE_TIMEOUT_SECONDS,
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


def voice_sample_wav_bytes(data: bytes, *, max_duration: float, max_bytes: int) -> tuple[bytes, float]:
    """完整解码纯音频，写可回填长度的 WAV，保留可校验的真实时长。"""
    with tempfile.TemporaryDirectory(prefix="spirit-voice-") as temporary:
        source = Path(temporary) / "sample"
        source.write_bytes(data)
        probe = _run(
            [_binary("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(source)],
            timeout=FFPROBE_TIMEOUT_SECONDS,
        )
        try:
            streams = json.loads(probe.stdout).get("streams", [])
            if (
                probe.returncode
                or not any(stream.get("codec_type") == "audio" for stream in streams)
                or any(stream.get("codec_type") == "video" for stream in streams)
            ):
                raise ValueError("invalid audio streams")
        except (ValueError, TypeError, AttributeError) as exc:
            raise VideoProcessError("文件不是有效的纯音频") from exc
        destination = Path(temporary) / "normalized.wav"
        result = _run(
            [
                _binary("ffmpeg"),
                "-v",
                "error",
                "-nostdin",
                "-i",
                str(source),
                "-t",
                str(max_duration + 1),
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                "24000",
                "-c:a",
                "pcm_s16le",
                str(destination),
            ],
        )
        if result.returncode:
            raise VideoProcessError("音频无法解码，请重新录制或上传")
        normalized = destination.read_bytes()
        duration = probe_audio(destination)
        if duration > max_duration:
            raise VideoProcessError(f"声音样本最长为 {max_duration:g} 秒，请截取后重新上传")
    if not normalized or len(normalized) > max_bytes:
        raise VideoProcessError("转换后的声音样本过大，请缩短后重试")
    return normalized, duration
