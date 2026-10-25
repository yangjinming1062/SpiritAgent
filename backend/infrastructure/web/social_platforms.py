"""资讯平台的公开标识、登录方式与原文地址规则。"""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import AfterValidator, WithJsonSchema

SocialAuthKind = Literal["cookie", "qr"]


@dataclass(frozen=True)
class PlatformDefinition:
    id: str
    auth_kind: SocialAuthKind
    labels: dict[str, str]
    hosts: tuple[str, ...]
    path_pattern: str
    query_keys: tuple[str, ...] = ()


SOCIAL_PLATFORMS: dict[str, PlatformDefinition] = {
    "x": PlatformDefinition(
        id="x",
        auth_kind="cookie",
        labels={"zh": "X", "en": "X"},
        hosts=("x.com", "www.x.com", "twitter.com", "www.twitter.com"),
        path_pattern=r"/[A-Za-z0-9_]+/status/{source_id}",
    ),
    "xiaohongshu": PlatformDefinition(
        id="xiaohongshu",
        auth_kind="qr",
        labels={"zh": "小红书", "en": "Xiaohongshu"},
        hosts=("xiaohongshu.com", "www.xiaohongshu.com"),
        path_pattern=r"/(?:explore|discovery/item)/{source_id}",
        query_keys=("xsec_token", "xsec_source"),
    ),
}


def get_social_platform(platform: str) -> PlatformDefinition:
    try:
        return SOCIAL_PLATFORMS[platform]
    except KeyError:
        raise ValueError("Unsupported social platform") from None


def _registered_platform(value: str) -> str:
    return get_social_platform(value).id


SocialPlatform = Annotated[
    str,
    AfterValidator(_registered_platform),
    WithJsonSchema({"type": "string", "enum": list(SOCIAL_PLATFORMS)}),
]
