"""跨层词汇：子 Agent 委派控制动作。

工具适配层校验参数后产出 `DelegateAction`，对话执行层接管子回合——
工具处理器不反向导入对话入口。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DelegateAction:
    task_description: str
    parent_session_id: str | None
