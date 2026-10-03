import abc
from typing import Literal

from pydantic import BaseModel, Field


class WebSearchItem(BaseModel):
    title: str
    url: str
    description: str
    position: int


class WebSearchData(BaseModel):
    web: list[WebSearchItem] = Field(default_factory=list)


class WebSearchResult(BaseModel):
    success: bool
    data: WebSearchData | None = None
    error: str | None = None


class WebDocument(BaseModel):
    url: str
    title: str = ""
    content: str = ""
    error: str | None = None
    content_kind: Literal["extracted_text", "summary"] = "extracted_text"
    source_excerpted: bool = False
    summarization_failed: bool = False


class WebSearchProvider(abc.ABC):
    """Web 搜索/抽取后端的抽象基类。子类必须实现 :meth:`is_available` 与至少一个 :meth:`search` / :meth:`extract`；凭据在构造时由 dispatcher 从系统设置传入。"""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Web 后端配置键中使用的稳定短标识（如 ``brave-free``、``ddgs``、``tavily``）。"""

    @property
    def display_name(self) -> str:
        """工具错误信息中使用的易读名称。"""
        return self.name

    @abc.abstractmethod
    def is_available(self) -> bool:
        """供应商能服务调用时返回 True——必须廉价（env 变量、可选依赖、实例 URL），不可有网络 IO。"""

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        """实现了 :meth:`extract` 时返回 True。"""
        return False

    async def search(self, query: str, limit: int = 5) -> WebSearchResult:
        """执行一次 Web 搜索；当 :meth:`supports_search` 为 True 时由子类重写。"""
        raise NotImplementedError(f"{self.name} does not support search (override supports_search)")

    async def extract(self, urls: list[str]) -> list[WebDocument]:
        """抽取页面为文档，失败保留 URL 与错误；同步 HTTP 库须在线程运行。"""
        raise NotImplementedError(f"{self.name} does not support extract (override supports_extract)")

    def missing_credential_message(self) -> str | None:
        """``is_available() == False`` 时给用户的可操作提示；仅在 dispatcher 显式选择该供应商（非静默回退）时调用，``None`` 回退到通用「X 未配置」文案。"""
        return None
