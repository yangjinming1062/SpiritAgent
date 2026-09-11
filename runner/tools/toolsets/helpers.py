from utils import get_disabled_config_names


def get_disabled_toolset_ids() -> set[str]:
    """从内存中的 config 读取 ``toolsets.disabled`` 列表; ``config-writer.cjs`` 的原子写锁通过把 skills/toolsets 放进相邻 YAML 段来序列化两次写入。"""
    return get_disabled_config_names(section="toolsets")
