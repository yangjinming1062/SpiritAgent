import asyncio
import base64
import contextlib
import functools
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from utils import cfg_get, get_spiritagent_home, is_env_passthrough, load_config, safe_schedule_threadsafe

from .cu_backend import DESKTOP_SENTINELS, ActionResult, CaptureResult, ComputerUseBackend, UIElement

logger = logging.getLogger(__name__)

_CUA_DRIVER_CMD = cfg_get(load_config(), "computer_use", "cua_driver_cmd", default="cua-driver")
_CUA_DRIVER_ARGS = ["mcp"]

# app= 的哨兵值集中定义在 cu_backend.DESKTOP_SENTINELS，防止 macOS 与 Windows 后端产生分歧
_MACOS_SHELL_APP_NAMES = frozenset({"finder", "dock"})

# cua-driver（Rust 二进制）启动时需要的变量，用于查找本地依赖和呈现合理的进程身份。
# 不在此列表的变量都会从子进程 env 中剔除，避免 Desktop JWT / Backend URL / safeStorage 密文泄漏到 cua-driver 进程树中。
#
# 拆分为精确匹配（单个变量名）和前缀匹配（变量族），让每条都明确是名还是命名空间
_CUA_DRIVER_SAFE_ENV_EXACT = frozenset(
    {
        # 路径 / 身份 / locale / shell
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LANGUAGE",
        "TERM",
        "TMPDIR",
        "TMP",
        "TEMP",
        # X11 / Wayland 显示服务提示
        "DISPLAY",
        "QT5",
        "QT6",
    },
)
_CUA_DRIVER_SAFE_ENV_PREFIXES = (
    # locale、XDG / freedesktop
    "LC_",
    "XDG_",
    # GUI 平台环境（Qt / SDL / EGL / GL — capture 管线需要绑定到显示面）
    "GTK_",
    "QT_",
    "SDL_",
    "EGL_",
    "GL_",
    # macOS 共享库路径
    "DYLD_",
    "LD_",
    "XKB_",
    "XKB_DEFAULT_",
    "KEYBOARD",
    # 输入法（fcitx / ibus / scim）— 没有这些，非 ASCII 布局的字符会输入错误
    "INPUT_",
    "IM_",
    "QT_IM_MODULE",
    "GTK_IM_MODULE",
    "XMODIFIERS",
)
_CUA_DRIVER_SECRET_SUBSTRINGS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
    "PASSWD",
    "JWT",
    "WEBHOOK",
    "API_KEY",
    "PRIVATE_KEY",
    "ACCESS_KEY",
    "SECRET_KEY",
)
# 弱密钥子串的判定已内联到 _is_cua_secret_var（仅 _KEY(?:_|$) 命中，去除原
# (?:^|_)(?:KEY|AUTH)(?:_|$) 的误杀 OAUTH_CLIENT_ID / GIT_AUTHOR_NAME 等合法变量）。
# 对已知公开标识符的显式非密钥覆盖，否则会被前缀白名单误杀。按变量名精确匹配
_CUA_DRIVER_PUBLIC_OVERRIDES: frozenset[str] = frozenset(
    {
        "OAUTH_CLIENT_ID",
        "OAUTH_ISSUER",
        "OAUTH_AUTHORIZE_URL",
        "OAUTH_TOKEN_URL",
        "OAUTH_USER_INFO_URL",
        "OAUTH_REDIRECT_URI",
        "AUTHORITY",
        "AUTHORITY_URL",
    },
)
# 名字不包含上述子串但仍然敏感的变量精确丢弃列表（例如 SPIRITAGENT_JWT 包含 "JWT" 已能被捕获；
# SPIRITAGENT_DESKTOP_TOKEN 包含 "TOKEN" 也能被捕获 — 此处保留作为双保险锚点）
_CUA_DRIVER_DROP_EXACT: frozenset[str] = frozenset()


def _is_cua_secret_var(name: str) -> bool:
    """判定环境变量是否包含不应传给 cua-driver 子进程的凭据。

    强子串优先（TOKEN / SECRET / PASSWORD 等）。弱匹配只在末尾带 _KEY 时
    触发（STRIPE_KEY / OPENAI_API_KEY 等）。原 (?:^|_)(?:KEY|AUTH)(?:_|$)
    会误杀 OAUTH_CLIENT_ID、GIT_AUTHOR_NAME、KEYBOARD_LAYOUT 等。
    """
    upper = name.upper()
    if any(s in upper for s in _CUA_DRIVER_SECRET_SUBSTRINGS):
        return True
    return bool(re.search(r"_KEY(?:_|$)", upper))


