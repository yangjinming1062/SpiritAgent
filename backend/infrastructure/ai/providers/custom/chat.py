from typing import ClassVar

from infrastructure.ai.providers.base import ChatProvider


class CustomChatProvider(ChatProvider):
    """标准 OpenAI Responses（/responses）通用供应商；base_url 填到版本前缀为止（如 https://host/v1），SDK 将拼 /responses。

    能力按标准协议声明为可用，实际支持与否取决于所配模型。
    """

    provider_name = "custom"
    # 地址由供应商信息库配置，模型由能力卡片指定。
    DEFAULT_BASE_URL: ClassVar[str] = ""
    supports_json_object: ClassVar[bool] = True
    supports_image: ClassVar[bool] = True  # input_image 为 Responses 标准部件
