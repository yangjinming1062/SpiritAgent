"""升级前检查无队列归属的旧视频；人工查实终态后登记结果或失败收尾。"""

import argparse
import asyncio
import json
import re
from urllib.parse import urlsplit

from infrastructure.database import ENGINE
from infrastructure.upgrade_checks import untracked_video_attempts, video_upgrade_state_sha256
from sqlalchemy import Connection, text


def run(connection: Connection, args: argparse.Namespace) -> None:
    pending = untracked_video_attempts(connection)
    if args.settled:
        kind, raw_id = args.settled.split(":", 1)
        entity_id = int(raw_id)
        item = next((value for value in pending if value.kind == kind and value.entity_id == entity_id), None)
        if item is None or item.state_sha256 != args.expected_state_sha256:
            raise ValueError("任务已变更或不在未决清单中，请重新检查状态指纹")
        if args.generation_id is not None and item.generation_id != args.generation_id:
            raise ValueError("提供的制作标识与当前保存的标识不符")
        if not args.note or len(args.note.strip()) < 10:
            raise ValueError("须记录已核实供应商终态的依据（至少 10 字）")
        table = {"media": "video_gen_jobs", "action": "companion_actions", "desktop": "desktop_video_actions"}[kind]
        row = (
            connection.execute(
                text(f"SELECT * FROM {table} WHERE id=:id FOR UPDATE"),
                {"id": entity_id},
            )
            .mappings()
            .one()
        )
        if video_upgrade_state_sha256(row) != args.expected_state_sha256:
            raise ValueError("任务在核对期间已变更，请重新检查状态指纹")
        progress = json.loads(row["generation_state_json"])
        state = json.loads(progress["video_chain_json"]) if kind == "desktop" else progress
        if (
            (args.generation_id is not None and state.get("generation_id") != args.generation_id)
            or state.get("queue_request_id")
            or state.get("result_url")
            or state.get("source_path")
            or state.get("stop_reason")
            in {"provider_failed", "submit_failed", "content_policy_blocked", "queue_stopped"}
        ):
            raise ValueError("任务在核对期间已变更")
        succeeded = args.outcome == "succeeded"
        result_url = args.result_url
        if succeeded:
            parsed = urlsplit(result_url or "")
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("已成功任务须提供原供应商可下载的 --result-url")
            state["result_url"] = result_url
        state["stop_reason"] = "" if succeeded else "provider_failed"
        state["phase"] = "storing" if succeeded else "complete"
        note = f"升级前人工核实 {args.outcome}: {args.note.strip()}"
        if kind == "desktop":
            progress.update(
                video_chain_json=json.dumps(state, ensure_ascii=False),
                provider_task_id=None,
                submission_unknown=False,
                failure_reason="" if succeeded else note,
            )
            if succeeded:
                progress["download_url"] = result_url
        else:
            progress = state
        status = "downloading" if succeeded and kind == "media" else "processing" if succeeded else "failed"
        updates = "generation_state_json=:state, status=:status"
        if kind == "media":
            updates += ", provider_task_id=NULL, error_reason=:reason, error_message=:note"
        elif kind == "action":
            updates += ", provider_task_id=NULL, error=:note"
        else:
            updates += ", error=:note"
        connection.execute(
            text(f"UPDATE {table} SET {updates} WHERE id=:id"),
            {
                "id": entity_id,
                "state": json.dumps(progress, ensure_ascii=False),
                "note": note,
                "status": status,
                "reason": None if succeeded else "provider_failed",
            },
        )
        pending = untracked_video_attempts(connection)
    for item in pending:
        print(
            f"{item.kind}:{item.entity_id} generation={item.generation_id or '(missing)'} state_sha256={item.state_sha256}",
        )
    if pending and not args.settled:
        raise RuntimeError("未决任务必须先按原供应商记录查实；本工具不发送远端请求或补录凭据")
    print("视频队列升级检查通过" if not pending else "已记录本项收尾，其余任务仍须核对")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settled", help="已按原供应商记录人工查实终态的 kind:id")
    parser.add_argument("--expected-state-sha256", help="检查输出的状态指纹，登记时必须完全一致")
    parser.add_argument("--generation-id", help="可选；提供时必须与已保存的制作标识完全一致")
    parser.add_argument("--outcome", choices=("succeeded", "failed", "cancelled"))
    parser.add_argument("--note")
    parser.add_argument("--result-url", help="已成功任务原有的下载地址，不重新提交制作")
    args = parser.parse_args()
    if args.settled and (not args.expected_state_sha256 or not args.outcome or not args.note):
        parser.error("登记收尾须提供 --expected-state-sha256、--outcome、--note")
    if args.expected_state_sha256 and not re.fullmatch(r"[a-f0-9]{64}", args.expected_state_sha256):
        parser.error("--expected-state-sha256 必须为检查输出的 64 位十六进制指纹")
    try:
        async with ENGINE.begin() as connection:
            await connection.run_sync(run, args)
    finally:
        await ENGINE.dispose()


if __name__ == "__main__":
    asyncio.run(main())
