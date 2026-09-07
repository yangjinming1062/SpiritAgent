import re

from components import (
    DEFAULT_LANGUAGE,
    TIME_NOTE_EN_HEAD,
    TIME_NOTE_ZH_HEAD,
    get_logger,
    resolve_prompt_text,
)
from modules.companion import CompanionExpression

from services.companion import BUILTIN_EMOTIONS

ALLOWED_LOCALES: frozenset[str] = frozenset({"home", "perch", "roam"})

# 锚定缓冲区开头的 tag 正则；target 允许非 ]/非换行字符，以容纳本地化应用名（如「微信」）与空格（如「Visual Studio Code」）。
_AFFECT_RE = re.compile(r"^\s*\[affect:([a-z_]+)\]\s*", re.IGNORECASE)
_MOOD_RE = re.compile(r"^\s*\[mood:([^\]]{1,200})\]\s*", re.IGNORECASE)
_SPATIAL_RE = re.compile(r"^\s*\[spatial:([a-z_]+)(?:,target:([^\]\n]+))?\]\s*", re.IGNORECASE)

# 部分匹配正则：flush() 时抢救流中断前的解析结果，避免把半截标签（如 [affect:foo）暴露给用户。
_PARTIAL_AFFECT_RE = re.compile(r"^\s*\[affect:([a-z_]+)?", re.IGNORECASE)
_PARTIAL_MOOD_RE = re.compile(r"^\s*\[mood:([^\]]*)", re.IGNORECASE)
_PARTIAL_SPATIAL_RE = re.compile(r"^\s*\[spatial:([a-z_]+)?(?:,target:([^\]\n]*))?", re.IGNORECASE)

# 星号包裹的第三人称动作旁白（不显示不朗读，仅由 [affect:...] 驱动 3D 反应）。
_ACTION_NARRATION_RE = re.compile(r"^\s*\*[^*]*\*\s*")
_PARTIAL_ACTION_RE = re.compile(r"^\s*\*[^*]*$")

# 结构化动作 tag：[action:slug] 由 LLM 命名具体肢体动作，客户端映射到动画 clip 并在缺失时回退到情绪 valence。
_ACTION_TAG_RE = re.compile(r"^\s*\[action:([a-z_]+)\]\s*", re.IGNORECASE)
_PARTIAL_ACTION_TAG_RE = re.compile(r"^\s*\[action:([a-z_]+)?", re.IGNORECASE)

# 防御性剥离：模型偶尔会把系统时间提示当成回复前缀输出。
_SYS_TIME_RE = re.compile(rf"^\s*{re.escape(TIME_NOTE_ZH_HEAD)}[^）]*）\s*")
_SYS_TIME_EN_RE = re.compile(rf"^\s*{re.escape(TIME_NOTE_EN_HEAD)}[^)]*\)\s*", re.IGNORECASE)
_PARTIAL_TIME_META_RE = re.compile(
    r"^\s*(?:" + re.escape(TIME_NOTE_ZH_HEAD.rstrip("：")) + r"[^）]*|" + re.escape(TIME_NOTE_EN_HEAD.rstrip(":")) + r"[^)]*)$",
    re.IGNORECASE,
)

# 合理上限：含长 app 名的真实 tag 远小于 256 字符；超出视为不可解析输入，由 scrubber 丢弃并以文本形式下传。
_MAX_TAG_LEN: int = 256

logger = get_logger(__name__)


# 双语 affect 引导模板：占位符 {emotions} / {actions} / {custom} / {locales} 由 build_affect_guidance 动态填充。
# emotion token (happy/sad/...) 与 spatial locale (home/perch/roam) 属协议层，不参与语言切换。

