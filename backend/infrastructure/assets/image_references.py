"""持久图片参考复用账户文件，供应商调用前才读取字节。"""

import asyncio
from collections.abc import Mapping
from pathlib import PurePosixPath

from infrastructure.ai.providers._reference import resolve_reference_bytes
from infrastructure.assets.asset_store import (
    build_data_uri,
    normalize_asset_reference,
    resolve_asset_reference,
    save_image_chain_input_asset_async,
    sniff_media_ext,
    validate_image_bytes,
)
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.constants import REMOTE_ASSET_DOWNLOAD_MAX_BYTES
from infrastructure.network import download_capped
from infrastructure.temp_files import get_file_path, owned_temp_files


def owned_image_path(reference: str, user_id: int) -> str:
    path = normalize_asset_reference(reference)
    parsed = parse_companion_asset_path(path)
    if parsed is None or parsed[0] != user_id:
        raise ValueError("参考图片必须属于当前账户")
    return path


def _local_image_bytes(path: str, user_id: int, max_bytes: int) -> tuple[bytes, str]:
    if path.startswith("temp-media/"):
        file_id = PurePosixPath(path).stem
        if not owned_temp_files(file_id, user_id):
            raise ValueError("参考图片必须属于当前账户")
        resolved = get_file_path(file_id)
    else:
        resolved = resolve_asset_reference(owned_image_path(path, user_id))
    if resolved is None:
        raise ValueError("参考图片无法读取")
    if resolved[0].stat().st_size > max_bytes:
        raise ValueError("参考图片超过大小限制")
    data = resolved[0].read_bytes()
    validate_image_bytes(data)
    return data, resolved[1]


async def require_owned_image_path(reference: str, *, user_id: int) -> str:
    path = owned_image_path(reference, user_id)
    await asyncio.to_thread(_local_image_bytes, path, user_id, REMOTE_ASSET_DOWNLOAD_MAX_BYTES)
    return path


async def freeze_image_references(
    references: Mapping[str, str | None],
    *,
    user_id: int,
    generation_id: str,
    directory: str,
    max_bytes: int = REMOTE_ASSET_DOWNLOAD_MAX_BYTES,
) -> dict[str, str]:
    """正式图片仅固定路径；外部或临时输入按本次制作身份转正一次。"""
    frozen: dict[str, str] = {}
    result: dict[str, str] = {}
    for label, value in references.items():
        if not value:
            continue
        local = normalize_asset_reference(value)
        key = local or value
        if key not in frozen:
            if local:
                data, _mime = await asyncio.to_thread(_local_image_bytes, local, user_id, max_bytes)
            elif value.startswith("data:"):
                data, _mime = await resolve_reference_bytes(value)
            else:
                data = await download_capped(value, max_bytes=max_bytes)
            if len(data) > max_bytes:
                raise ValueError("参考图片超过大小限制")
            if parse_companion_asset_path(local) is not None:
                frozen[key] = local
            else:
                await asyncio.to_thread(validate_image_bytes, data)
                ext = sniff_media_ext(data)
                if ext not in {"png", "jpg", "webp", "gif"}:
                    raise ValueError("参考图片无法读取")
                frozen[key] = await save_image_chain_input_asset_async(
                    data, user_id=user_id, generation_id=generation_id, label=label, ext=ext, directory=directory
                )
        result[label] = frozen[key]
    return result


async def materialize_image_references(references: Mapping[str, str | None], *, user_id: int) -> dict[str, str]:
    """仅供即时调用使用；同一路径的多种参考职责共用一次读取和编码。"""
    loaded: dict[str, str] = {}
    result: dict[str, str] = {}
    for label, value in references.items():
        if not value:
            continue
        path = owned_image_path(value, user_id)
        if path not in loaded:
            data, mime = await asyncio.to_thread(_local_image_bytes, path, user_id, REMOTE_ASSET_DOWNLOAD_MAX_BYTES)
            loaded[path] = await asyncio.to_thread(build_data_uri, data, mime)
        result[label] = loaded[path]
    return result
