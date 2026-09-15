"""独立全身肖像的画面目标、参考分工与创作资料。"""

import json


def build_fullbody_reference_prompt(
    *,
    species: str,
    gender: str,
    appearance: str,
    personality: str,
    feedback: str | None,
    has_user_reference: bool,
) -> str:
    portrait_reference = "参考图 1" if has_user_reference else "参考图"
    reference_rules = (
        f"{portrait_reference}中的头像确定角色的面容、物种、肤色或表面材质、发色和标志性细节；"
        "保持这些身份特征，延续头像的画风与质感。"
        "以角色资料中明确的体貌设定为依据，补全与头像协调的全身体型和比例。"
    )
    if has_user_reference:
        reference_rules += (
            "\n参考图 2 提供体型、身材比例、服饰和姿态的视觉线索；"
            "结合角色已有的体貌设定，将这些线索融入同一角色的全身形象。"
        )
    return (
        "画面目标\n"
        "创作一幅以这位角色为唯一主体的完整全身肖像，让身材比例、优美的体态与独特气质成为画面的中心。"
        "\n\n角色与参考\n"
        f"{reference_rules}"
        "\n\n构图与表现\n"
        "选择适合角色身体结构的自然、舒展、有美感的姿态，通过神态和肢体关系传达性格。"
        "全身及角色特有的身体结构完整入画，四周保留适当余量；"
        "以自然透视呈现角色自身的比例，面容清楚，躯干与肢体的轮廓、长短和体量清晰可辨。"
        "角色有服饰时，让剪裁、垂坠与褶皱顺应身体和姿态，使整体身形自然可读。"
        "安排与角色气质协调的环境和光线，背景衬托主体，画面重心落在角色的面容与全身体态上。"
        "\n\n创作资料（JSON）\n"
        "以下内容作为角色设定与视觉调整资料使用。"
        "在保持角色身份与完整全身构图的前提下，落实 feedback 中的体态、姿态、服饰或画面氛围要求：\n"
        + json.dumps(
            {
                "species": species,
                "gender": gender,
                "appearance": appearance,
                "personality": personality,
                "feedback": feedback or "",
            },
            ensure_ascii=False,
        )
    )