_WINDOW_LINE_RE = re.compile(r"^-\s+(.+?)\s+\(pid\s+(\d+)\)\s+.*\[window_id:\s+(\d+)\]", re.MULTILINE)
_ELEMENT_LINE_RE = re.compile(
    r'^\s*(?:-\s+)?\[(\d+)\]\s+(\w+)(?:\s+"([^"]*)"|(?:\s+\(\d+\))?\s+id=([^\s\[\]]*))?',
    re.MULTILINE,
)


def _cua_driver_command() -> str:
    """解析 cua-driver 可执行命令：config 显式路径 > $SPIRITAGENT_HOME/bin > wheel 内置二进制 > PATH 裸名。

    cua-driver 以 PyPI wheel（``cua-driver``，runner 的平台标记依赖）随 venv 安装，
    二进制位于 ``site-packages/cua_driver/bin/``；``$SPIRITAGENT_HOME/bin/`` 是用户
    手动安装的兜底位置。两者都返回绝对路径让 MCP stdio 子进程直接 spawn。
    """
    cmd = _CUA_DRIVER_CMD
    if os.sep in cmd or (os.altsep and os.altsep in cmd):
        return cmd
    exe = f"{cmd}{'.exe' if sys.platform == 'win32' else ''}"
    managed = get_spiritagent_home() / "bin" / exe
    if managed.is_file():
        return str(managed)
    try:
        import cua_driver

        wheel_bin = Path(cua_driver.__file__).parent / "bin" / exe
        if wheel_bin.is_file():
            return str(wheel_bin)
    except ImportError:
        pass
    return cmd


@functools.lru_cache(maxsize=1)
def cua_driver_binary_available() -> bool:
    """当存在可用的 cua-driver 二进制且与宿主架构匹配时返回 True。

    查找顺序见 ``_cua_driver_command``（config 路径 > $SPIRITAGENT_HOME/bin > PATH）。
    单纯的 shutil.which 会接受一份为不同 OS / arch 构建的副本（例如 macOS 二进制被 scp 到 Windows 主机），
    仅在 exec 时以一个晦涩的加载器错误失败 — 一次 subprocess.run([cmd, '--version']) 可尽早暴露这种错配。
    结果在进程生命周期内缓存（cu-driver 安装状态在运行时是静态的）；每次 handle_computer_use 调用可避免最多 3 次子进程派生。
    """
    command = _cua_driver_command()
    path = shutil.which(command)
    if not path:
        return False
    try:
        result = subprocess.run([command, "--version"], capture_output=True, timeout=3, check=False)
    except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def cua_driver_install_hint() -> str:
    return (
        "cua-driver is not importable. It ships as the `cua-driver` PyPI dependency of the "
        "runner wheel (platform marker win32/darwin) — a normal runner install already "
        "bundles it. If you see this, the runner venv is broken: reinstall the runner. "
        "Dev fallback: `uv pip install cua-driver==0.21.0` into the runner venv."
    )


def _build_cua_driver_env() -> dict[str, str]:
    """返回 cua-driver 子进程的净化环境变量。

    按前缀白名单（PATH / HOME / DYLD_* / LD_* / locale / tmp / GUI 显示）保留变量，
    丢弃名字包含密钥子串（TOKEN / JWT / SECRET / ...）的变量，
    另外尊重通过 register_env_passthrough(...) 注册的额外变量 — 这是调用方开启 cua-driver 运行时所需变量的入口。

    与旧实现的 os.environ.copy() 相比，此实现可让 Desktop 的 JWT、Backend base URL 以及任何 safeStorage 密文
    都不会泄漏到 cua-driver 进程树中 — macOS 上不会通过 ps -E 泄漏（Windows 上 cua-driver 也不支持 wmic process 查询）。

    弱密钥子串（KEY、AUTH）只在词边界匹配，因此 KEYBOARD_LAYOUT / XKB_KEYMAP / OAUTH_CLIENT_ID 得以保留，
    而 STRIPE_KEY / API_KEY / AUTH_HEADER 会被丢弃。

    同时根据 computer_use.cua_telemetry 配置项注入 CUA_DRIVER_RS_TELEMETRY_ENABLED — 失败安全默认关闭。
    """

    def _is_secret_var(name: str) -> bool:
        return _is_cua_secret_var(name)

    safe: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in _CUA_DRIVER_DROP_EXACT:
            continue
        if key in _CUA_DRIVER_PUBLIC_OVERRIDES:
            safe[key] = value
            continue
        if _is_secret_var(key):
            continue
        upper = key.upper()
        if upper in _CUA_DRIVER_SAFE_ENV_EXACT or any(upper.startswith(p) for p in _CUA_DRIVER_SAFE_ENV_PREFIXES):
            safe[key] = value
            continue
        if is_env_passthrough(key):
            safe[key] = value

    telemetry_enabled = bool(cfg_get(load_config(), "computer_use", "cua_telemetry", default=False))
    safe["CUA_DRIVER_RS_TELEMETRY_ENABLED"] = "1" if telemetry_enabled else "0"
    return safe


