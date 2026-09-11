from .camofox import is_camofox_mode
from .engine import find_browser_binary
from .session import _get_cdp_override


def check_browser_native_requirements() -> bool:
    """Camofox > cdp_url override > 本地已装 Edge/Chrome/Brave/Chromium 任一可用即可。"""
    if is_camofox_mode():
        return True
    if bool(_get_cdp_override()):
        return True
    return find_browser_binary() is not None
