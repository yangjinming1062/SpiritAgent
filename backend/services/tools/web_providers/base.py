import abc
from typing import Any


class WebSearchProvider(abc.ABC):
    """Web 搜索/抽取后端的抽象基类。子类必须实现 :meth:`is_available` 与至少一个 :meth:`search` / :meth:`extract`；凭据按调用由 dispatcher 从 ``user_settings`` 注入。"""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Web 后端配置键中使用的稳定短标识（如 ``brave-free``、``ddgs``、``tavily``）。"""

    @property
    def display_name(self) -> str:
        """在 ``spiritagent tools`` 界面展示的易读标签。"""
        return self.name

    @abc.abstractmethod
    def is_available(self) -> bool:
        """供应商能服务调用时返回 True——必须廉价（env 变量、可选依赖、实例 URL），不可有网络 IO。"""

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        """实现了 :meth:`extract` 时返回 True。"""
        return False

    async def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        """执行一次 Web 搜索；当 :meth:`supports_search` 为 True 时由子类重写。"""
        raise NotImplementedError(f"{self.name} does not support search (override supports_search)")

    async def extract(self, urls: list[str], **kwargs: Any) -> Any:
        """从一个或多个 URL 抽取内容；返回 ``[{"url", "title", "content", "raw_content", "metadata": dict?, "error": str?}, ...]`` 形式，包装同步 HTTP 库的子类需在 ``search``/``extract`` 内部用 :func:`asyncio.to_thread` 避免阻塞事件循环。"""
        raise NotImplementedError(f"{self.name} does not support extract (override supports_extract)")

    def missing_credential_message(self) -> str | None:
        """``is_available() == False`` 时给用户的可操作提示；仅在 dispatcher 显式选择该供应商（非静默回退）时调用，``None`` 回退到通用「X 未配置」文案。"""
        return None