def _parse_windows_from_text(text: str) -> list[dict[str, Any]]:
    return [
        {"app_name": m[1].strip(), "pid": int(m[2]), "window_id": int(m[3]), "off_screen": "[off-screen]" in m[0]}
        for m in _WINDOW_LINE_RE.finditer(text)
    ]


def _parse_elements_from_structured(raw_elements: list[dict[str, Any]]) -> list[UIElement]:
    elements: list[UIElement] = []
    for raw in raw_elements:
        if not isinstance(raw, dict):
            continue
        idx = raw.get("element_index")
        if not isinstance(idx, int):
            continue
        role = str(raw.get("role", ""))
        label = str(raw.get("label", ""))
        frame = raw.get("frame")
        bounds = (0, 0, 0, 0)
        if isinstance(frame, dict):
            with contextlib.suppress(TypeError, ValueError):
                bounds = (
                    int(frame.get("x", 0)),
                    int(frame.get("y", 0)),
                    int(frame.get("w", 0)),
                    int(frame.get("h", 0)),
                )
        raw_token = raw.get("element_token")
        token = raw_token if isinstance(raw_token, str) and raw_token else None
        elements.append(UIElement(index=idx, role=role, label=label, bounds=bounds, element_token=token))
    return elements


def _parse_elements_from_tree(markdown: str) -> list[UIElement]:
    return [
        UIElement(index=int(m[1]), role=m[2], label=m[3] or m[4] or "", bounds=(0, 0, 0, 0))
        for m in _ELEMENT_LINE_RE.finditer(markdown)
    ]


def _image_dimensions_from_bytes(raw: bytes) -> tuple[int, int]:
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        w, h = int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
        if w > 0 and h > 0:
            return w, h
    if raw.startswith(b"\xff\xd8"):
        i, n = 2, len(raw)
        while i + 9 < n:
            if raw[i] != 0xFF:
                i += 1
                continue
            marker, i = raw[i + 1], i + 2
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                continue
            if i + 2 > n or (seg_len := int.from_bytes(raw[i : i + 2], "big")) < 2 or i + seg_len > n:
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if (
                    seg_len >= 7
                    and (w := int.from_bytes(raw[i + 5 : i + 7], "big")) > 0
                    and (h := int.from_bytes(raw[i + 3 : i + 5], "big")) > 0
                ):
                    return w, h
                break
            i += seg_len
    return 0, 0


