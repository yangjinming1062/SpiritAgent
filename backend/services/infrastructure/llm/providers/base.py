from abc import ABC, abstractmethod
from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Literal

from components import get_logger
from modules.media import SpeechStyle
from openai import AsyncOpenAI

from .http import get_async_client

logger = get_logger(__name__)

# 产品对外暴露的推理强度档位（升序）。供应商实际支持集是其子集，由 ChatProvider.REASONING_EFFORTS 声明。
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
REASONING_EFFORT_ORDER: tuple[ReasoningEffort, ...] = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
)
REASONING_EFFORT_RANK: dict[str, int] = {effort: rank for rank, effort in enumerate(REASONING_EFFORT_ORDER)}
PRODUCT_REASONING_EFFORTS: frozenset[str] = frozenset(REASONING_EFFORT_ORDER)


def resolve_provider_reasoning_effort(requested: str | None, supported: Collection[str]) -> str | None:
    """把产品推理档位映射到供应商支持档位：空值/集合外不下发；恰好支持则透传；否则取不高于请求强度的最高支持档（含 none），无候选则不下发。"""
    if not requested:
        return None
    raw = requested.strip().lower()
    if raw not in PRODUCT_REASONING_EFFORTS:
        return None
    if raw in supported:
        return raw
    request_rank = REASONING_EFFORT_RANK[raw]
    candidates = [
        effort
        for effort in supported
        if effort in REASONING_EFFORT_RANK and REASONING_EFFORT_RANK[effort] <= request_rank
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda effort: REASONING_EFFORT_RANK[effort])


class ServiceType(StrEnum):
    llm = "llm"
    stt = "stt"
    tts = "tts"
    image_gen = "image_gen"
    video_gen = "video_gen"
    embedding = "embedding"


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    api_key: str
    model: str
    service_type: ServiceType
    provider_name: str
    model_overridden: bool = False


class BaseProvider(ABC):
    """供应商根类：子类声明 service_type 与 provider_name，按能力走下方对应 ABC。"""

    service_type: ServiceType = ServiceType.llm
    provider_name: str = ""
    # 能力卡片未填写端点与模型时使用的默认值。
    DEFAULT_BASE_URL: ClassVar[str] = ""
    DEFAULT_MODEL: ClassVar[str] = ""
    # False 表示该能力可不带 api_key（如本机无鉴权服务）；能力链解析据此放宽空密钥
    requires_api_key: ClassVar[bool] = True

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config


