"""视频生成目标的通用描述指引。"""

VIDEO_GOAL_GUIDANCE: dict[str, str] = {
    "zh": (
        "用简洁、正面的描述说明希望传达的效果、活动目标和必要的场景条件。"
        "角色出镜时点明与目标相关的性格气质，让视频模型结合参考形象与实际身体自主决定具体表演。"
        "保留本次明确要求及作品必需的主体、用品、支撑、过程、速度、频率与运镜；"
        "不自行添加面部操作或逐秒、逐部位脚本。安静陪伴或休息也可以是完整目标，"
        "无需为填满时长添加动作。目标与所选时长相称。"
    ),
    "en": (
        "Use concise, positive descriptions of the intended effect, activity goal and necessary setting. "
        "When a character appears, include relevant personality or temperament and let the video model choose "
        "the performance using the reference appearance and actual body. Preserve explicit requirements for "
        "this creation and essential subjects, props, support, processes, speed, frequency and camera movement; "
        "do not invent facial operations or a second-by-second, body-part script. Quiet companionship or rest "
        "can be a complete goal without adding movement to fill the clip. Fit the goal to the duration. "
    ),
}
