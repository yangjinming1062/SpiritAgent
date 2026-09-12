"""自动化应用流程：cron 两轨回合的执行与终态交付。"""

from services.application.automation.cron_turns import execute_cron_turn
from services.application.automation.standard_turns import execute_standard_turn

__all__ = ["execute_cron_turn", "execute_standard_turn"]
