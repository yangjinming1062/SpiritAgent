#!/usr/bin/env python3
"""读取指定账户最近七天内需要纠偏、择优或没有成品的聊天和动态制作样本。"""

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import bootstrap.models  # noqa: E402,F401 -- 注册全部 ORM 关系
from features.media.chain import MediaChainState  # noqa: E402
from features.media.character_images import ImageChainState  # noqa: E402
from features.media.models import ImageGenJob, VideoGenJob  # noqa: E402
from features.posts.publication_models import PostPublication  # noqa: E402
from infrastructure.ai.providers.local.image import composition_noise_seed  # noqa: E402
from infrastructure.assets.asset_store import resolve_asset_reference  # noqa: E402
from infrastructure.database import SESSION_LOCAL  # noqa: E402
from infrastructure.job_models import JobRecord  # noqa: E402
from infrastructure.text import utc_now  # noqa: E402
from sqlalchemy import func, or_, select, text  # noqa: E402


async def report(user_id: int, job_id: int | None) -> None:
    async with SESSION_LOCAL() as db:
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        since = utc_now() - timedelta(days=7)
        if job_id:
            video_id = await db.scalar(
                select(PostPublication.video_job_id).where(
                    PostPublication.id == job_id, PostPublication.user_id == user_id
                )
            )
            job_id = video_id or job_id
        query = (
            select(
                JobRecord.id, JobRecord.kind, JobRecord.state, JobRecord.stage, JobRecord.error, JobRecord.checkpoint
            )
            .where(
                JobRecord.user_id == user_id,
                JobRecord.kind.in_(("media.image", "media.video", "post.publish")),
                or_(JobRecord.finished_at >= since, (JobRecord.state == "unknown") & (JobRecord.updated_at >= since)),
                JobRecord.checkpoint.is_not(None),
            )
            .order_by(func.coalesce(JobRecord.finished_at, JobRecord.updated_at).desc(), JobRecord.id.desc())
        )
        if job_id:
            query = query.where(JobRecord.id == job_id)
        async for job, kind, state, stage, error, checkpoint in await db.stream(query.execution_options(yield_per=16)):
            if kind == "media.video":
                chain = MediaChainState.model_validate(checkpoint)
                params = await db.scalar(select(VideoGenJob.params_json).where(VideoGenJob.id == job))
                goal = params["goal"]
                settings = {key: params[key] for key in ("resolution", "aspect_ratio", "duration")}
                slots = 1
            else:
                raw = checkpoint.get("chain" if kind == "media.image" else "image_chain")
                chain = ImageChainState.model_validate(raw or {})
                if chain.inputs:
                    goal, slots = chain.daily_goal, chain.inputs.n
                    settings = chain.inputs.model_dump(include={"size", "aspect_ratio"})
                elif kind == "media.image":
                    params = await db.scalar(select(ImageGenJob.params_json).where(ImageGenJob.id == job))
                    goal, slots, settings = params["goal"], len(params["media_ids"]), {"size": params["size"]}
                else:
                    plan, child = (
                        await db.execute(
                            select(PostPublication.plan_json, PostPublication.video_job_id).where(
                                PostPublication.id == job
                            )
                        )
                    ).one()
                    if child or not plan or plan.get("content_type") not in {"image", "video"}:
                        continue
                    goal, slots = plan["prompt"], 1
                    settings = {key: plan.get(key) for key in ("size", "aspect_ratio", "duration")}
            for slot in range(slots):
                items = [c for c in chain.candidates if c.slot == slot]
                choice = chain.selections.get(slot)
                if choice and choice.mode == "passed" and len(items) == 1 and not items[0].attempt and not job_id:
                    continue
                candidates = []
                for item in items:
                    review = item.review
                    reviewed_evidence = review.evidence if review else None
                    evidence = reviewed_evidence or item.evidence
                    evidence_context = json.loads(evidence.context_json) if evidence else {}
                    provider = chain.providers[item.attempt] if item.attempt < len(chain.providers) else None
                    seed = chain.local_seed(item.attempt, slot) if provider and provider.provider == "local" else None
                    composition = (
                        seed is not None
                        and isinstance(chain, ImageChainState)
                        and chain.pose_correction(item.attempt, slot)[1]
                    )
                    correction = chain.local_correction if chain.is_correction_attempt(item.attempt) else None
                    opening_instruction = correction.opening_frame_instruction if correction else None
                    opening_composition = (
                        seed is not None
                        and kind == "media.video"
                        and params["identity_reference_role"] == "portrait"
                        and len(params["reference_images"]) == 2
                        and (
                            (opening_instruction and chain.needs_pose_correction(item.attempt, slot))
                            or (not opening_instruction and params.get("compose_activity_opening"))
                        )
                    )
                    local = resolve_asset_reference(item.path)
                    candidates.append(
                        {
                            "file": str(local[0]) if local else None,
                            "attempt": item.attempt,
                            "provider": provider.provider if provider else None,
                            "model": provider.model if provider else None,
                            "size": (provider.video_resolution or provider.image_size) if provider else None,
                            "noise_seed": str(seed) if seed is not None else None,
                            "composition_noise_seed": str(composition_noise_seed(seed)) if composition else None,
                            "opening_composition_noise_seed_if_used": str(composition_noise_seed(seed))
                            if opening_composition
                            else None,
                            "checks": review.model_dump(
                                include={
                                    "status",
                                    "identity",
                                    "goal",
                                    "quality",
                                    "motion",
                                    "face",
                                    "opening",
                                    "steps_before_stop",
                                    "step_requirement",
                                    "correction_instruction",
                                    "opening_frame_instruction",
                                    "fixed_ambient_scene",
                                }
                            )
                            if review
                            else None,
                            "motion_profile": evidence_context.get("goal_quality", evidence_context).get(
                                "motion_profile"
                            ),
                            "basis_id": reviewed_evidence.content_basis_id if reviewed_evidence else None,
                            "media_sha256": evidence.media_hash if evidence else None,
                            "reference_hashes": list(
                                dict.fromkeys(e.sha256 for e in evidence.images if "参考图" in e.role)
                            )
                            if evidence
                            else [],
                        }
                    )
                print(
                    json.dumps(
                        {
                            "job": job,
                            "kind": kind,
                            "state": state,
                            "stage": stage,
                            "error": error,
                            "slot": slot,
                            "goal": goal,
                            "request": settings,
                            "correction": chain.local_correction.reason if chain.local_correction else None,
                            "correction_instruction": chain.local_correction.instructions.get(slot)
                            if chain.local_correction
                            else None,
                            "observed_hold": chain.local_correction.observed_hold.model_dump()
                            if chain.local_correction and chain.local_correction.observed_hold
                            else None,
                            "decision": choice.model_dump() if choice else None,
                            "candidates": candidates,
                        },
                        ensure_ascii=False,
                    )
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--job-id", type=int)
    args = parser.parse_args()
    asyncio.run(report(args.user_id, args.job_id))
