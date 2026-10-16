"""Explicit HTTP resource registration; URL prefixes belong to each router."""

from .admin import router as admin_router
from .companion import router as companion_router
from .companion_actions import router as companion_actions_router
from .companion_desktop_videos import router as companion_desktop_videos_router
from .companion_journal import router as companion_journal_router
from .companion_posts import router as companion_posts_router
from .companion_scenes import router as companion_scenes_router
from .companion_topics import router as companion_topics_router
from .config import router as config_router
from .gateway import router as gateway_router
from .media import router as media_router
from .onboarding import router as onboarding_router
from .page import router as page_router
from .remote import router as remote_router
from .resource_catalog import router as resource_catalog_router
from .sessions import router as sessions_router
from .update import management_router as update_management_router
from .update import router as update_router
from .user import router as user_router
from .voices import router as voices_router

ROUTERS = (
    admin_router,
    gateway_router,
    companion_router,
    companion_actions_router,
    companion_desktop_videos_router,
    companion_journal_router,
    companion_posts_router,
    companion_scenes_router,
    companion_topics_router,
    resource_catalog_router,
    voices_router,
    onboarding_router,
    config_router,
    media_router,
    page_router,
    remote_router,
    sessions_router,
    update_router,
    update_management_router,
    user_router,
)
