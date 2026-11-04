"""分别汇总身份与画面要求的定性依据。"""


def quality_failure_message(
    identity_verdict: str | None,
    identity_reason: str,
    review_verdict: str | None,
    review_reason: str,
    *,
    require_identity: bool = True,
) -> str | None:
    failures: list[str] = []
    if require_identity and identity_verdict != "pass":
        label = "身份不一致" if identity_verdict == "fail" else "身份检查待复核"
        failures.append(f"{label}：{identity_reason}" if identity_reason else label)
    if review_verdict != "pass":
        label = "画面要求未通过" if review_verdict == "fail" else "画面要求待复核"
        failures.append(f"{label}：{review_reason}" if review_reason else label)
    return "；".join(failures) or None
