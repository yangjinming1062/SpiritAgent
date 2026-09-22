"""内置工具包：各工具模块提供 register(registry)，注册由 bootstrap 显式触发。"""

from .action_tool import register as register_actions
from .companion_wait_tool import register as register_companion_wait
from .image_generation_tool import register as register_image_generation
from .journal_tool import register as register_journal
from .scene_tool import register as register_scene
from .send_message_tool import register as register_send_message
from .video_generation_tool import register as register_video_generation
from .web_tools import register as register_web

__all__ = [
    "register_actions",
    "register_companion_wait",
    "register_image_generation",
    "register_journal",
    "register_scene",
    "register_send_message",
    "register_video_generation",
    "register_web",
]
