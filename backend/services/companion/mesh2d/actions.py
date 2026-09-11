"""2D 动作白名单 — LLM 可触发动作词表（客户端 PuppetStage 动作包络按同名兑现）。"""

DEFAULT_ACTIONS: frozenset[str] = frozenset(
    {
        "wave_right",
        "wave_left",
        "present_right",
        "present_left",
        "point_right",
        "point_left",
        "hands_on_hip",
        "hair_touch",
        "spread_arms",
        "look_away_left",
        "look_away_right",
        "turn_body_left",
        "turn_body_right",
        "lean_forward",
        "shy",
        "idle_glance",
        "petting",
        "dizzy",
        "edge_cling",
        "click",
        "long_press",
        "drag_end",
    },
)

# 本地物理 / 交互触发动作：脱离触发上下文播放会是悬空姿态，注入 LLM 清单时排除。
NON_LLM_ACTIONS: frozenset[str] = frozenset(
    {"edge_cling", "click", "long_press", "drag_end"},
)
