from utils import get_disabled_config_names


def get_disabled_toolset_ids() -> set[str]:
    """从内存配置读取 ``toolsets.disabled``。"""
    return get_disabled_config_names(section="toolsets")
