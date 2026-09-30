from utils import cfg_get, load_config

from .camofox import is_camofox_mode
from .engine import find_browser_binary


def check_browser_native_requirements() -> bool:
    """Camofox > cdp_url > 本地浏览器任一可用；只查配置不做端点发现。"""
    if is_camofox_mode():
        return True
    if str(cfg_get(load_config(), "browser", "cdp_url", default="")).strip():
        return True
    return find_browser_binary() is not None
