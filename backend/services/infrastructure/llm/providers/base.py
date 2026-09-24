from abc import ABC, abstractmethod
from collections.abc import Collection
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Literal

from modules.media import SpeechStyle
from openai import AsyncOpenAI

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
    """把产品推理档位映射到供应商实际支持的档位。

    - 空值或集合外值：不下发 reasoning。
    - 请求档恰好被支持：原样透传。
    - 请求档不在支持集：取不高于请求强度的最高支持档（含 ``none``）；例如选 ultra 但只支持到 xhigh → xhigh，选 minimal 但不支持 → none。
    - 没有不高于请求强度的支持档（例如选 minimal 但供应商只有 low 起）：不下发 reasoning。
    """
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
    extra: dict[str, Any] = field(default_factory=dict)


class BaseProvider(ABC):
    """供应商根类：子类声明 service_type 与 provider_name，按能力走下方对应 ABC。"""

    service_type: ServiceType = ServiceType.llm
    provider_name: str = ""
    # 提示词规约族，影响 system_prompt 中工具调用与执行纪律段落的选择；非 Google 模型保持 "openai"。
    PROMPT_FAMILY: ClassVar[str] = "openai"
    # 各能力默认模型；register() 时镜像到 registry，能力解析不需 import 各 provider 类。
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {}
    # 与 DEFAULT_MODELS["llm"] 不同时的视觉模型（如 mimo 用 mimo-v2.5、文生用 mimo-v2.5-pro）。
    DEFAULT_VISION_MODELS: ClassVar[dict[str, str]] = {}
    # 与 DEFAULT_MODELS["llm"] 不同时的视频理解模型；空表示沿用文本/视觉默认。
    DEFAULT_VIDEO_MODELS: ClassVar[dict[str, str]] = {}

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    def raw_client(self) -> "AsyncOpenAI | None":
        """默认无 OpenAI 客户端；OpenAI 兼容子类覆写此方法。"""
        return None


