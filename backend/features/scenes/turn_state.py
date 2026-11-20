"""同一回合的场景工具共用查库状态。"""

from dataclasses import dataclass


@dataclass
class SceneTurnState:
    inspected: bool = False
