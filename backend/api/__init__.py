import importlib
import pkgutil

from fastapi import APIRouter

from . import v1

# 自动发现 api/v1/*.py 模块并收集 router 入口：通过 ``router = get_router()`` 声明、无 router 的 helper（如 _http_errors）跳过。新增路由只需在 api/v1/ 下加文件，无需别处注册。
ROUTERS: list[APIRouter] = []
for _finder, _name, _is_pkg in pkgutil.iter_modules(v1.__path__, v1.__name__ + "."):
    _module = importlib.import_module(_name)
    _router = getattr(_module, "router", None)
    if isinstance(_router, APIRouter):
        ROUTERS.append(_router)
    # 次级 router（如无需鉴权的伙伴文件服务）按 public_router 显式声明，让主 router 保持默认发现契约。
    _public = getattr(_module, "public_router", None)
    if isinstance(_public, APIRouter):
        ROUTERS.append(_public)

__all__ = ["ROUTERS"]
