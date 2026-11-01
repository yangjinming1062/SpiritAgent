"""伙伴实际呈现视图的中英文提示词。"""

# 呈现视图的提示词块使用模式无关的成品事实；窗口的静态投影与桌面的完整播放
# 由执行层分别适配，不能让宿主模式改变伙伴对当前生活的判断。
ACTION_VIEW_TEXTS: dict[str, dict[str, str]] = {
    "zh": {
        "presenting_image": "当前正在呈现一张已生成的图片或一段视频。",
        "presenting_static": "当前显示静态形象，没有播放图片或视频。",
        "presenting_unknown": "当前呈现的画面内容尚未识别。",
        "not_presenting": "当前没有呈现图片或视频。",
        "playable": "可以直接呈现的成品：{names}。",
        "playable_item": "{name}（{clip_kind}，visual_id {visual_id}）",
        "playable_separator": "，",
        "no_playable": "暂时没有可直接呈现的成品，可按情境用 visual_generate 生成一份。",
    },
    "en": {
        "presenting_image": "A generated picture or video is currently presented.",
        "presenting_static": "The companion is currently shown as a still look, with no picture or video playing.",
        "presenting_unknown": "The presented picture has not been identified.",
        "not_presenting": "No picture or video is currently presented.",
        "playable": "Ready to present: {names}.",
        "playable_item": "{name} ({clip_kind}, visual_id {visual_id})",
        "playable_separator": ", ",
        "no_playable": (
            "No finished picture or video is ready to present; use visual_generate when the situation fits."
        ),
    },
}