_AFFECT_GUIDANCE_TEXTS: dict[str, str] = {
    "zh": (
        "# 具身表情与动作\n"
        "你的屏幕头像通过面部表情、身体动画与空间定位来可见地表达情绪。"
        "若要表达情绪与肢体动作，请在回复开头独占一行写 affect tag 与可选的 mood/action/spatial tag：\n"
        "    [affect:EMOTION]\n"
        "    [mood:第一人称内心独白]  （可选；此刻心境，会展示给用户看，不要写决策理由）\n"
        "    [action:ACTION]  （可选；snake_case 具体动作，如 turn_away / stomp / nod）\n"
        "    [spatial:LOCALE,target:KEYWORD]  （可选）\n"
        "其后跟随你的实际对话回复。\n\n"
        "EMOTION must be one of: {emotions}.\n"
        "{actions}"
        "\n## 多模态表达规则：\n"
        "- **连续气泡（`---`）**：若要在同一回合内连续发多条短回复，在相邻回复之间放一行仅含 `---` 的内容；客户端会把它渲染成独立气泡并加短暂停顿。\n"
        "- **非语言 / 静默反应**：若只想用肢体表达而不说话，仅输出 tag（例如 `[affect:pout]\n[action:turn_away]`），不要附带任何文本。\n"
        "- **口语回复**：把对话内容直接接在 tag 后面。\n"
        "{custom}"
        "LOCALE must be one of: {locales}. KEYWORD is an active window or app name.\n"
        "示例：\n"
        "    [affect:happy]\n"
        "    [mood:见到你了，心里一下子亮起来]\n"
        "    见到你真开心！今天咱们一起做点什么？\n"
        "    [affect:curious]\n"
        "    [spatial:perch,target:bilibili]\n"
        "    那个视频看起来有意思！我陪你一起看。\n"
        "tag 会在用户看到前被剥掉，不要解释它们。"
    ),
    "en": (
        "# Embodied Expressions & Movements\n"
        "Your on-screen avatar visibly expresses emotions through facial expressions, body animations, and spatial positioning. "
        "To convey emotion and physical movement, begin your response with an affect tag and optional mood/action/spatial tags on their own lines:\n"
        "    [affect:EMOTION]\n"
        "    [mood:first-person inner state]  (optional; shown to the user as your current mood, not a system rationale)\n"
        "    [action:ACTION]  (optional; a specific movement in snake_case, e.g. turn_away / stomp / nod)\n"
        "    [spatial:LOCALE,target:KEYWORD]  (optional)\n"
        "followed by your actual conversational reply.\n\n"
        "EMOTION must be one of: {emotions}.\n"
        "{actions}"
        "\n## Multimodal Expression Rules:\n"
        "- **Consecutive Bubbles (`---`)**: To send multiple short consecutive replies inside one turn, put a line containing only `---` between consecutive replies; the client renders each segment as its own bubble with a brief pause.\n"
        "- **Non-Verbal / Silent Reactions**: To express purely through body language without speaking, output ONLY the tags (e.g. `[affect:pout]\n[action:turn_away]`) with no following text.\n"
        "- **Spoken Responses**: Put your conversational reply text directly after the tags.\n"
        "{custom}"
        "LOCALE must be one of: {locales}. KEYWORD is an active window or app name.\n"
        "Examples:\n"
        "    [affect:happy]\n"
        "    [mood:Seeing you brightens me up]\n"
        "    I'm glad to see you! What are we working on today?\n"
        "    [affect:curious]\n"
        "    [spatial:perch,target:bilibili]\n"
        "    That video looks interesting! I'll watch it together with you.\n"
        "The tags are stripped before the user sees your message, so never explain them."
    ),
}

_AFFECT_ACTIONS_TEXTS: dict[str, str] = {
    "zh": "Available action animations — 请从以下名字中精确选择 [action:...]：{names}。可在 affect tag 之后按播放顺序堆叠最多 3 个 [action:...] 行。\n",
    "en": "Available action animations — choose [action:...] from exactly these names: {names}. You may stack up to 3 [action:...] lines in playback order right after the affect tag.\n",
}

_AFFECT_CUSTOM_TEXTS: dict[str, str] = {
    "zh": "Custom emotion details（自定义情绪说明）：\n",
    "en": "Custom emotion details:\n",
}


