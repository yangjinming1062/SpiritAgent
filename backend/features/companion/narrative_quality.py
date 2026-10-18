"""动态与日记保存前的语义审阅；修订次数由各创作入口限定。"""

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from infrastructure.ai.prompt_engineer import LlmAttemptDiagnostic, LlmJsonValidationError, call_llm_json
from infrastructure.ai.user_config import UserLlmConfig
from infrastructure.logger import get_logger
from infrastructure.text import resolve_prompt_text
from pydantic import BaseModel, ConfigDict, Field

from features.companion.narrative_prompts import NARRATIVE_REVIEW_REPAIR_TEXTS, NARRATIVE_REVIEW_TEXTS

logger = get_logger(__name__)


class NarrativeIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    field: Literal["publication_intent", "title", "body", "text", "narration"]
    kind: Literal["unfinished_text", "unsupported_claim", "contradiction"]
    excerpt: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class NarrativeReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    issues: list[NarrativeIssue]


class NarrativeQualityError(RuntimeError):
    def __init__(self, review: NarrativeReview) -> None:
        logger.warning(
            "narrative revision failed content review",
            extra={
                "fields": sorted({i.field for i in review.issues}),
                "issues": sorted({i.kind for i in review.issues}),
            },
        )
        super().__init__("创作内容仍有问题，暂未发布")


async def review_narrative(
    llm_cfg: UserLlmConfig,
    source: dict[str, Any],
    draft: dict[str, str],
    language: str,
    *,
    before_call: Callable[[], Awaitable[None]] | None = None,
    diagnostics: list[LlmAttemptDiagnostic] | None = None,
) -> NarrativeReview:
    def parse_output(value: Any) -> NarrativeReview:
        review = NarrativeReview.model_validate(value)
        if any(issue.field not in draft or issue.excerpt not in draft[issue.field] for issue in review.issues):
            raise LlmJsonValidationError("invalid_review_excerpt", fields=("issues",))
        return review

    return await call_llm_json(
        llm_cfg,
        resolve_prompt_text(NARRATIVE_REVIEW_TEXTS, language),
        {"output_language": language, "draft": draft, "source": source},
        schema=NarrativeReview.model_json_schema(),
        schema_name="narrative_review",
        parse_output=parse_output,
        repair_prompt=resolve_prompt_text(NARRATIVE_REVIEW_REPAIR_TEXTS, language),
        reasoning_effort="medium",
        before_call=before_call,
        diagnostics=diagnostics,
    )
