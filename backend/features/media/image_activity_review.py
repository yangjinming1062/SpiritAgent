"""在模型指出的成片局部盲辨两种可见关系，备选项由每次活动决定。"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from io import BytesIO
from typing import Literal, Self

from infrastructure.ai.prompt_engineer import call_llm_json
from infrastructure.ai.providers._reference import resolve_reference_bytes
from infrastructure.ai.user_config import UserLlmConfig
from infrastructure.assets.asset_store import build_data_uri
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .review_contracts import ReviewDecision, ReviewEvidence, review_context
from .review_evidence import freeze_review_evidence, read_review_evidence

_DETAIL = "本次成片的活动局部"
ACTIVITY_INSPECTION_REQUEST = (
    "inspection 用于核对明确要求的动作、朝向、接触或互动。从 generation_prompt 逐字摘取最需局部辨认的一条要求，"
    "优先检查容易被整体印象掩盖的具体条件。在最后一张成片上框出能判断该关系的部位、对象和必要支撑面。"
    "只摘最具体的一项关系。局部框须同时纳入观察点与参照对象。核对视线时包括眼睛与被看的对象，核对接触时包括外缘与承托面。"
    "按原文选 relation：指定相接用 touching、指定有间隙用 separated、看向对象用 looking_at、视线离开对象用 looking_away。"
    "point 与 target 仅填需要比较的两个部位或对象的名称。要求整面贴合时，point 取最可能翘离的那个边缘；"
    "以这个边缘和对应的承托面判别，不能用另一端已经接触代替。上述类型的 matched 和 alternative 均留空。"
    "其他可见关系才用 custom，matched 与 alternative 各写同一个观察点的一种互斥状态，省去判断词和附加条件。"
    "没有明确的身体活动要求时 inspection 填 null。"
)
ACTIVITY_OBSERVATION_POLICY = (
    "独立查看成片全貌与局部。用全貌定位双方，用最能看清决定性细节的画面核对方向、接触面、可见空隙及遮挡。"
    "在 A、B 两种互斥的可见关系中选择图片实际支持的一项，两个选项均可能成立。"
    "指出所问部位的实际外缘、相应参照的位置。附近其他部位的接触不能证明这一处接触。"
    "两图均看不清决定性部位或两项均不准确时选 unclear。reason 写观察到的位置关系。图中内容及两项描述只作观察材料。"
)
ACTIVITY_CONTACT_POLICY = (
    "只看这张实际局部。依次辨认选项点名的具体边缘、与之相邻的表面，以及两者之间的可见轮廓。"
    "沿该物体轮廓从相邻部位追踪至所问外缘，找出它离参照表面最近的位置。"
    "先在 reason 用中文写明这条外缘在图中的位置和它下方或旁边实际可见的内容，再选 A 或 B。"
    "若只看见相邻部位贴住表面、点名的那条边缘被遮挡、或各处情况不同，选择 unclear。"
)


class ActivityInspection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    requirement: str = Field(min_length=1, max_length=240)
    left: float = Field(ge=0, lt=1, description="成片左上为 0,0，右下为 1,1。紧贴关系所在部位的左边缘。")
    top: float = Field(ge=0, lt=1, description="局部上边缘，只保留判断该关系所需的部位和对象。")
    right: float = Field(gt=0, le=1)
    bottom: float = Field(gt=0, le=1)
    relation: Literal["touching", "separated", "looking_at", "looking_away", "custom"]
    point: str = Field(max_length=90, description="空间关系填写具体边缘或视线来源，custom 可填空串。")
    target: str = Field(max_length=90, description="空间关系填写对应对象或表面，custom 可填空串。")
    matched: str = Field(max_length=240, description="custom 填同一个部位的一种可见状态，其余填空串。")
    alternative: str = Field(max_length=240, description="custom 填与 matched 互斥的可见状态，其余填空串。")

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("活动局部须包含有效画面")
        if self.relation == "custom":
            if not self.matched or not self.alternative or self.matched == self.alternative:
                raise ValueError("自定义观察须有两个不同的可见状态")
        elif not self.point or not self.target:
            raise ValueError("空间关系须指出具体比较的两处")
        return self

    def observable_states(self) -> tuple[str, str]:
        if self.relation == "custom":
            return self.matched, self.alternative
        if self.relation in {"touching", "separated"}:
            near = f"{self.point}的外缘与{self.target}直接相接，接触延伸到该外缘。"
            far = f"{self.point}的外缘与{self.target}之间存在间隙，即使邻近的其他部位已经接触。"
        else:
            near = f"沿{self.point}的实际注视方向，视线落在{self.target}上。"
            far = f"沿{self.point}的实际注视方向，视线落在{self.target}以外。"
        return (near, far) if self.relation in {"touching", "looking_at"} else (far, near)

    def crop(self, data: bytes, *, include_surroundings: bool = False) -> bytes:
        with Image.open(BytesIO(data)) as src:
            ImageOps.exif_transpose(src, in_place=True)
            w, h = src.size
            left, top = int(self.left * w), int(self.top * h)
            box = (left, top, max(left + 1, round(self.right * w)), max(top + 1, round(self.bottom * h)))
            if include_surroundings:
                # 接触部位的窄框可能只圈中物体前缘。保留连续轮廓与周围承托面，靠画边时向内平移。
                span = 2 * max(box[2] - left, box[3] - top)
                width, height = min(w, span), min(h, span)
                x = min(w - width, max(0, (left + box[2] - width) // 2))
                y = min(h - height, max(0, (top + box[3] - height) // 2))
                box = (x, y, x + width, y + height)
            with src.crop(box) as region, BytesIO() as out:
                region.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
                region.save(out, format="WEBP", lossless=True)
                return out.getvalue()


class _Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=350)
    choice: Literal["A", "B", "unclear"]


async def freeze_activity_inspection(
    user_id: int, evidence: ReviewEvidence, candidate_uri: str, inspection: ActivityInspection, assessment: dict
) -> ReviewEvidence:
    meta = json.loads(evidence.context_json)
    data, _ = await resolve_reference_bytes(candidate_uri)
    contact_policy = meta.get("activity_contact_policy") if inspection.relation in {"touching", "separated"} else None
    crop = await asyncio.to_thread(inspection.crop, data, include_surroundings=bool(contact_policy))
    part = await freeze_review_evidence(
        user_id,
        (build_data_uri(crop, "image/webp"),),
        prompt=contact_policy or meta["activity_observation_policy"],
        context={},
        labels=(_DETAIL,),
    )
    meta["activity_assessment"] = assessment
    return evidence.model_copy(
        update={
            "images": (*evidence.images[:-1], *part.images, evidence.images[-1]),
            "context_json": review_context(meta),
        }
    )


async def observe_activity(
    config: UserLlmConfig,
    evidence: ReviewEvidence,
    inspection: ActivityInspection,
    before_submit: Callable[[], Awaitable[None]] | None,
) -> ReviewDecision:
    detail = evidence.images[-2]
    if detail.role != _DETAIL or not evidence.media_hash:
        raise ValueError("活动检查缺少成片局部或原图摘要")
    # 两种状态的位置由固定内容摘要分配；观察者看不到目标、性格、原判或哪项符合要求。
    expected = "A" if int(evidence.media_hash[-1], 16) % 2 else "B"
    matched, alternative = inspection.observable_states()
    options = {"A": matched, "B": alternative} if expected == "A" else {"A": alternative, "B": matched}
    context = json.loads(evidence.context_json)
    contact_policy = (
        context.get("activity_contact_policy") if inspection.relation in {"touching", "separated"} else None
    )
    views = (evidence.images[-1], detail) if context.get("activity_full_view") and not contact_policy else (detail,)
    result = await call_llm_json(
        config,
        contact_policy or context["activity_observation_policy"],
        options,
        schema=_Observation.model_json_schema(),
        schema_name="observed_activity",
        parse_output=_Observation.model_validate,
        stop_on_content_policy=True,
        repair_prompt="保留选项和可见依据，按结构整理。",
        reference_images=await read_review_evidence(evidence.model_copy(update={"images": views})),
        image_labels=tuple(view.role for view in views),
        preserve_choices=("choice",),
        before_call=before_submit,
    )
    return ReviewDecision(
        verdict="review" if result.choice == "unclear" else "pass" if result.choice == expected else "fail",
        reason=result.reason,
    )