class ProviderError(Exception):
    """供应商级错误；status_code 与 body 供错误分类读取。"""

    def __init__(self, message: str, *, status_code: int | None = None, body: dict | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body or {}


class ChatProvider(BaseProvider):
    """经 OpenAI Responses 协议提供 chat 的供应商。"""

    service_type: ServiceType = ServiceType.llm

    # 0 表示未声明，由 resolve_context_tokens 回退到全局默认
    CONTEXT_TOKENS: ClassVar[int] = 0
    DEFAULT_VISION_MODEL: ClassVar[str] = ""  # 与 DEFAULT_MODEL 不同的视觉模型；空表示文本与视觉共用
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset({"none", "low", "medium", "high"})
    TEMPERATURE_MIN: ClassVar[float] = 0.0
    TEMPERATURE_MAX: ClassVar[float] = 2.0
    # 是否已验证 json_object 模式也接受顶层数组；只支持对象的模式不能约束陪伴回复
    supports_json_array: ClassVar[bool] = False
    supports_json_object: ClassVar[bool] = False
    supports_vision: ClassVar[bool] = False  # 接受 input_image 部件；文本模型需配合 DEFAULT_VISION_MODEL
    # 接受 Responses 形状的 input_video 部件；仅 chat.completions 支持视频的供应商（如 mimo）不能声明
    supports_video: ClassVar[bool] = False

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_async_client(config.api_key, config.base_url)

    @classmethod
    def scale_temperature(cls, normalized: float) -> float:
        """把 [0, 1] 归一化温度映射到本供应商原生刻度：clamp(归一化值 × MAX, MIN, MAX)，保留两位小数。"""
        return round(max(cls.TEMPERATURE_MIN, min(cls.TEMPERATURE_MAX, normalized * cls.TEMPERATURE_MAX)), 2)

    def raw_client(self) -> AsyncOpenAI:
        return self._client


@dataclass(frozen=True)
class ImageGenRequest:
    prompt: str
    n: int = 1
    size: str | None = None
    aspect_ratio: str | None = None
    reference_image: str | None = None
    secondary_reference_image: str | None = None  # 仅 supports_multiple_reference_images 的供应商消费
    image_edit: bool = False  # True 以 reference_image 为底图增量编辑（画布贴近参考图）；False 参考图仅作身份条件，画布服从 size/aspect_ratio
    response_format: Literal["b64", "url"] = "b64"
    background: Literal["transparent"] | None = (
        None  # transparent 请求原生透明 PNG，仅发给 supports_transparent_background 链；None 不加字段
    )


@dataclass(frozen=True)
class ImageAsset:
    b64: str | None = None
    url: str | None = None
    mime: str = "image/png"


@dataclass(frozen=True)
class ImageGenResult:
    images: list[ImageAsset]


class ImageGenProvider(BaseProvider):
    service_type: ServiceType = ServiceType.image_gen

    max_images_per_request: ClassVar[int | None] = None  # 单次原生请求输出上限；None 表示适配器完整透传 n
    max_prompt_chars: ClassVar[int | None] = None  # 提示词字符上限；放不下的请求在链上跳过该供应商，None 表示不限

    supports_reference_image: ClassVar[bool] = (
        False  # 原生消费 reference_image（图生图）；False 则对参考图请求跳过，避免图→文→图
    )
    supports_multiple_reference_images: ClassVar[bool] = (
        False  # 同时消费 secondary_reference_image；False 时调用链会过滤掉
    )
    supports_image_edit: ClassVar[bool] = (
        False  # 以 reference_image 为编辑底图的真图像编辑（保留未提及区域）；弱参考条件化不算
    )
    # True 须已完成 background="transparent" 参数映射且配置模型经真实调用验证返回带 Alpha 的图像
    supports_transparent_background: ClassVar[bool] = False

    @abstractmethod
    async def generate(self, req: ImageGenRequest) -> ImageGenResult: ...


@dataclass(frozen=True)
class VideoGenRequest:
    prompt: str
    duration: int = 6
    resolution: str = "768P"
    first_frame_image: str | None = None
    last_frame_image: str | None = None
    reference_images: tuple[str, ...] = ()
    aspect_ratio: str | None = None


VideoJobState = Literal["queued", "processing", "succeeded", "failed"]


@dataclass(frozen=True)
class VideoJobStatus:
    task_id: str
    status: VideoJobState
    download_url: str | None = None  # succeeded 时供应商给出的成品下载地址
    error: str | None = None


class VideoGenProvider(BaseProvider):
    service_type: ServiceType = ServiceType.video_gen

    # 能力声明（与各适配器 submit 校验保持一致）：编排层据此取链上最保守时长并按供应商选档；None 按适配器自身校验兜底
    durations: tuple[int, ...] | None = None  # 可用时长档（整数秒，升序）
    resolutions: tuple[str, ...] | None = None  # 可接受的分辨率档，可能包含兼容别名
    supports_first_frame: bool = False  # 支持 first_frame_image 图生视频；False 时带首帧的请求跳过该供应商
    supports_loop_frames: bool = False
    supports_reference_images: bool = False  # 消费 reference_images 身份参考；False 时编排层省略该字段，不排除该供应商

    def max_resolution(
        self,
        *,
        duration: int,
        first_frame: bool = False,
        last_frame: bool = False,
        reference_images: bool = False,
    ) -> str | None:
        """配置模型在本次输入组合下的最高原生档；未声明或组合不可用时返回 None。"""
        return None

    @abstractmethod
    async def submit(self, req: VideoGenRequest) -> VideoJobStatus: ...

    @abstractmethod
    async def poll(self, task_id: str) -> VideoJobStatus: ...


@dataclass(frozen=True)
class TTSResult:
    audio: bytes
    mime: str
    voice: str = ""  # 供应商回退后的音色 id，透出到 X-Voice-Used


@dataclass(frozen=True)
class VoiceDesignResult:
    voice_id: str
    trial_audio: bytes
    trial_audio_mime: str
    provider: str


class TTSProvider(BaseProvider):
    service_type: ServiceType = ServiceType.tts

    VOICE_CATALOG: ClassVar[list[dict]] = []

    VOICE_DESIGN_GUIDE: ClassVar[str | None] = None  # None 不支持声纹设计；非空字符串表示支持并作为面向用户的撰写指引

    @abstractmethod
    async def synthesize(self, text: str, *, voice: str, speech_style: SpeechStyle | None) -> TTSResult:
        """合成 MP3 音频。"""

    async def design_voice(self, prompt: str, *, preview_text: str = "") -> VoiceDesignResult:
        raise NotImplementedError(f"{self.provider_name} does not support voice design")


@dataclass(frozen=True)
class STTResult:
    text: str


class STTProvider(BaseProvider):
    service_type: ServiceType = ServiceType.stt

    @abstractmethod
    async def transcribe(self, audio: bytes, *, mime_type: str = "audio/wav", language: str = "auto") -> STTResult: ...


class EmbeddingProvider(BaseProvider):
    service_type: ServiceType = ServiceType.embedding

    @abstractmethod
    async def embed(self, texts: list[str], *, purpose: str = "db") -> list[list[float]]:
        """purpose 区分入库（"db"）与检索（"query"）；仅部分供应商（如 MiniMax embo-01）按用途优化向量。"""


def pick_catalog_voice(voice: str, catalog: list[dict], *, provider: str) -> str:
    """voice 不在 catalog 时回退到目录首位，避免向供应商传入陌生 id 触发 400。"""
    if voice and any(v.get("id") == voice for v in catalog):
        return voice
    chosen = catalog[0]["id"]
    if voice:
        logger.info("tts voice substituted", extra={"provider": provider, "requested": voice, "used": chosen})
    return chosen
