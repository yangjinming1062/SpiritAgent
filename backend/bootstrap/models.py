"""Explicit ORM registration for application and Alembic composition."""

import features.accounts.models  # noqa: F401 -- 显式 ORM 登记。
import features.accounts.remote.models  # noqa: F401 -- 显式 ORM 登记。
import features.actions.models  # noqa: F401 -- 显式 ORM 登记。
import features.appearance.character_card_models  # noqa: F401 -- 显式 ORM 登记。
import features.appearance.models  # noqa: F401 -- 显式 ORM 登记。
import features.assets.models  # noqa: F401 -- 显式 ORM 登记。
import features.automation.models  # noqa: F401 -- 显式 ORM 登记。
import features.companion.intent_models  # noqa: F401 -- 显式 ORM 登记。
import features.companion.models  # noqa: F401 -- 显式 ORM 登记。
import features.configuration.models  # noqa: F401 -- 显式 ORM 登记。
import features.configuration.settings.models  # noqa: F401 -- 显式 ORM 登记。
import features.conversation.models  # noqa: F401 -- 显式 ORM 登记。
import features.conversation.submission_models  # noqa: F401 -- 显式 ORM 登记。
import features.desktop_life.models  # noqa: F401 -- 显式 ORM 登记。
import features.journal.models  # noqa: F401 -- 显式 ORM 登记。
import features.media.models  # noqa: F401 -- 显式 ORM 登记。
import features.media.review_models  # noqa: F401 -- 显式 ORM 登记。
import features.memory.models  # noqa: F401 -- 显式 ORM 登记。
import features.posts.models  # noqa: F401 -- 显式 ORM 登记。
import features.scenes.models  # noqa: F401 -- 显式 ORM 登记。
import features.updates.models  # noqa: F401 -- 显式 ORM 登记。
import runtime.events.models  # noqa: F401 -- 显式 ORM 登记。
import workflows.nightly.models  # noqa: F401 -- 显式 ORM 登记。