class ProviderError(Exception):
    """供应商级错误；字段对齐 error_classifier：status_code 给 _extract_status_code，body 给 _extract_error_body。

    ``provider`` 与 ``model`` 保留原始来源信息，供日志与调试定位，不参与错误分类。
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: dict | None = None,
        provider: str = "",
        model: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body or {}
        self.provider = provider
        self.model = model


class ProviderResultUnknownError(Exception):
    """非幂等请求可能已被供应商接受，但响应在确认前丢失。"""

    def __init__(self, method: str, url: str) -> None:
        super().__init__("Upstream result is unknown; automatic retry was suppressed to avoid duplicate charges.")
        self.method = method
        self.url = url


class ChatProvider(BaseProvider):
    service_type: ServiceType = ServiceType.llm

    # 是否已验证 json_object 模式也接受顶层数组；只支持对象的模式不能约束陪伴回复。
    supports_json_array: ClassVar[bool] = False
    supports_json_object: ClassVar[bool] = False
    # True 表示接受 image_url 内容部件；文本模型仅文本时需配合视觉变体（见 DEFAULT_VISION_MODELS）。
    supports_vision: ClassVar[bool] = False
    # True 表示接受 Responses 形状的 input_video 内容部件；仅 chat.completions 支持视频的供应商（如 mimo）不能声明。
    supports_video: ClassVar[bool] = False

    @abstractmethod
    def raw_client(self) -> AsyncOpenAI | None:
        """若该供应商走 OpenAI SDK 则返回缓存的 AsyncOpenAI，否则返回 None。"""


@dataclass(frozen=True)
class ImageGenRequest:
    prompt: str
    n: int = 1
    size: str | None = None
    aspect_ratio: str | None = None
    quality: str | None = None
    reference_image: str | None = None
    # 第二参考图（如风格/演示参考）；只有 supports_multiple_reference_images 的供应商会消费。
    secondary_reference_image: str | None = None
    response_format: Literal["b64", "url"] = "b64"
    # 背景输出策略：transparent 请求原生透明输出（透明 PNG），只允许发给声明
    # supports_transparent_background 的供应商链；None 不向请求添加字段，保持默认行为。
    background: Literal["transparent"] | None = None


@dataclass(frozen=True)
class ImageAsset:
    b64: str | None = None
    url: str | None = None
    mime: str = "image/png"


@dataclass(frozen=True)
class ImageGenResult:
    images: list[ImageAsset]
    model: str
    raw: Any = None


class ImageGenProvider(BaseProvider):
    service_type: ServiceType = ServiceType.image_gen

    # 单次原生请求的输出数量上限；None 表示适配器完整透传 n。
    max_images_per_request: ClassVar[int | None] = None

    # True 表示供应商原生消费 reference_image（图生图）；False 则对参考图请求跳过，避免图→文→图。
    supports_reference_image: ClassVar[bool] = False
    # True 表示同时消费 secondary_reference_image（双参考图生图）；False 时调用链会过滤掉，退而求其次选单参考图供应商。
    supports_multiple_reference_images: ClassVar[bool] = False
    # True 表示以 reference_image 为编辑底图的真图像编辑（保留未提及区域、按增量重绘）；
    # 角色条件化等弱参考（如 minimax subject_reference）不算编辑，置 False。
    supports_image_edit: ClassVar[bool] = False
    # True 表示已把 background="transparent" 映射为供应商请求参数，且配置的模型经真实调用
    # 验证返回带 Alpha 的图像；仅声明能力而未完成参数映射与真实验证不得置 True。
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
    model: str | None = None


@dataclass(frozen=True)
class VideoJobStatus:
    task_id: str
    status: Literal["queued", "processing", "succeeded", "failed"]
    file_id: str | None = None
    # 成功路径直接返回下载 URL 的供应商（如 MiniMax H3 v2，无 files/retrieve）填这里，让 worker 跳过二次拉取；None 表示需走 fetch(file_id)。
    download_url: str | None = None
    error: str | None = None
    raw: Any = None


@dataclass(frozen=True)
class VideoAsset:
    download_url: str
    content_type: str
    size: int | None = None
    expires_at: float | None = None


class VideoGenProvider(BaseProvider):
    service_type: ServiceType = ServiceType.video_gen

    # 能力声明（与各适配器 submit 校验保持一致）：编排层据此取链上最保守时长并按供应商选档；
    # None 表示未声明，按适配器自身校验兜底。
    # 可用时长档（整数秒，升序）。
    durations: tuple[int, ...] | None = None
    # 可用分辨率档（按成本升序）。
    resolutions: tuple[str, ...] | None = None
    # 支持 first_frame_image 图生视频；False 时带首帧的请求跳过该供应商。
    supports_first_frame: bool = False
    supports_loop_frames: bool = False
    # 消费 reference_images 身份参考；False 时编排层省略该字段，不排除该供应商。
    supports_reference_images: bool = False

    @abstractmethod
    async def submit(self, req: VideoGenRequest) -> VideoJobStatus: ...

    @abstractmethod
    async def poll(self, task_id: str) -> VideoJobStatus: ...

    @abstractmethod
    async def fetch(self, file_id: str) -> VideoAsset: ...


@dataclass(frozen=True)
class TTSResult:
    audio: bytes
    mime: str
    # 供应商回退后的音色 id，透出到 X-Voice-Used。
    voice: str = ""


@dataclass(frozen=True)
class VoiceDesignResult:
    voice_id: str
    trial_audio: bytes
    trial_audio_mime: str
    provider: str


class TTSProvider(BaseProvider):
    service_type: ServiceType = ServiceType.tts

    VOICE_CATALOG: ClassVar[list[dict]] = []

    # None 表示不支持声纹设计；非空字符串表示支持并作为面向用户的撰写指引。
    VOICE_DESIGN_GUIDE: ClassVar[str | None] = None

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        fmt: str = "mp3",
        speed: float | None = None,
        speech_style: SpeechStyle | None = None,
    ) -> TTSResult: ...

    async def design_voice(self, prompt: str, *, preview_text: str = "") -> VoiceDesignResult:
        raise NotImplementedError(f"{self.provider_name} does not support voice design")


@dataclass(frozen=True)
class STTResult:
    text: str
    raw: Any = None


class STTProvider(BaseProvider):
    service_type: ServiceType = ServiceType.stt

    @abstractmethod
    async def transcribe(self, audio: bytes, *, mime_type: str = "audio/wav", language: str = "auto") -> STTResult: ...


class EmbeddingProvider(BaseProvider):
    service_type: ServiceType = ServiceType.embedding
    dimension: ClassVar[int] = 1536

    @abstractmethod
    async def embed(self, texts: list[str], *, purpose: str = "db") -> list[list[float]]:
        """purpose 区分入库（"db"）与检索（"query"）；仅部分供应商（如 MiniMax embo-01）按用途优化向量。"""

    async def embed_one(self, text: str, *, purpose: str = "db") -> list[float] | None:
        results = await self.embed([text], purpose=purpose)
        return results[0] if results else None


def pick_catalog_voice(voice: str, catalog: list[dict]) -> str:
    """voice 不在 catalog 时回退到目录首位，避免向供应商传入陌生 id 触发 400。"""
    return voice if voice and any(v.get("id") == voice for v in catalog) else catalog[0]["id"]
