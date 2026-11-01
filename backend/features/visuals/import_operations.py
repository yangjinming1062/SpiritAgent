"""素材操作队列与旧分析记录的状态投影。队列只由事务入口领取和更新。"""

from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from features.media.character_images import ImageChainState

from .schemas import ImportAnalysisResult, ImportComponent, ImportSelection


class ImportOperation(ImportSelection):
    id: str = Field(default_factory=lambda: uuid4().hex)
    command: Literal["organize", "existing", "extract", "retry", "regenerate", "skip", "undo"]
    state: Literal["queued", "running", "paused", "failed"] = "queued"


def import_operations(values: list[dict[str, Any]]) -> list[ImportOperation]:
    operations = []
    for value in values:
        operation = ImportOperation.model_validate(value)
        if "state" not in value:
            # 旧版完成时残留的待重试选择没有状态字段；按暂停投影，等用户显式继续，
            # 避免升级后旧失败任务的条目永久显示为排队中。
            operation.state = "paused"
        operations.append(operation)
    return operations


def normalize_import_result(result: ImportAnalysisResult, chains: dict[str, ImageChainState]) -> None:
    for component in result.components:
        chain = chains.get(component.observation.key)
        if component.operation_state == "idle" and component.status == "recognized":
            if component.material_id is not None and component.creation_method in {"pose", "motion"}:
                component.status, component.operation_state, component.failure_code = (
                    "failed",
                    "failed",
                    "action_rejected",
                )
            elif component.material_id is None and component.candidate_verdict == "pass" and component.preview_path:
                component.status, component.operation_state, component.failure_code = "failed", "failed", "save_failed"
        if (
            component.operation_state == "idle"
            and component.status == "recognized"
            and component.candidate_verdict in {"review", "fail"}
        ):
            component.failure_code = "quality_rejected"
            component.operation_state = "failed"
            component.status = "failed"
        if (
            component.operation_state in {"idle", "failed"}
            and component.status == "failed"
            and component.failure_code is None
        ):
            component.failure_code = (
                "result_unknown"
                if chain and chain.stop_reason == "result_unknown"
                else "result_download_failed"
                if chain and chain.pending_urls
                else "processing_failed"
            )
            component.operation_state = "failed"
        component.can_extract = component.material_id is None or (
            component.observation.kind == "action" and component.status == "failed"
        )
        component.extract_command = extraction_command(component, chain)


def extraction_command(
    component: ImportComponent, chain: ImageChainState | None
) -> Literal["extract", "retry", "regenerate"]:
    if component.candidate_verdict in {"review", "fail"}:
        return "regenerate"
    if component.preview_path or component.creation_method == "pose":
        return "retry"
    if chain is not None:
        if chain.phase == "complete" and chain.stop_reason == "exhausted":
            return "regenerate"
        return "retry"
    if component.operation_state == "failed":
        return "regenerate" if component.failure_code == "generation_failed" else "retry"
    return "extract"
