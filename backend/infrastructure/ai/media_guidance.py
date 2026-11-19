"""给媒体创作者的选材、单帧构图和连续运动指引。"""

MEDIA_CHOICE_GUIDANCE = {
    "zh": "先判断这次表达靠什么成立：一个可见瞬间用图片，只有变化过程本身值得看才用视频。感受或想法用文字已能说清时不必配图；没有新的分享目的不必创作。",
    "en": "Choose the medium for what it conveys: one visible moment calls for a still, a change worth watching calls for video. A thought can stand as text; do not create a work without a fresh purpose. ",
}

IMAGE_GOAL_GUIDANCE = {
    "zh": "写一个可见瞬间；取景应能同时看清主体与真正重要的接触，画外细节不列为画面条件。本人出镜时让面容或标志性结构可辨，把身份、材质和画风交给参考图。",
    "en": "Describe one visible moment. Frame the subject and essential contacts together; do not make offscreen details conditions of the picture. For self depiction keep the face or distinguishing structure readable and let references supply identity, materials and rendering style. ",
}

VIDEO_GOAL_GUIDANCE: dict[str, str] = {
    "zh": (
        "写一个片长内看得清的主要变化：从什么状态开始、怎样自然变化、如何停稳或循环。"
        "优先用一个稳定镜头让面容和关键接触可见；气质通过动作节奏表达，参考图决定角色外观与画风。"
        "保留本次明确要求及作品必需的主体、用品、支撑、过程、速度、频率与运镜；"
        "不自行添加面部操作或逐秒、逐部位脚本。安静陪伴或休息也可以是完整目标，"
        "无需为填满时长添加动作。目标与所选时长相称。"
    ),
    "en": (
        "Describe one change the viewer can follow within the clip: its starting state, natural motion and resting or looping end. "
        "Prefer a continuous stable shot with the face and necessary contacts readable. Let timing convey temperament and references supply likeness and style. Preserve explicit requirements for "
        "this creation and essential subjects, props, support, processes, speed, frequency and camera movement; "
        "do not invent facial operations or a second-by-second, body-part script. Quiet companionship or rest "
        "can be a complete goal without adding movement to fill the clip. Fit the goal to the duration. "
    ),
}
