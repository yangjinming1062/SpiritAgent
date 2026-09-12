import asyncio
import time
from typing import Any

import httpx
from components import SETTINGS, download_capped, log_paid_call

from ..._http import get_json, post_json

DEFAULT_BASE_URL: str = "https://openapi.tripo3d.ai/v3"

MODEL_VERSION_DEFAULT: str = "v3.1-20260211"
# 绑骨算法版本按骨架分流：v1.0 仅适用于双足类人型并解锁 90+ 个 preset:biped:* 预设，v2.5 适用于非类人动物骨架。
MODEL_VERSION_RIG_BIPED: str = "v1.0-20240301"
MODEL_VERSION_RIG_ANIMAL: str = "v2.5-20260210"

# 骨骼命名规范，与算法版本正交。必须是 tripo：retarget 不接受 mixamo 命名的骨骼（供应商 error_code 1004）。
RIG_SPEC: str = "tripo"

# 语义键 → 该 rig 下的预设 token。每个预设单独计费，故只绑产品必需的几个。
# 键是扁平单一命名空间，同时容纳应用状态、交互反馈与 LLM 动作 token；avian 无任何预设，有意缺席。
# biped 收敛到 4 个预设以满足 Tripo `/animations/retarget` 单次 ≤ 5 预设的限制。
_RETARGET_CLIPS: dict[str, dict[str, str]] = {
    "biped": {
        "idle": "preset:biped:idle",
        "emotional": "preset:biped:laugh_01",
        "walk": "preset:biped:walk",
        "laugh": "preset:biped:laugh_01",
        "cry": "preset:biped:sob",
    },
}

# 非 biped 每类只有一个预设，全部语义键收敛到它。
_SEMANTIC_KEYS: tuple[str, ...] = ("idle", "emotional", "interacting", "poke", "drag")

_RETARGET_CLIPS.update(
    {
        rig_type: dict.fromkeys(_SEMANTIC_KEYS, preset)
        for rig_type, preset in (
            ("quadruped", "preset:quadruped:walk"),
            ("hexapod", "preset:hexapod:walk"),
            ("octopod", "preset:octopod:walk"),
            ("serpentine", "preset:serpentine:march"),
            ("aquatic", "preset:aquatic:march"),
        )
    },
)

_DOWNLOAD_TIMEOUT_SECONDS: float = 120.0

# P 系列为低面数优化并封顶 ``face_limit``；封顶保护切换到 P 系列 id 的运营人员。
_P_SERIES_FACE_LIMIT_MAX: int = 20_000


class TripoApiError(RuntimeError):
    """v3 响应包中 ``code`` 非零。"""


class TripoTaskFailed(RuntimeError):
    """轮询任务进入终态非成功状态。"""


def _base_url() -> str:
    return SETTINGS.tripo_base_url or DEFAULT_BASE_URL


def _api_key() -> str:
    key = getattr(SETTINGS, "tripo_api_key", "") or ""
    if not key:
        raise TripoApiError("TRIPO_API_KEY is not configured")
    return key


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}


def _envelope(resp: httpx.Response) -> dict[str, Any]:
    """检查 HTTP 状态再解信封：5xx / 4xx 立即抛 TripoApiError（带状态码），避免 HTML 网关页被当 JSON 解析。"""
    if resp.status_code >= 400:
        snippet = resp.text[:200]
        raise TripoApiError(f"tripo HTTP {resp.status_code}: {snippet}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise TripoApiError(f"tripo returned non-JSON body: {resp.text[:200]}") from exc
    code = payload.get("code")
    status = payload.get("status")
    if code != 0 or status != "success":
        raise TripoApiError(f"tripo code={code} status={status}: {payload.get('message')}")
    return payload.get("data") or {}


async def upload_file(file_bytes: bytes, filename: str, content_type: str = "image/jpeg") -> str:
    """POST /v3/files —— multipart 上传，返回 ``file_token``（在 image-to-model 中作为 ``input`` 使用）。"""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{_base_url()}/files",
            headers={"Authorization": f"Bearer {_api_key()}"},
            files={"file": (filename, file_bytes, content_type)},
        )
    return _envelope(resp)["file_token"]


def _common_model_kwargs(
    *,
    model_version: str,
    pbr: bool,
    texture_quality: str | None,
    face_limit: int | None,
    enable_autofix: bool | None,
    texture_alignment: str | None = None,
    orientation: str | None = None,
) -> dict[str, Any]:
    """H / M 系列端点共享的可选 Tripo3D 负载字段。``texture_alignment`` 与 ``orientation`` 是 multiview 专属 framing hints，``None`` 时不写入负载，避免单图端点收到 schema 之外的字段。"""
    payload: dict[str, Any] = {"model": model_version, "pbr": pbr}
    if texture_quality:
        payload["texture_quality"] = texture_quality
    if face_limit is not None:
        payload["face_limit"] = (
            min(face_limit, _P_SERIES_FACE_LIMIT_MAX) if model_version.startswith("P") else face_limit
        )
    if enable_autofix is not None:
        payload["enable_image_autofix"] = enable_autofix
    if model_version.startswith("v3") and SETTINGS.tripo_geometry_quality:
        payload["geometry_quality"] = SETTINGS.tripo_geometry_quality
    if texture_alignment is not None:
        payload["texture_alignment"] = texture_alignment
    if orientation is not None:
        payload["orientation"] = orientation
    return payload


