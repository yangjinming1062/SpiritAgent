"""素材操作队列、结果投影与恢复阶段判定。队列只由事务入口领取和更新。"""

from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from features.media.character_images import ImageChainState

from .schemas import (
    ImportAnalysisResult,
    ImportAnalysisResultView,
    ImportAnalysisState,
    ImportComponent,
    ImportComponentView,
    ImportSelection,
    VisualJobState,
)


def import_analysis_state(state: VisualJobState, stage: str) -> ImportAnalysisState:
    # 共享任务存储以 paused 保存等待；上传契约区分待确认和执行暂停。
    return "awaiting_input" if state == "paused" and stage == "confirmation" else state


class ImportOperation(ImportSelection):
    id: str = Field(default_factory=lambda: uuid4().hex)
    command: Literal["organize", "existing", "extract", "retry", "regenerate", "skip", "undo"]
    state: Literal["queued", "running", "paused", "failed"] = "queued"


def import_operations(values: list[dict[str, Any]]) -> list[ImportOperation]:
    return [ImportOperation.model_validate(value) for value in values]


def import_result_view(
    result: ImportAnalysisResult,
    chains: dict[str, ImageChainState],
    operations: list[ImportOperation],
    state: str,
) -> ImportAnalysisResultView:
    """只读投影，持久结果始终不含队列状态及界面操作字段。"""
    pending = {operation.key: operation for operation in operations}
    components: list[ImportComponentView] = []
    for component in result.components:
        chain = chains.get(component.observation.key)
        operation = pending.get(component.observation.key)
        operation_state = (
            operation.state
            if operation is not None
            else "failed"
            if component.status == "failed"
            else "paused"
            if component.status == "reviewing"
            else "succeeded"
            if component.status in {"created", "reused", "skipped", "undone", "ambiguous"}
            else "idle"
        )
        if operation is not None and operation.state != "failed" and state in {"paused", "cancelled"}:
            operation_state = "paused"
        failure_code = component.failure_code
        if operation_state == "failed" and failure_code is None:
            failure_code = (
                "result_unknown"
                if chain and chain.stop_reason == "result_unknown"
                else "result_download_failed"
                if chain and chain.pending_urls
                else "processing_failed"
            )
        components.append(
            ImportComponentView(
                **{**component.model_dump(), "failure_code": failure_code},
                operation_state=operation_state,
                can_extract=component.material_id is None
                or (component.observation.kind == "action" and component.status in {"failed", "ambiguous"}),
                extract_command=extraction_command(component, chain),
            )
        )
    return ImportAnalysisResultView(**{**result.model_dump(), "components": components})


def extraction_command(
    component: ImportComponent, chain: ImageChainState | None
) -> Literal["extract", "retry", "regenerate"]:
    if chain is not None:
        # 已提交和已返回的结果优先恢复，不能因旧检查结论而重新付费。
        if chain.phase in {"submitting", "storing", "evaluating"} or chain.pending_urls or chain.pending_path:
            return "retry"
        if chain.stop_reason == "result_unknown" or chain.can_retry_goal_review():
            return "retry"
    if component.candidate_verdict in {"review", "fail"}:
        return "regenerate"
    if component.observation.kind == "action" and (
        component.failure_code == "action_rejected" or component.status == "ambiguous"
    ):
        return "regenerate"
    if component.preview_path or component.creation_method == "pose":
        return "retry"
    if chain is not None:
        if any(not candidate.evaluated for candidate in chain.candidates):
            return "retry"
        if chain.inputs is not None and chain.best() is not None:
            return "retry"
        if chain.stop_reason or (not chain.remaining_slots and chain.next_index >= len(chain.providers)):
            return "regenerate"
        return "retry"
    if component.status == "failed":
        return "regenerate" if component.failure_code == "generation_failed" else "retry"
    return "extract"


def resume_material_operations(
    result: ImportAnalysisResult,
    operations: list[ImportOperation],
    chains: dict[str, ImageChainState],
    *,
    include_failed: bool = True,
) -> list[ImportOperation]:
    """继续未完成条目，保留用户暂停前的操作及修正说明。"""
    for component in result.components:
        if component.status == "failed" and not include_failed:
            continue
        previous = next((entry for entry in operations if entry.key == component.observation.key), None)
        if previous is not None and previous.state in {"queued", "running"}:
            continue
        if (previous is None or previous.state != "paused") and component.status in {
            "created",
            "reused",
            "skipped",
            "undone",
            "ambiguous",
        }:
            continue
        command = extraction_command(component, chains.get(component.observation.key))
        operation = ImportOperation(
            key=component.observation.key,
            command="organize" if component.status == "recognized" else command,
            notes=previous.notes if previous is not None else component.notes,
        )
        if previous is not None and previous.state == "paused":
            operation = previous.model_copy(update={"state": "queued"})
        operations = [entry for entry in operations if entry.key != operation.key] + [operation]
        component.operation_stage = "organize"
        component.revision += 1
    return operations
