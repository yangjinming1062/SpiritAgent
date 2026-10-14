from spirit_agent_runner.contracts.config import cfg_get, load_config
from spirit_agent_runner.features.browser.camofox import is_camofox_mode
from spirit_agent_runner.features.browser.engine.launcher import find_browser_binary


def check_browser_native_requirements() -> bool:
    """cdp_url > Camofox > 本地浏览器任一可用；只查配置不做端点发现。"""
    if is_camofox_mode():
        return True
    if str(cfg_get(load_config(), "browser", "cdp_url", default="")).strip():
        return True
    return find_browser_binary() is not None


def check_browser_cdp_requirements() -> bool:
    """CDP-only browser tools are unavailable when the active backend is Camofox."""
    return check_browser_native_requirements() and not is_camofox_mode()
