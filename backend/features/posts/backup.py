"""发布任务的专有字段；控制字段由 jobs 的物理父表导出。"""

from infrastructure.backup import BackupTable

from .publication_models import PostPublication


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "post_publications",
            PostPublication,
            foreign_keys={"video_job_id": "video_gen_jobs", "post_id": "companion_entries"},
        ),
    )
