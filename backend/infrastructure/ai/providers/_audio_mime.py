def audio_extension_for_mime(mime: str) -> str:
    """multipart 音频上传的文件名扩展名；上传侧归一化只产 wav/mpeg/ogg/mp4/webm，其余（含默认 audio/wav）按 wav 命名。"""
    if "wav" in mime:
        return "wav"
    if "mpeg" in mime or "mp3" in mime:
        return "mp3"
    if "ogg" in mime:
        return "ogg"
    if "flac" in mime:
        return "flac"
    if "m4a" in mime or "mp4" in mime:
        return "m4a"
    if "webm" in mime:
        return "webm"
    return "wav"
