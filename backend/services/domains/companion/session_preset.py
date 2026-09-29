_WORK_PRESETS: frozenset[str] = frozenset({"developer", "product_manager", "copywriter", "language_teacher"})


def is_work_preset(preset_id: str | None) -> bool:
    return preset_id in _WORK_PRESETS
