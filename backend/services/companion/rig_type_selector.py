import re
from typing import Protocol

from components import safe_json_loads
from sqlalchemy.ext.asyncio import AsyncSession

from services.llm import ProviderConfig

_RIG_TYPES: tuple[str, ...] = ("biped", "quadruped", "avian", "serpentine", "aquatic", "hexapod", "octopod")

_SYSTEM_PROMPT = (
    "你是一个 3D 角色分类助手。根据用户给出的物种完成两项判断，只输出一个 JSON 对象，不要其他文字：\n"
    '{"rig_type": "<七选一>", "has_humanoid_face": <true|false>}\n'
    "rig_type 七选一：\n"
    "biped：双足人形（人类、精灵、矮龙、人形机器人、猫娘等拟人化生物）；\n"
    "quadruped：四足动物（猫、狗、狼、马、鹿、兔子等）；\n"
    "avian：鸟类或有翼生物（鹰、凤凰、天使等）；\n"
    "serpentine：蛇形或龙形生物（蛇、龙等）；\n"
    "aquatic：鱼类或水生生物（鱼、海豚、人鱼等）；\n"
    "hexapod：六足生物（蚂蚁、甲虫等）；\n"
    "octopod：八足生物（蜘蛛、章鱼等）。\n"
    "has_humanoid_face 判断该物种的头部是否呈现类人面孔（五官比例接近人类）：精灵/猫娘/人鱼为 true，机械狼/独角兽/史莱姆为 false。"
)

_USER_TEMPLATE = "物种：{species}\n分类："

# 自定义伙伴以人形为主、二次元是产品的主要风格载体，故分类失败时退到主流路径而非小众路径
_DEFAULT_CLASSIFICATION: tuple[str, bool] = ("biped", True)


class _ChatFn(Protocol):
    async def __call__(
        self,
        db: AsyncSession | None,
        user_id: int | None,
        system_prompt: str,
        user_payload: str,
        *,
        provider_config: ProviderConfig | None = None,
    ) -> str: ...


async def classify_species(
    chat: _ChatFn,
    species: str,
    *,
    db: AsyncSession | None = None,
    user_id: int | None = None,
) -> tuple[str, bool]:
    """由 LLM 判定骨骼类型与是否类人面孔；任何异常都回落默认值而不抛错——判错只影响风格与动画库选择，不影响正确性。"""
    try:
        species_text = species.strip() or "人类"
        user_payload = _USER_TEMPLATE.format(species=species_text)
        raw = await chat(db, user_id, _SYSTEM_PROMPT, user_payload)
        match = re.search(r"\{.*\}", raw or "", re.DOTALL)
        data = safe_json_loads(match.group(0), default=None) if match else None
        if not isinstance(data, dict):
            return _DEFAULT_CLASSIFICATION
        candidate = str(data.get("rig_type", "")).strip().lower()
        face = data.get("has_humanoid_face")
        rig = candidate if candidate in _RIG_TYPES else "biped"
        return rig, (face if isinstance(face, bool) else True)
    except Exception:
        return _DEFAULT_CLASSIFICATION


async def select_rig_type(
    chat: _ChatFn,
    species: str,
    *,
    db: AsyncSession | None = None,
    user_id: int | None = None,
) -> str:
    """classify_species 的骨骼类型视图，供不关心类人面孔标志的调用方使用。"""
    return (await classify_species(chat, species, db=db, user_id=user_id))[0]
