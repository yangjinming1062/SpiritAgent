"""给媒体创作者的选材、单帧构图和连续运动指引。"""

CHARACTER_ACTIVITY_POSE = (
    "结合角色自身的特点和此刻的意图选择动作、神态、关注对象与节奏。按实际身体结构、受力、接触和场地完成这个活动。"
    "保留创作目标中明确指定的动作与镜头。"
)
CHARACTER_FACE_MOTION = (
    "按指定的景别、焦点和活动拍摄。脸部在焦内时保留原画风中可见的眼睑、鼻尖、唇线与下颌细节。"
    "视角随指定动作改变时，同一张脸的透视、受光与遮挡连续变化；朝向保持时，五官的位置稳定。"
)

MEDIA_CHOICE_GUIDANCE = {
    "zh": "先判断这次表达靠什么成立：一个可见瞬间用图片，只有变化过程本身值得看才用视频。感受或想法用文字已能说清时不必配图；没有新的分享目的不必创作。",
    "en": "Choose the medium for what it conveys: one visible moment calls for a still, a change worth watching calls for video. A thought can stand as text; do not create a work without a fresh purpose. ",
}

IMAGE_GOAL_GUIDANCE = {
    "zh": "写一个可见瞬间。本人出镜时，根据自己的性格、当时的心情和具体互动决定怎样行动。用一两处关系摆好人、物和机位：关注对象在画面的什么方位，从镜头能看见角色哪一侧，或什么表面正在承重。让面容或标志性结构在这个角度可辨，身份、材质和画风交给参考图。画外细节不列为条件。",
    "en": "Describe one visible moment. For self depiction, choose how you act from your character, current mood and this interaction. Place the subject, object and camera in relation to each other. Give one or two visible cues, such as where the object of attention lies in frame, which side of the subject the camera sees, or which surface bears their weight. Keep distinguishing features readable from that angle. References supply likeness, materials and style. Leave offscreen details open. ",
}

VIDEO_GOAL_GUIDANCE: dict[str, str] = {
    "zh": (
        "写一个片长内看得清的主要变化：从什么状态开始、怎样自然变化、如何停稳或循环。"
        "本人出镜时，根据自己的意愿、性格与此刻关系选择怎样回应和行动，再写清起初朝向的对象或身体的支点，以及动作如何从这里开始。优先用一个稳定镜头看清关键接触，气质通过节奏表达，参考图决定角色外观与画风。"
        "保留本次明确要求及作品必需的主体、用品、支撑、过程、速度、频率与运镜；"
        "不自行添加面部操作或逐秒、逐部位脚本。安静陪伴或休息也可以是完整目标，"
        "无需为填满时长添加动作。目标与所选时长相称。"
    ),
    "en": (
        "Describe one change the viewer can follow within the clip: its starting state, natural motion and resting or looping end. "
        "For self depiction, decide how you respond from your intentions, temperament and this encounter. Say what the subject initially faces or rests on and how the movement starts there. Prefer a continuous stable shot that shows the necessary contacts. Let timing convey temperament and references supply likeness and style. Preserve explicit requirements for "
        "this creation and essential subjects, props, support, processes, speed, frequency and camera movement; "
        "do not invent facial operations or a second-by-second, body-part script. Quiet companionship or rest "
        "can be a complete goal without adding movement to fill the clip. Fit the goal to the duration. "
    ),
}
