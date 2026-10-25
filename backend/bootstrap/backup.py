"""按备份依赖顺序登记业务贡献者，恢复编排只消费贡献契约。"""

from features.accounts.backup import backup_tables as account_tables
from features.action_library.backup import backup_tables as action_library_tables
from features.appearance.backup import backup_tables as appearance_tables
from features.assets.backup import backup_tables as asset_tables
from features.automation.backup import backup_tables as automation_tables
from features.companion.backup import backup_tables as companion_tables
from features.configuration.backup import backup_tables as configuration_tables
from features.conversation.backup import backup_tables as conversation_tables
from features.entries.backup import backup_tables as entry_tables
from features.entries.ports import set_entry_projector
from features.jobs.backup import backup_tables as job_tables
from features.journal.journal_indexes import rebuild_diary_indexes
from features.life.backup import backup_tables as life_tables
from features.media.backup import backup_tables as media_tables
from features.memory.backup import backup_tables as memory_tables
from features.posts.backup import backup_tables as post_tables
from features.scenes.backup import backup_tables as scene_tables
from features.topics.backup import backup_tables as topic_tables
from features.visuals.backup import backup_tables as visual_tables
from infrastructure.backup import BACKUP_REGISTRY


def register_backup_tables() -> None:
    set_entry_projector(rebuild_diary_indexes)
    contributions = (
        *account_tables(),
        *conversation_tables(),
        *configuration_tables(),
        *appearance_tables(),
        *asset_tables(),
        *job_tables(),
        *action_library_tables(),
        *scene_tables(),
        *visual_tables(),
        *companion_tables(),
        *automation_tables(),
        *memory_tables(),
        *media_tables(),
        *post_tables(),
        *entry_tables(),
        *life_tables(),
        *topic_tables(),
    )
    BACKUP_REGISTRY.configure(contributions)