def tripo_common_kwargs_from_settings(
    *,
    model_version: str | None = None,
    texture_alignment: str | None = None,
    orientation: str | None = None,
) -> dict[str, Any]:
    """从 SETTINGS 构造 Tripo 端点的通用调用 kwargs。"""
    kwargs: dict[str, Any] = {
        "model_version": model_version if model_version is not None else SETTINGS.tripo_model_version,
        "pbr": True,
        "texture_quality": SETTINGS.tripo_texture_quality,
        "face_limit": SETTINGS.tripo_face_limit or None,
        "enable_autofix": SETTINGS.tripo_enable_autofix,
    }
    if texture_alignment is not None:
        kwargs["texture_alignment"] = texture_alignment
    if orientation is not None:
        kwargs["orientation"] = orientation
    return kwargs


async def create_image_to_model(
    image_token: str,
    *,
    multiview_tokens: dict[str, str] | None = None,
    model_version: str = MODEL_VERSION_DEFAULT,
    pbr: bool = True,
    texture_quality: str | None = None,
    face_limit: int | None = None,
    enable_autofix: bool | None = None,
    texture_alignment: str | None = "original_image",
    orientation: str | None = "align_image",
) -> str:
    """提交图生 3D 任务；提供辅助视角时使用多视角端点，否则使用单图端点。"""
    if not image_token:
        raise ValueError("image-to-model requires a non-empty image_token")
    auxiliary_tokens = {view: token for view, token in (multiview_tokens or {}).items() if view != "front" and token}
    if auxiliary_tokens:
        views = {"front": image_token, **auxiliary_tokens}
        if len(views) < 2:
            raise ValueError("multiview-to-model requires at least 2 views")
        inputs = [{view: views[view]} for view in ("front", "back") if views.get(view)]
    else:
        texture_alignment = None
        orientation = None
        inputs = None
    payload = _common_model_kwargs(
        model_version=model_version,
        pbr=pbr,
        texture_quality=texture_quality,
        face_limit=face_limit,
        enable_autofix=enable_autofix,
        texture_alignment=texture_alignment,
        orientation=orientation,
    )
    if inputs is not None:
        payload["inputs"] = inputs
    else:
        payload["input"] = image_token
    endpoint = "generation/multiview-to-model" if inputs is not None else "generation/image-to-model"
    resp = await post_json(_base_url(), _auth_headers(), endpoint, payload)
    task_id = _envelope(resp)["task_id"]
    log_paid_call("tripo", "image_to_3d_submit", task_id=task_id)
    return task_id


async def get_task(task_id: str) -> dict[str, Any]:
    """单次 GET /v3/tasks/{id}；状态映射由调用方负责。"""
    resp = await get_json(_base_url(), {"Authorization": f"Bearer {_api_key()}"}, f"tasks/{task_id}")
    data = _envelope(resp)
    if data.get("status") == "success":
        log_paid_call(
            "tripo",
            "image_to_3d_result",
            task_id=task_id,
            urls=[(data.get("output") or {}).get("model_url")],
            level="debug",
        )
    return data


async def rig_check(task_id: str) -> str:
    """启动 ``animate_prerigcheck`` 任务；返回 task_id，轮询以读取 ``output.rig_type`` 与 ``output.riggable``。"""
    resp = await post_json(_base_url(), _auth_headers(), "animations/rig-check", {"input": task_id})
    return _envelope(resp)["task_id"]


async def poll_rig_check(task_id: str, *, interval: float = 2.0, timeout: float = 60.0) -> dict[str, Any]:
    """轮询 ``animate_prerigcheck`` 任务至终态，返回其 ``output`` 字典。"""
    deadline = time.monotonic() + timeout
    while True:
        data = await get_task(task_id)
        if data.get("status") == "success":
            return data.get("output") or {}
        if data.get("status") in ("failed", "cancelled", "banned"):
            raise TripoTaskFailed(f"task {task_id} reached status={data.get('status')}: {data.get('message') or data}")
        if time.monotonic() > deadline:
            raise TripoTaskFailed(f"task {task_id} did not finish within {timeout}s")
        await asyncio.sleep(interval)


def retarget_clips(rig_type: str) -> dict[str, str]:
    """该 rig 的语义键 → 预设 token 映射；空字典代表该骨架无可用预设（avian）。"""
    return _RETARGET_CLIPS.get(rig_type, {})


def rig_model_version(rig_type: str) -> str:
    return MODEL_VERSION_RIG_BIPED if rig_type == "biped" else MODEL_VERSION_RIG_ANIMAL


async def rig(task_id: str, rig_type: str) -> str:
    """对 ``task_id`` 产出的模型绑骨，返回新的 rigged task_id。"""
    resp = await post_json(
        _base_url(),
        _auth_headers(),
        "animations/rig",
        {"input": task_id, "rig_type": rig_type, "spec": RIG_SPEC, "model": rig_model_version(rig_type)},
    )
    return _envelope(resp)["task_id"]


async def retarget(task_id: str, rig_type: str) -> str:
    """把该 rig 的全部预设动画烘焙进已绑骨模型，返回新的 task_id；批量提交产出单个含多 clip 的 GLB。"""
    presets = list(dict.fromkeys(retarget_clips(rig_type).values()))
    if not presets:
        raise ValueError(f"no retarget presets for rig_type={rig_type}")
    payload = {
        "input": task_id,
        "animations": presets,
        "out_format": "glb",
        "bake_animation": True,
        "export_with_geometry": True,
    }
    resp = await post_json(_base_url(), _auth_headers(), "animations/retarget", payload)
    new_task_id = _envelope(resp)["task_id"]
    log_paid_call("tripo", "animate_bind_submit", task_id=new_task_id, urls=presets)
    return new_task_id


async def download_model(model_url: str) -> bytes:
    """Tripo 模型 URL 短期有效，需在 rig 任务成功后立即下载。"""
    return await download_capped(model_url, max_bytes=100 * 1024 * 1024, timeout=_DOWNLOAD_TIMEOUT_SECONDS)
