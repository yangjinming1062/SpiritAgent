"""通过导入登记完整 ORM，供应用与 Alembic 装配共用。"""

import features.accounts.models
import features.accounts.remote.models
import features.action_library.models
import features.appearance.character_card_models
import features.appearance.models
import features.assets.models
import features.automation.models
import features.companion.intent_models
import features.companion.models
import features.configuration.models
import features.configuration.settings.models
import features.conversation.models
import features.conversation.submission_models
import features.journal.models
import features.media.models
import features.media.video_queue.models
import features.media.voice_models
import features.memory.models
import features.posts.models
import features.scenes.models
import features.topics.models
import features.updates.models  # noqa: F401 -- 显式 ORM 登记。
import features.visuals.models  # noqa: F401 -- 显式 ORM 登记。
import runtime.events.models  # noqa: F401 -- 显式 ORM 登记。
import workflows.nightly.models  # noqa: F401 -- 显式 ORM 登记。
