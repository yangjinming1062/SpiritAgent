"""生活事实及显式任务、结果和证据关系的备份贡献。"""

from infrastructure.backup import BackupTable

from .models import LifeEvent, LifePursuit, LifeState, LifeStep, PursuitEvidence


def backup_tables() -> tuple[BackupTable, ...]:
    result_keys = {
        "result_job_id": "jobs",
        "result_visual_id": "visual_items",
        "result_visual_version_id": "visual_versions",
        "result_entry_id": "companion_entries",
    }
    return (
        BackupTable("life_pursuits", LifePursuit),
        BackupTable(
            "life_steps",
            LifeStep,
            foreign_keys={"pursuit_id": "life_pursuits", "job_id": "jobs", **result_keys},
            unique_keys=("idempotency_key",),
        ),
        BackupTable(
            "life_events",
            LifeEvent,
            foreign_keys={"pursuit_id": "life_pursuits", "step_id": "life_steps", **result_keys},
            unique_keys=("event_key",),
        ),
        BackupTable("life_states", LifeState, unique_keys=(), foreign_keys={"active_pursuit_id": "life_pursuits"}),
        BackupTable(
            "pursuit_evidence",
            PursuitEvidence,
            foreign_keys={"pursuit_id": "life_pursuits", "event_id": "life_events"},
            unique_keys=("pursuit_id", "event_id"),
        ),
    )
