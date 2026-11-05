"""从候选的权威视觉检查汇总失败依据。"""

from features.media.review_contracts import VisualReview


def quality_failure_message(review: VisualReview | None) -> str | None:
    if review is None:
        return None
    if review.verdict == "pass":
        return None
    failures: list[str] = []
    labels = {"identity": "身份", "goal": "画面要求", "quality": "画面质量"}
    for name in review.required:
        decision = getattr(review, name)
        if decision is not None and decision.verdict == "pass":
            continue
        label = labels[name] + ("未通过" if decision and decision.verdict == "fail" else "待复核")
        failures.append(f"{label}：{decision.reason}" if decision else label)
    return "；".join(failures) or None
