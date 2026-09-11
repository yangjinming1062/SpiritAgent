from typing import Protocol

from components import get_logger, safe_json_loads
from sqlalchemy.ext.asyncio import AsyncSession

from services.llm import ProviderConfig

from .persona_service import normalize_persona_aliases

logger = get_logger(__name__)

# 按骨骼类型分类的初始种子词汇表（与客户端 PERSONALITY_TAG_SEED_BY_RIG 一致）。
_TAG_SEEDS_BY_RIG: dict[str, list[str]] = {
    "common": [
        "温顺",
        "警惕",
        "敏锐",
        "暴躁",
        "好奇",
        "沉稳",
        "灵动",
        "威严",
        "幼态",
        "迟钝",
        "好斗",
        "懒散",
        "忠诚",
        "狡黠",
        "胆小",
        "敏捷",
        "神秘",
        "亲人",
        "独立",
        "贪吃",
    ],
    "biped": [
        "活泼",
        "好动",
        "元气",
        "安静",
        "慵懒",
        "热血",
        "文静",
        "温柔",
        "温婉",
        "体贴",
        "暖心",
        "冷漠",
        "高冷",
        "清冷",
        "孤僻",
        "俏皮",
        "调皮",
        "搞怪",
        "呆萌",
        "软萌",
        "中二",
        "幽默",
        "腹黑",
        "傲娇",
        "毒舌",
        "霸道",
        "强势",
        "叛逆",
        "内敛",
        "严肃",
        "妖娆",
        "妩媚",
        "性感",
        "清纯",
        "仙气",
        "贵气",
        "优雅",
        "阳光",
        "开朗",
        "忧郁",
        "敏感",
        "神经质",
        "细腻",
        "多愁善感",
        "理性",
        "冷静",
        "知性",
        "聪明",
        "博学",
        "严谨",
        "粘人",
        "害羞",
        "社恐",
        "社牛",
        "体面",
        "随和",
    ],
    "quadruped": [
        "护主",
        "撒娇",
        "狂野",
        "贪玩",
        "拆家",
        "顺从",
        "凶猛",
        "护食",
        "捕猎",
        "摇尾",
        "欢腾",
        "憨厚",
        "警戒",
        "领地意识",
        "爱抚",
        "温顺可爱",
        "精力充沛",
        "机警敏捷",
    ],
    "avian": [
        "高傲",
        "翱翔",
        "啼鸣",
        "聒噪",
        "俯冲",
        "求偶",
        "高贵",
        "灵巧",
        "孤傲",
        "机敏",
        "轻盈",
        "展翅",
        "鸣啭",
        "华丽",
        "警觉锐利",
        "从容不迫",
        "羽翼丰满",
    ],
    "serpentine": [
        "冷酷",
        "潜伏",
        "致命",
        "蜕变",
        "缠绕",
        "森冷",
        "剧毒",
        "幽暗",
        "诡谲",
        "隐忍",
        "冰冷",
        "吐信",
        "盘踞",
        "阴翳",
        "迅捷突袭",
        "神秘莫测",
    ],
    "aquatic": [
        "悠游",
        "静谧",
        "深邃",
        "跃动",
        "浮游",
        "群居",
        "洄游",
        "幻彩",
        "纯净",
        "游弋",
        "吐泡",
        "摆尾",
        "空灵",
        "波澜不惊",
        "如鱼得水",
        "灵波荡漾",
    ],
    "hexapod": [
        "勤劳",
        "秩序",
        "机械",
        "群集",
        "工蜂",
        "蛰伏",
        "探索",
        "坚韧",
        "服从",
        "狂躁",
        "筑巢",
        "拟态",
        "触角敏锐",
        "冷酷高效",
        "甲壳坚硬",
    ],
    "octopod": [
        "多智",
        "伪装",
        "莫测",
        "怪诞",
        "克苏鲁",
        "多面",
        "诡异",
        "探知",
        "喷墨",
        "触手灵动",
        "不可名状",
        "洞察",
        "狡诈多端",
        "深海潜行",
        "柔韧变幻",
    ],
}

_SYSTEM_PROMPT = (
    "你是一个角色性格与行为标签分析专家。根据用户给出的伙伴设定（名称、性格描述、说话风格、生物物种等），"
    "提炼并输出 3 到 10 个最能概括其性格特征、处事态度或生物形态行为的简短中文标签（每个标签 2-4 字）。\n"
    "要求：\n"
    "1. 优先参考给出的候选种子词汇；\n"
    "2. 如果种子词汇无法充分表达该角色的独特气质（如赛博朋克、神魔幻化、特殊习性等），允许并鼓励自创新标签；\n"
    '3. 必须输出纯 JSON 字符串数组格式，例如 ["活泼", "傲娇", "忠诚"]，不要任何 markdown 标记、解释或多余字符。'
)


class ChatFn(Protocol):
    async def __call__(
        self,
        db: AsyncSession | None,
        user_id: int | None,
        system_prompt: str,
        user_payload: str,
        *,
        provider_config: ProviderConfig | None = None,
    ) -> str: ...


async def analyze_personality_tags(
    chat: ChatFn,
    definition_json: str,
    user_id: int | None = None,
    *,
    species: str | None = None,
    rig_type: str | None = None,
    db: AsyncSession | None = None,
    provider_config: ProviderConfig | None = None,
) -> list[str]:
    """LLM 分析 persona 设定，返回 3-10 个去重后的性格标签；不过滤自创标签。"""
    try:
        raw_data = safe_json_loads(definition_json, default={})
        data = normalize_persona_aliases(raw_data) if isinstance(raw_data, dict) else {}

        char_species = species or data.get("biological_type", "人类")
        char_rig = rig_type or "biped"

        rig_seeds = _TAG_SEEDS_BY_RIG.get(char_rig, _TAG_SEEDS_BY_RIG["biped"])
        common_seeds = _TAG_SEEDS_BY_RIG["common"]
        candidate_seeds = list(dict.fromkeys(common_seeds + rig_seeds))

        user_payload = (
            f"角色设定如下：\n"
            f"- 名字: {data.get('name', '伙伴')}\n"
            f"- 物种: {char_species} (骨骼类型: {char_rig})\n"
            f"- 性格描述: {data.get('personality') or '未设定'}\n"
            f"- 说话风格: {data.get('speaking_style', '')}\n\n"
            f"候选种子词参考：{', '.join(candidate_seeds[:40])}\n"
            f"请输出 3-10 个标签 JSON 数组："
        )

        raw = await chat(db, user_id, _SYSTEM_PROMPT, user_payload, provider_config=provider_config)
        cleaned_raw = raw.strip()
        if cleaned_raw.startswith("```"):
            cleaned_raw = cleaned_raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        parsed = safe_json_loads(cleaned_raw, default=None)
        if isinstance(parsed, list):
            tags = [str(t).strip() for t in parsed if str(t).strip()]
        else:
            # 兼容非 JSON 逗号/换行分隔
            tags = [
                t.strip(" \"'[]\n\r\t")
                for t in cleaned_raw.replace("，", ",").replace("\n", ",").split(",")
                if t.strip(" \"'[]\n\r\t")
            ]

        # 去重且保持顺序
        if deduped := list(dict.fromkeys(tags)):
            return deduped[:10]

        return []
    except Exception:
        logger.warning("Failed to analyze personality tags with LLM", exc_info=True)
        return []
