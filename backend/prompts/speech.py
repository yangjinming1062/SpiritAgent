"""陪伴语音的朗读与控制语义；供应商可用选项由装配方提供。"""

SPEECH_PERFORMANCE_GUIDANCES: dict[str, str] = {
    "zh": (
        "\n# 语音表达\n"
        "先写此刻要对用户说的台词，再选择能让这句话听起来贴合语境的演绎。"
        "日常交流像接话，节奏随句意自然起伏；情绪强度来自这轮交流，不由亲密关系或人设标签自动放大。"
        "短句通常一个表达重点就够了，长句的变化跟随实际转折。相邻气泡保持同一说话者和连贯语气，"
        "每句的重音与收尾服从各自句意，不套用一段相同的起伏过程。\n"
        "speech 只控制本气泡怎样发声，使用下面列出的能力，不填写供应商或模型标识。"
        "常态朗读可以保留默认值；语速、拖音、气声、笑声和停顿按表达需要选择，不要求每句都有。"
        "以下是独立格式示例，不是固定台词或一轮必须生成的气泡数量。\n"
    ),
    "en": (
        "\n# Spoken delivery\n"
        "Write what you want to say to the user now, then choose delivery that fits those words in this exchange. "
        "Everyday chat should sound like responding to someone, with rhythm following the meaning. Emotional "
        "intensity comes from this exchange, not automatically from intimacy or persona labels. A short line "
        "usually needs one expressive focus; changes in a longer line should follow its actual turns. Keep "
        "the speaker and tone continuous across bubbles, while emphasis and endings follow each line's meaning "
        "instead of repeating the same emotional arc.\n"
        "speech controls only how this bubble sounds. Use the capabilities below without provider or model "
        "identifiers. Default values are valid for ordinary delivery. Choose speed changes, elongated sounds, "
        "breathiness, laughter, and pauses when the expression needs them, not in every line. The following "
        "are independent format examples, not fixed dialogue or a required number of bubbles.\n"
    ),
}

MIMO_SPEECH_GUIDANCES: dict[str, str] = {
    "zh": (
        "MiMo 的三种控制按用途选择，可以单独使用或配合使用。\n"
        "- instruction：可选的自然语言朗读指令，放入语音模型的 user 消息，不会被念出。"
        "通常一两句，写清这句是在怎样接话、想传达的态度，以及确有需要的节奏或重音；"
        "指向本气泡实际的词，不给短句安排它承载不了的多阶段表演。"
        "复杂表演可补充与发声有关的角色、当前情境和演绎要领，日常对话无需填写角色传记或场景描写。"
        "采用已选音色，稳定的音色和身份不用逐句重新设计。情境只取本轮已建立的信息，"
        "不加入台词和交流中没有的身体动作、视觉细节或事件；没有额外指导时用 null。\n"
        "- styles：整句的风格标签，合成为台词开头的 (风格)。日常通常零到两个，"
        "例如 开心、无奈、释然、温柔、俏皮；这些是示例，支持简短的自定义风格。"
        "只选覆盖整句的基调，局部变化放在 segments。instruction 已表达的同一要求不必再用标签堆叠；"
        "默认用 []，不按数量补齐。\n"
        "- segment.tag：在该段文字之前插入 [音频标签]，把变化放在实际发生的位置。"
        "例如 轻笑、叹气、吸气、哽咽，或 小声、语速加快、恢复平静 等局部说法。"
        "标签只写简短可听见的控制，不写完整的导演说明、眼神或肢体动作。"
        "语气变化放在需要变化的词前；后文需要回到原有语气时在回落处标记。"
        "笑声或吸气会产生额外声音，台词已有“哈哈”等发声文字时不要再重复制造同一个声音。"
        "instruction、styles 和 tag 要形成同一个表达意图，不互相矛盾。\n"
    ),
    "en": (
        "Choose among MiMo's three controls by purpose; they can be used separately or together.\n"
        "- instruction: optional natural-language delivery guidance sent in the speech model's user message, "
        "which is not spoken. Usually one or two sentences: how this line responds, its attitude, and any "
        "needed rhythm or emphasis on actual words in this bubble. Do not give a short line a multi-stage "
        "performance it cannot carry. A complex performance may add voice-relevant character, current "
        "situation, and acting guidance; everyday chat does not need a biography or scene description. "
        "Use the selected voice without redesigning stable voice qualities or identity for each line. "
        "Use only the situation established in this exchange, without adding bodily actions, visual "
        "details, or events absent from the words and conversation. Use null when no extra guidance is needed.\n"
        "- styles: whole-line labels rendered as (style) at the start. Usually zero to two for daily chat, "
        "such as 开心 (happy), 无奈 (resigned), 释然 (relieved), 温柔 (gentle), or 俏皮 (playful). These are "
        "examples; brief custom styles are supported. Choose a baseline covering the whole line; put local "
        "changes in segments. Do not pile on labels repeating the same instruction. Default to [], without "
        "filling a quota.\n"
        "- segment.tag: inserts [audio tag] before that segment's words, at the point of an audible change. "
        "For example 轻笑 (chuckle), 叹气 (sigh), 吸气 (inhale), 哽咽 (choked voice), or local delivery such "
        "as 小声 (quietly), 语速加快 (faster), and 恢复平静 (return to a calm tone). Use a brief audible "
        "control, not a full director instruction, gaze, or bodily gesture. Put a change before the words "
        "that need it and mark a return where the later words need the original tone. Laughter and inhaling "
        "add sounds; do not duplicate a sound already written in the dialogue, such as 'haha'. Keep "
        "instruction, styles, and tags consistent with the same expressive intent.\n"
    ),
}

SPEECH_SEGMENT_GUIDANCES: dict[str, str] = {
    "zh": (
        "segments：只在需要段内标记或停顿时分段，否则用 []，系统直接朗读 text。"
        "非空时，各段 text 按序拼接必须逐字还原本气泡 text，包括空格与标点；"
        "分段只是标记落点，不增加气泡，不改变台词。本轮提供 tag 时，放在变化开始的段上，其余段用 null；"
        "一个 tag 只写一个控制。空 text 的段可承载该位置的声音，包括句尾的声音。"
        "最多十二段，变化来自句意，不为分段添加语气词或凑标记。\n"
    ),
    "en": (
        "segments: split only when inline tags or pauses are needed; otherwise use [], and text is spoken "
        "directly. In a nonempty array, segment text values must concatenate in order to exactly this "
        "bubble's text, including spaces and punctuation. Segments locate controls without adding bubbles "
        "or changing the words. When tag is available, put it where a change starts and use null elsewhere; "
        "one tag holds one control. An empty-text segment can carry a sound at that position, including "
        "a closing sound. Use at most twelve segments, with changes following the meaning rather than "
        "adding interjections or markers to fill the structure.\n"
    ),
}
