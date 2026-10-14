"""按服务端运营配置选择社交采集源，不自动切换带收费的供应商。"""

from infrastructure.config import SETTINGS

from .free_social import FreeSocialClient
from .social_content import SocialPlatform
from .tikhub import TikHubClient


def create_social_client() -> FreeSocialClient | TikHubClient:
    if SETTINGS.social_search_backend == "tikhub":
        return TikHubClient(api_key=SETTINGS.tikhub_api_key, base_url=SETTINGS.tikhub_base_url)
    return FreeSocialClient(
        x_cookies=SETTINGS.x_search_cookies,
        xhs_base_url=SETTINGS.xhs_mcp_base_url,
        xhs_auth_token=SETTINGS.xhs_mcp_auth_token,
    )


def available_platforms() -> tuple[SocialPlatform, ...]:
    return create_social_client().supported_platforms


def source_configured() -> bool:
    return bool(available_platforms())