def _split_tree_text(full_text: str) -> tuple[str, str]:
    parts = full_text.split("\n", 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def _parse_key_combo(keys: str) -> tuple[str | None, list[str]]:
    MODIFIER_NAMES = {"cmd", "command", "shift", "option", "alt", "ctrl", "control", "fn"}
    KEY_ALIASES = {"command": "cmd", "alt": "option", "control": "ctrl"}
    parts = [p.strip().lower() for p in re.split(r"[+\-]", keys) if p.strip()]
    modifiers = [KEY_ALIASES.get(p, p) for p in parts if KEY_ALIASES.get(p, p) in MODIFIER_NAMES]
    key = next((p for p in reversed(parts) if KEY_ALIASES.get(p, p) not in MODIFIER_NAMES), None)
    return key, modifiers


class _AsyncBridge:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()

        def _run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._ready.set()
            try:
                self._loop.run_forever()
            finally:
                with contextlib.suppress(Exception):
                    self._loop.close()

        self._thread = threading.Thread(target=_run, daemon=True, name="cua-driver-loop")
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("cua-driver asyncio bridge failed to start")

    def run(self, coro: Any, timeout: float | None = 30.0) -> Any:
        if not self._loop or not self._thread or not self._thread.is_alive():
            if asyncio.iscoroutine(coro):
                coro.close()
            raise RuntimeError("cua-driver bridge not started")
        if (fut := safe_schedule_threadsafe(coro, self._loop)) is None:
            raise RuntimeError("cua-driver bridge not started")
        return fut.result(timeout=timeout)

    def stop(self) -> None:
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = self._loop = None


class _CuaDriverSession:
    def __init__(self, bridge: _AsyncBridge) -> None:
        self._bridge = bridge
        self._session = None
        self._exit_stack = None
        self._lock = threading.Lock()
        self._started = False

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("cua-driver session not started")

    async def _aenter(self) -> None:
        if not cua_driver_binary_available():
            raise RuntimeError(cua_driver_install_hint())
        params = StdioServerParameters(
            command=_cua_driver_command(),
            args=_CUA_DRIVER_ARGS,
            env=_build_cua_driver_env(),
        )
        stack = AsyncExitStack()
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._exit_stack, self._session = stack, session

    async def _aexit(self) -> None:
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except Exception as e:
                logger.warning("cua-driver shutdown error: %s", e)
        self._exit_stack = self._session = None

    def start(self) -> None:
        with self._lock:
            if not self._started:
                self._bridge.start()
                self._bridge.run(self._aenter(), timeout=15.0)
                self._started = True

    def stop(self) -> None:
        with self._lock:
            if self._started:
                try:
                    self._bridge.run(self._aexit(), timeout=5.0)
                finally:
                    self._started = False

    async def _call_tool_async(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return _extract_tool_result(await self._session.call_tool(name, args))

    @staticmethod
    def _is_closed_session_error(exc: Exception) -> bool:
        name = exc.__class__.__name__
        return (
            name in {"ClosedResourceError", "BrokenResourceError", "EndOfStream"}
            or ("anyio" in getattr(exc.__class__, "__module__", "") and "Resource" in name)
            or isinstance(exc, BrokenPipeError | EOFError)
        )

    def _restart_session_locked(self) -> None:
        try:
            if self._started:
                self._bridge.run(self._aexit(), timeout=5.0)
        except Exception as e:
            logger.debug("cua-driver session cleanup before reconnect failed: %s", e)
        self._started = False
        self._bridge.run(self._aenter(), timeout=15.0)
        self._started = True

    def call_tool(self, name: str, args: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        self._require_started()
        try:
            return self._bridge.run(self._call_tool_async(name, args), timeout=timeout)
        except Exception as e:
            if not self._is_closed_session_error(e):
                raise
            logger.warning("cua-driver MCP session closed during %s; reconnecting once", name)
            with self._lock:
                self._restart_session_locked()
            return self._bridge.run(self._call_tool_async(name, args), timeout=timeout)


def _extract_first_image(out: dict[str, Any]) -> tuple[str | None, str | None]:
    """从 call_tool 结果 dict 中提取第一张图片及其 MIME。

    vision / som / zoom capture 路径共用，保证"cua-driver 未返回 image_mime_types"的兜底逻辑只在一处。
    响应中没有图片部分时返回 (None, None)。
    """
    images = out.get("images") or []
    if not images:
        return None, None
    mimes = out.get("image_mime_types") or []
    return images[0], mimes[0] if mimes else None


def _extract_tool_result(mcp_result: Any) -> dict[str, Any]:
    content = getattr(mcp_result, "content", []) or []
    # cua-driver 0.5.x+ 在每个图片部分上都会带 mimeType；旧版本只设置 data。
    # 保留线协议声明的 MIME，让下游可以省去 base64 magic-byte sniff 兜底路径。
    #
    # 单遍：并行构建 (data, mime_type) 元组，保证当未来 cua-driver 在图片之间插入非图片部分时，
    # 两个并行列表不会错位。
    image_parts: list[tuple[str, str | None]] = []
    text_chunks: list[str] = []
    for part in content:
        ptype = getattr(part, "type", None)
        if ptype == "image" and getattr(part, "data", None):
            image_parts.append((part.data, getattr(part, "mimeType", None)))
        elif ptype == "text" and getattr(part, "text", ""):
            text_chunks.append(part.text)
    images = [d for d, _ in image_parts]
    image_mime_types = [m for _, m in image_parts]
    data = None
    if text_chunks:
        joined = "\n".join(text_chunks)
        try:
            data = json.loads(joined) if joined.strip().startswith(("{", "[")) else joined
        except json.JSONDecodeError:
            data = joined
    return {
        "data": data,
        "images": images,
        "image_mime_types": image_mime_types,
        "structuredContent": getattr(mcp_result, "structuredContent", None),
        "isError": bool(getattr(mcp_result, "isError", False)),
    }


class CuaDriverBackend(ComputerUseBackend):
    def __init__(self) -> None:
        self._bridge = _AsyncBridge()
        self._session = _CuaDriverSession(self._bridge)
        # ``_state_lock`` 守住 ``_active_pid`` / ``_active_window_id`` / ``_last_app`` 三个字段的并发读写;
        # 之前没有任何锁, 并发 ``computer_use`` 调用会让 ``click`` 落到错的进程。
        self._state_lock = threading.RLock()
        self._active_pid = None
        self._active_window_id = None
        self._last_app = None

    def _active_target(self) -> tuple[int | None, int | None]:
        """锁内原子读取 ``_active_pid`` 与 ``_active_window_id``, 防 click/type 期间被 capture 改写。"""
        with self._state_lock:
            return self._active_pid, self._active_window_id

    def start(self) -> None:
        self._session.start()

    def stop(self) -> None:
        try:
            self._session.stop()
        finally:
            self._bridge.stop()

    def is_available(self) -> bool:
        # cua-driver 仅发布 darwin 与 win32 版本。我们仅检查 cua-driver 在 PATH 上且未运行在不支持的宿主。
        if not cua_driver_binary_available():
            return False
        return sys.platform in {"darwin", "win32"}

    def capture(self, mode: str = "som", app: str | None = None) -> CaptureResult:
        lw_out = self._session.call_tool("list_windows", {"on_screen_only": True})
        if raw_windows := (lw_out.get("structuredContent") or {}).get("windows"):
            windows = sorted(
                [
                    {
                        "app_name": w.get("app_name", ""),
                        "pid": int(w["pid"]),
                        "window_id": int(w["window_id"]),
                        "off_screen": not w.get("is_on_screen", True),
                        "title": w.get("title", ""),
                        "z_index": w.get("z_index", 0),
                    }
                    for w in raw_windows
                ],
                key=lambda w: w["z_index"],
            )
        else:
            windows = _parse_windows_from_text(lw_out["data"] if isinstance(lw_out["data"], str) else "")

        if not windows:
            return CaptureResult(mode=mode, width=0, height=0)

        if app and app.lower() in DESKTOP_SENTINELS:
            shell = [w for w in windows if w["app_name"].lower() in _MACOS_SHELL_APP_NAMES]
            if not shell:
                return CaptureResult(
                    mode=mode,
                    width=0,
                    height=0,
                    window_title=f"<no shell window found; sentinel app={app!r} requires Finder/Dock to be visible on the active Space>",
                )
            # 哨兵命中 — 使用 shell 窗口列表并跳过下面的子串过滤。
            # 否则后续 `if app:` 会丢弃 shell 窗口（因为 'desktop' 不是 'finder'/'dock' 的子串）。
            windows = shell
            app = None

        if app:
            app_lower = app.lower()
            if not (windows := [w for w in windows if app_lower in w["app_name"].lower()]):
                return CaptureResult(
                    mode=mode,
                    width=0,
                    height=0,
                    window_title=f"<no on-screen window matched app={app!r}; call list_apps to see available app names (macOS reports localized names, e.g. '計算機' instead of 'Calculator')>",
                )

        target = next((w for w in windows if not w.get("off_screen", False)), windows[0])
        with self._state_lock:
            self._active_pid, self._active_window_id, app_name = (
                target["pid"],
                target["window_id"],
                target["app_name"],
            )
            if app or not self._last_app:
                self._last_app = app_name

        png_b64, elements, width, height, window_title, image_mime_type = (None, [], 0, 0, "", None)
        if mode == "vision":
            sc_out = self._session.call_tool(
                "screenshot",
                {"window_id": self._active_window_id, "format": "jpeg", "quality": 85},
            )
            png_b64, image_mime_type = _extract_first_image(sc_out)
        else:
            gws_out = self._session.call_tool(
                "get_window_state",
                {"pid": self._active_pid, "window_id": self._active_window_id},
            )
            gws_struct = gws_out.get("structuredContent") or {}
            gws_data = gws_out["data"] if isinstance(gws_out.get("data"), str) else ""
            if raw_elements := gws_struct.get("elements"):
                elements = _parse_elements_from_structured(raw_elements)
            else:
                _, tree = _split_tree_text(gws_data)
                if tree:
                    elements = _parse_elements_from_tree(tree)
            png_b64, image_mime_type = _extract_first_image(gws_out)
            _, tree = _split_tree_text(gws_data)
            if tree and (wt := re.search(r'AXWindow\s+"([^"]+)"', tree)):
                window_title = wt.group(1)

        png_bytes_len = 0
        if png_b64:
            try:
                raw = base64.b64decode(png_b64, validate=False)
                png_bytes_len = len(raw)
                if (dims := _image_dimensions_from_bytes(raw)) != (0, 0):
                    width, height = dims
            except Exception:
                png_bytes_len = len(png_b64) * 3 // 4

        return CaptureResult(
            mode=mode,
            width=width,
            height=height,
            png_b64=png_b64,
            elements=elements,
            app=app_name,
            window_title=window_title,
            png_bytes_len=png_bytes_len,
            image_mime_type=image_mime_type,
        )

    def click(
        self,
        *,
        element: int | None = None,
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
        click_count: int = 1,
        modifiers: list[str] | None = None,
    ) -> ActionResult:
        pid, window_id = self._active_target()
        if pid is None:
            return ActionResult(ok=False, action="click", message="No active window — call capture() first.")
        tool = "right_click" if button == "right" else "double_click" if click_count == 2 else "click"
        args = {"pid": pid}
        if element is not None:
            if window_id is None:
                return ActionResult(ok=False, action=tool, message="No active window_id for element_index click.")
            args |= {"element_index": element, "window_id": window_id}
        elif x is not None and y is not None:
            args |= {"x": x, "y": y}
        else:
            return ActionResult(ok=False, action=tool, message="click requires element= or x/y.")
        if modifiers:
            args["modifier"] = modifiers
        return self._action(tool, args)

    def drag(
        self,
        *,
        from_element: int | None = None,
        to_element: int | None = None,
        from_xy: tuple[int, int] | None = None,
        to_xy: tuple[int, int] | None = None,
        button: str = "left",
        modifiers: list[str] | None = None,
    ) -> ActionResult:
        pid, window_id = self._active_target()
        if pid is None:
            return ActionResult(ok=False, action="drag", message="No active window — call capture() first.")
        args = {"pid": pid}
        if from_element is not None and to_element is not None:
            if window_id is None:
                return ActionResult(ok=False, action="drag", message="No active window_id for element-based drag.")
            args |= {"from_element": from_element, "to_element": to_element, "window_id": window_id}
        elif from_xy is not None and to_xy is not None:
            args |= {"from_x": int(from_xy[0]), "from_y": int(from_xy[1]), "to_x": int(to_xy[0]), "to_y": int(to_xy[1])}
        else:
            return ActionResult(
                ok=False,
                action="drag",
                message="drag requires from_element/to_element or from_coordinate/to_coordinate.",
            )
        return self._action("drag", args)

    def scroll(
        self,
        *,
        direction: str,
        amount: int = 3,
        element: int | None = None,
        x: int | None = None,
        y: int | None = None,
        modifiers: list[str] | None = None,
    ) -> ActionResult:
        pid, window_id = self._active_target()
        if pid is None:
            return ActionResult(ok=False, action="scroll", message="No active window — call capture() first.")
        args = {"pid": pid, "direction": direction, "amount": max(1, min(50, amount))}
        if element is not None and window_id is not None:
            args |= {"element_index": element, "window_id": window_id}
        elif x is not None and y is not None:
            args |= {"x": x, "y": y}
        return self._action("scroll", args)

    def type_text(self, text: str) -> ActionResult:
        pid, _ = self._active_target()
        if pid is None:
            return ActionResult(ok=False, action="type_text", message="No active window — call capture() first.")
        return self._action("type_text", {"pid": pid, "text": text})

    def key(self, keys: str) -> ActionResult:
        pid, _ = self._active_target()
        if pid is None:
            return ActionResult(ok=False, action="key", message="No active window — call capture() first.")
        key_name, modifiers = _parse_key_combo(keys)
        if not key_name:
            return ActionResult(ok=False, action="key", message=f"Could not parse key from '{keys}'.")
        res = (
            self._action("hotkey", {"pid": pid, "keys": [*modifiers, key_name]})
            if modifiers
            else self._action("press_key", {"pid": pid, "key": key_name})
        )
        res.action = "key"
        return res

    def set_value(self, value: str, element: int | None = None) -> ActionResult:
        with self._state_lock:
            pid, window_id = self._active_pid, self._active_window_id
        if pid is None or window_id is None:
            return ActionResult(ok=False, action="set_value", message="No active window — call capture() first.")
        if element is None:
            return ActionResult(ok=False, action="set_value", message="set_value requires element= (element index).")
        return self._action("set_value", {"pid": pid, "window_id": window_id, "element_index": element, "value": value})

    def zoom(
        self,
        window_id: int,
        x: int,
        y: int,
        w: int,
        h: int,
        factor: float = 2.0,
        fmt: str = "jpeg",
        quality: int = 85,
    ) -> dict[str, Any]:
        """Python 内部辅助（不暴露到工具 schema）：截取窗口子区域并可选上采样，用于在不消耗整屏 token 的前提下 OCR / 检查密集 UI 区域。返回 {image_b64, mime_type, width, height}，width/height 为上采样后的像素。"""
        out = self._session.call_tool(
            "zoom",
            {
                "window_id": window_id,
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "factor": factor,
                "format": fmt,
                "quality": quality,
            },
        )
        image_b64, mime_type = _extract_first_image(out)
        return {
            "image_b64": image_b64,
            "mime_type": mime_type,
            "width": int((w or 0) * (factor or 0)),
            "height": int((h or 0) * (factor or 0)),
        }

    def list_apps(self) -> list[dict[str, Any]]:
        data = self._session.call_tool("list_apps", {}).get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("apps", [])
        if isinstance(data, str):
            return [
                {"name": m.group(1).strip(), "pid": int(m.group(2))}
                for line in data.splitlines()
                if (m := re.search(r"(.+?)\s+\(pid\s+(\d+)\)", line))
            ]
        return []

    def focus_app(self, app: str, raise_window: bool = False) -> ActionResult:
        lw_out = self._session.call_tool("list_windows", {"on_screen_only": True})
        if raw_windows := (lw_out.get("structuredContent") or {}).get("windows"):
            windows = sorted(
                [
                    {
                        "app_name": w.get("app_name", ""),
                        "pid": int(w["pid"]),
                        "window_id": int(w["window_id"]),
                        "z_index": w.get("z_index", 0),
                    }
                    for w in raw_windows
                ],
                key=lambda w: w["z_index"],
            )
        else:
            windows = _parse_windows_from_text(lw_out["data"] if isinstance(lw_out["data"], str) else "")

        app_lower = app.lower()
        if matched := [w for w in windows if app_lower in w["app_name"].lower()]:
            target = matched[0]
            with self._state_lock:
                self._active_pid, self._active_window_id, self._last_app = (
                    target["pid"],
                    target["window_id"],
                    target["app_name"],
                )
            return ActionResult(
                ok=True,
                action="focus_app",
                message=f"Targeted {target['app_name']} (pid {self._active_pid}, window {self._active_window_id}) without raising window.",
            )
        return ActionResult(ok=False, action="focus_app", message=f"No on-screen window found for app '{app}'.")

    def _action(self, name: str, args: dict[str, Any]) -> ActionResult:
        try:
            out = self._session.call_tool(name, args)
        except Exception as e:
            logger.exception("cua-driver %s call failed", name)
            return ActionResult(ok=False, action=name, message=f"cua-driver error: {e}")
        data = out["data"]
        message = str(data.get("message", "")) if isinstance(data, dict) else str(data) if isinstance(data, str) else ""
        return ActionResult(
            ok=not out["isError"],
            action=name,
            message=message,
            meta=data if isinstance(data, dict) else {},
        )
