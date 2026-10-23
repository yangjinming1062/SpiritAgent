"""影像迁移只读预检；凭据从运行环境读取，报告不包含连接字符串。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from infrastructure.config import SETTINGS
from infrastructure.database import database_url
from sqlalchemy import create_engine, text
from workflows.maintenance.visuals_migration import build_plan, fetch_old_rows, summarize
from workflows.maintenance.visuals_migration_files import verify_migration_files


def main() -> int:
    engine = create_engine(
        database_url("postgresql+psycopg"),
        connect_args={"connect_timeout": 10},
        hide_parameters=True,
    )

    try:
        with engine.connect() as bind:
            bind.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            revision = bind.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if revision != "0002":
                print(json.dumps({"blocking": {"schema": "预检要求数据库处于 0002"}}))
                return 1
            rows = fetch_old_rows(bind)
            plan = build_plan(rows, Path(SETTINGS.data_dir))
            checks = verify_migration_files(plan, Path(SETTINGS.data_dir))
    finally:
        engine.dispose()
    failures = [entry for entry in checks if entry["error"]]
    report = {
        "summary": summarize(plan),
        "file_checks": checks,
        "blocking": {
            "file_errors": failures,
            "pending_queue_requests": plan.diagnostics.get("pending_queue_requests", []),
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 1 if failures or report["blocking"]["pending_queue_requests"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
