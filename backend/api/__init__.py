import importlib
import pkgutil

from fastapi import APIRouter

from . import v1

# 自动发现 api/v1/*.py 模块的 ``router = get_router()`` 入口；无 router 的 helper（如 _http_errors）跳过。新增路由只需在 api/v1/ 下加文件，无需别处注册。
ROUTERS: list[APIRouter] = []
for _finder, _name, _is_pkg in pkgutil.iter_modules(v1.__path__, v1.__name__ + "."):
    _router = getattr(importlib.import_module(_name), "router", None)
    if isinstance(_router, APIRouter):
        ROUTERS.append(_router)

__all__ = ["ROUTERS"]
