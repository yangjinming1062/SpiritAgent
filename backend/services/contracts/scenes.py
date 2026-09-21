"""由真实回合创建、工具循环共享的场景操作预算。"""

import asyncio
from dataclasses import dataclass, field


@dataclass
class SceneTurnState:
    user_text: str = ""
    inspected: bool = False
    switch_claimed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
