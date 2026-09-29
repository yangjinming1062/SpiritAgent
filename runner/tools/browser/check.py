from utils import cfg_get, load_config

from .camofox import is_camofox_mode
from .engine import find_browser_binary


def check_browser_native_requirements() -> bool:
    """Camofox > cdp_url override > 本地已装 Edge/Chrome/Brave/Chromium 任一可用即可。

    只看 ``cdp_url`` 是否配置，不做端点发现：每个浏览器工具都会调用本探测，连接失败由实际调用报告。
    """
    if is_camofox_mode():
        return True
    if str(cfg_get(load_config(), "browser", "cdp_url", default="")).strip():
        return True
    return find_browser_binary() is not None
