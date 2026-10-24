"""按备份依赖顺序登记业务贡献者，恢复编排只消费贡献契约。"""

from features.accounts.backup import backup_tables as account_tables
from features.action_library.backup import backup_tables as action_library_tables
from features.appearance.backup import backup_tables as appearance_tables
from features.automation.backup import backup_tables as automation_tables
from features.companion.backup import backup_tables as companion_tables
from features.configuration.backup import backup_tables as configuration_tables
from features.conversation.backup import backup_tables as conversation_tables
from features.journal.backup import backup_tables as journal_tables
from features.life.backup import backup_tables as life_tables
from features.media.backup import backup_tables as media_tables
from features.memory.backup import backup_tables as memory_tables
from features.posts.backup import backup_tables as post_tables
from features.scenes.backup import backup_tables as scene_tables
from features.topics.backup import backup_tables as topic_tables
from features.visuals.backup import backup_tables as visual_tables
from infrastructure.backup import BACKUP_REGISTRY, BACKUP_TABLE_ORDER


def register_backup_tables() -> None:
    contributions = (
        *account_tables(),
        *conversation_tables(),
        *configuration_tables(),
        *appearance_tables(),
        *action_library_tables(),
        *scene_tables(),
        *visual_tables(),
        *companion_tables(),
        *automation_tables(),
        *memory_tables(),
        *media_tables(),
        *post_tables(),
        *journal_tables(),
        *life_tables(),
        *topic_tables(),
    )
    tables = {table.name: table for table in contributions}
    if len(tables) != len(contributions) or set(tables) != set(BACKUP_TABLE_ORDER):
        raise ValueError("backup contributions must match the declared table order")
    BACKUP_REGISTRY.configure(tables[name] for name in BACKUP_TABLE_ORDER)
