"""由真实回合创建、工具循环共享的场景操作预算。"""

import asyncio
from dataclasses import dataclass, field


@dataclass
class SceneTurnState:
    inspected: bool = False
    # 创建与切换分别限一次；创建并申请自动启用时同时占用切换额度。
    create_claimed: bool = False
    switch_claimed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
