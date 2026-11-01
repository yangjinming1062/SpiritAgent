"""汇总独立质量检查的失败原因，供结算和历史任务展示共用。"""

from features.media.chain import MEDIA_IDENTITY_ACCEPT_SCORE


def quality_failure_message(
    identity_score: int | None,
    identity_reason: str,
    review_verdict: str | None,
    review_reason: str,
) -> str | None:
    failures: list[str] = []
    if identity_score is None:
        failures.append("角色还原度检查未完成")
    elif identity_score < MEDIA_IDENTITY_ACCEPT_SCORE:
        message = f"角色还原度未通过（{identity_score} 分，需至少 {MEDIA_IDENTITY_ACCEPT_SCORE} 分）"
        failures.append(f"{message}：{identity_reason}" if identity_reason else message)
    if review_verdict != "pass":
        message = "画面要求检查未通过" if review_verdict else "画面要求检查未完成"
        failures.append(f"{message}：{review_reason}" if review_reason else message)
    elif failures:
        failures.append("画面要求检查已通过")
    return "；".join(failures) if failures else None
