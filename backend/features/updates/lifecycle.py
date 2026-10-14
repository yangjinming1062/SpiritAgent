"""更新存储在业务恢复前收敛未完成上传。"""

from runtime.lifecycle import RuntimeParticipant

from .releases import recover_update_storage


def runtime_participants() -> tuple[RuntimeParticipant, ...]:
    return (RuntimeParticipant("updates.storage", prepare=recover_update_storage),)
