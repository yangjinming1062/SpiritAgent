"""无凭据的生成来源与单圈供应商选择。"""

from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from infrastructure.ai.providers.base import ProviderConfig, ServiceType
from infrastructure.ai.tasks import AiTaskKey


class GenerationOrigin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task: AiTaskKey
    service_type: ServiceType
    provider_id: UUID
    model_card_id: UUID
    model: str

    @classmethod
    def from_config(cls, config: ProviderConfig) -> "GenerationOrigin":
        if config.task is None:
            raise ValueError("generation origin requires an AI task")
        return cls(
            task=config.task,
            service_type=config.service_type,
            provider_id=config.provider_id,
            model_card_id=config.model_card_id,
            model=config.model,
        )


class ProviderSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: GenerationOrigin | None = None
    mode: Literal["default", "current", "next"] = "default"


def order_provider_chain(
    chain: Sequence[ProviderConfig],
    selection: ProviderSelection | None = None,
) -> list[ProviderConfig]:
    """在能力过滤前旋转当前链；来源失效时从首项开始，单次只遍历一圈。"""
    ordered = list(chain)
    if not ordered or selection is None or selection.mode == "default" or selection.origin is None:
        return ordered
    origin = selection.origin
    index = next(
        (
            index
            for index, config in enumerate(ordered)
            if config.task == origin.task
            and config.service_type == origin.service_type
            and config.provider_id == origin.provider_id
            and config.model_card_id == origin.model_card_id
            and config.model == origin.model
        ),
        None,
    )
    if index is None:
        return ordered
    start = (index + (selection.mode == "next")) % len(ordered)
    return ordered[start:] + ordered[:start]