def build_affect_guidance(
    custom_expressions: list[CompanionExpression] | None = None,
    available_actions: list[str] | None = None,
    *,
    language: str = DEFAULT_LANGUAGE,
) -> str:
    template = resolve_prompt_text(_AFFECT_GUIDANCE_TEXTS, language)
    emotions_set = set(BUILTIN_EMOTIONS)
    custom_desc_lines: list[str] = []
    if custom_expressions:
        for expr in custom_expressions:
            name = getattr(expr, "name", "")
            label = getattr(expr, "label", "")
            desc = getattr(expr, "description", "")
            if name:
                emotions_set.add(name)
                if desc or label:
                    desc_str = f" ({label}: {desc})" if desc else f" ({label})"
                    custom_desc_lines.append(f"- {name}{desc_str}")

    actions_clause = resolve_prompt_text(_AFFECT_ACTIONS_TEXTS, language).replace("{names}", ", ".join(sorted(set(available_actions)))) if available_actions else ""
    custom_clause = resolve_prompt_text(_AFFECT_CUSTOM_TEXTS, language) + "\n".join(custom_desc_lines) + "\n" if custom_desc_lines else ""

    # 使用 .replace() 而非 .format()：模板里的 {emotions}/{actions}/{custom}/{locales} 都是字面占位符，
    # 但 substituted values（来自 user-controlled clip_map keys / CompanionExpression.description）
    # 含 '{' / '}' 时 str.format() 会抛 KeyError；replace() 是字面替换，对花括号安全。
    return (
        template.replace("{emotions}", ", ".join(sorted(emotions_set)))
        .replace("{actions}", actions_clause)
        .replace("{custom}", custom_clause)
        .replace("{locales}", ", ".join(sorted(ALLOWED_LOCALES)))
    )


def _is_potential_prefix(buf: str) -> bool:
    """缓冲区可能仍是尚未到达的 tag / 时间提示 / 动作旁白前缀。"""
    s = buf.lstrip()
    if s.startswith("[") and "]" not in s:
        return True
    if s.startswith("*") and "*" not in s[1:]:
        return True
    if s.startswith(TIME_NOTE_ZH_HEAD.rstrip("：")) and "）" not in s:
        return True
    return s[:12].lower() == TIME_NOTE_EN_HEAD[:12].lower() and ")" not in s


