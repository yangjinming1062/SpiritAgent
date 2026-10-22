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


def _audio_info(data: bytes) -> tuple[float, str] | None:
    """解析音频时长与主 MIME，不可解析或非有效时长返回 None。"""
    try:
        audio = AudioFile(io.BytesIO(data))
        length = float(audio.info.length)
        mime = audio.mime[0]
        if not math.isfinite(length) or length <= 0:
            return None
    except Exception:
        return None
    return length, mime


def audio_bytes_info(data: bytes) -> tuple[float, str]:
    """校验合成音频，返回实际时长与文件扩展名。"""
    info = _audio_info(data)
    if info is None:
        raise ValueError("生成的语音无效")
    duration, mime = info
    extension = {
        "audio/mp3": "mp3",
        "audio/mpeg": "mp3",
        "audio/wav": "wav",
        "audio/ogg": "ogg",
        "audio/flac": "flac",
    }.get(mime)
    if extension is None:
        raise ValueError("生成的语音格式不受支持")
    return duration, extension


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


def audio_wav_bytes(path: Path) -> tuple[bytes, float]:
    """探测纯音频并转为 16kHz 单声道 WAV，返回字节与探测时长。"""
    duration = probe_audio(path)
    if duration * 16000 * 2 + 128 > STT_MAX_AUDIO_BYTES:
        raise VideoProcessError("音频转换后超过输入体积上限", internal="converted audio exceeds byte limit")
    data = ffmpeg_stdout(
        ["-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"],
        failure_message="音频解码失败",
    )
    if len(data) > STT_MAX_AUDIO_BYTES:
        raise VideoProcessError("音频转换后超过输入体积上限", internal="converted audio exceeds byte limit")
    return data, duration


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
        # 该 WAV 由本函数以固定参数写出，时长是字节的确定函数，直接解析。
        info = _audio_info(normalized)
        if info is None:
            raise VideoProcessError("文件不是有效的纯音频", internal="invalid wav length")
        duration, _ = info
        if duration > max_duration:
            raise VideoProcessError(f"声音样本最长为 {max_duration:g} 秒，请截取后重新上传")
    if not normalized or len(normalized) > max_bytes:
        raise VideoProcessError("转换后的声音样本过大，请缩短后重试")
    return normalized, duration