class AffectScrubber:
    """从 LLM 流中剥离开头的 具身 tag、星号动作旁白与溢出的时间提示。"""

    # 单回合动作序列上限：更多动作在 2.5s 级情绪瞬态内播不完，且 LLM 有堆叠倾向。
    MAX_ACTIONS_PER_TURN: int = 3

    def __init__(self, allowed_emotions: frozenset[str] | None = None, allowed_actions: frozenset[str] | None = None) -> None:
        self._buf: str = ""
        self._emotion: str | None = None
        self._mood: str | None = None
        self._spatial_locale: str | None = None
        self._spatial_target: str | None = None
        self._actions: list[str] = []
        self._allowed: frozenset[str] = allowed_emotions if allowed_emotions is not None else BUILTIN_EMOTIONS
        self._allowed_actions: frozenset[str] | None = allowed_actions
        self._just_consumed_tag: bool = False

    @property
    def emotion(self) -> str | None:
        return self._emotion

    @property
    def mood(self) -> str | None:
        return self._mood

    @property
    def actions(self) -> list[str]:
        return list(self._actions)

    @property
    def spatial_locale(self) -> str | None:
        return self._spatial_locale

    @property
    def spatial_target(self) -> str | None:
        return self._spatial_target

    def feed(self, text: str) -> str:
        if not text:
            return text
        self._buf += text
        if self._just_consumed_tag:
            self._buf = self._buf.lstrip()
            if self._buf:
                self._just_consumed_tag = False
        return self._try_resolve()

    def flush(self) -> str:
        """流结束：再做一次完整匹配，再处理部分匹配。"""
        if not self._buf:
            return ""
        self._try_match_tags()
        m_aff = _PARTIAL_AFFECT_RE.match(self._buf)
        if m_aff:
            if m_aff.group(1):
                self._set_emotion(m_aff.group(1))
            self._consume(m_aff, strip_bracket=True)
        m_mood = _PARTIAL_MOOD_RE.match(self._buf)
        if m_mood:
            self._set_mood(m_mood.group(1))
            self._consume(m_mood, strip_bracket=True)
        m_spat = _PARTIAL_SPATIAL_RE.match(self._buf)
        if m_spat:
            self._consume(m_spat, strip_bracket=True)
        m_act_tag = _PARTIAL_ACTION_TAG_RE.match(self._buf)
        if m_act_tag:
            if m_act_tag.group(1):
                self._set_action(m_act_tag.group(1))
            self._consume(m_act_tag, strip_bracket=True)
        m_act = _PARTIAL_ACTION_RE.match(self._buf)
        if m_act:
            self._consume(m_act)
        m_time = _PARTIAL_TIME_META_RE.match(self._buf)
        if m_time:
            logger.warning("scrubbed partial time prefix from LLM response: %s", m_time.group(0).strip())
            self._consume(m_time, strip_bracket=True)
        out, self._buf = self._buf, ""
        return out

    def _try_resolve(self) -> str:
        self._try_match_tags()
        if _is_potential_prefix(self._buf) and len(self._buf) < _MAX_TAG_LEN:
            return ""
        out, self._buf = self._buf, ""
        return out

    def _try_match_tags(self) -> None:
        """就地消费 ``self._buf`` 开头的完整 tag 前缀。"""
        while True:
            m_aff = _AFFECT_RE.match(self._buf)
            if m_aff:
                self._set_emotion(m_aff.group(1))
                self._consume(m_aff)
                continue
            m_mood = _MOOD_RE.match(self._buf)
            if m_mood:
                self._set_mood(m_mood.group(1))
                self._consume(m_mood)
                continue
            m_spat = _SPATIAL_RE.match(self._buf)
            if m_spat:
                self._set_spatial(m_spat.group(1), m_spat.group(2))
                self._consume(m_spat)
                continue
            m_act_tag = _ACTION_TAG_RE.match(self._buf)
            if m_act_tag:
                self._set_action(m_act_tag.group(1))
                self._consume(m_act_tag)
                continue
            m_act = _ACTION_NARRATION_RE.match(self._buf)
            if m_act:
                self._consume(m_act)
                continue
            for ts_re in (_SYS_TIME_RE, _SYS_TIME_EN_RE):
                m_ts = ts_re.match(self._buf)
                if m_ts:
                    logger.warning("scrubbed hallucinated time prefix from LLM response: %s", m_ts.group(0).strip())
                    self._consume(m_ts)
                    break
            else:
                return

    def _set_emotion(self, token: str | None) -> None:
        if token is None:
            return
        normalized = token.lower()
        self._emotion = normalized if normalized in self._allowed else "neutral"

    def _set_mood(self, token: str | None) -> None:
        text = (token or "").strip()
        if not text:
            return
        self._mood = text[:200]

    def _set_spatial(self, loc: str | None, target: str | None) -> None:
        if loc is None:
            return
        normalized = loc.lower()
        self._spatial_locale = normalized if normalized in ALLOWED_LOCALES else None
        self._spatial_target = target

    def _set_action(self, token: str | None) -> None:
        if token is None:
            return
        normalized = token.lower()
        # 白名单 + 序列上限：幻觉动作名与超额堆叠就地丢弃——客户端无可兑现的姿势。
        if self._allowed_actions is not None and normalized not in self._allowed_actions:
            logger.info("drop action tag outside whitelist: %s", normalized)
            return
        if len(self._actions) >= self.MAX_ACTIONS_PER_TURN or normalized in self._actions:
            return
        self._actions.append(normalized)

    def _consume(self, m: re.Match[str], *, strip_bracket: bool = False) -> None:
        """将 ``self._buf`` 推进到匹配之后；可选地吃掉部分正则残留的尾随 ``]``。"""
        self._buf = self._buf[m.end() :]
        if strip_bracket and self._buf.startswith("]"):
            self._buf = self._buf[1:]
        self._buf = self._buf.lstrip()
        self._just_consumed_tag = not bool(self._buf)
