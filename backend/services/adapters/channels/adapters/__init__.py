"""渠道适配器包：各适配器由 bootstrap/registrations.py 显式注册（业务包导入不产生副作用）。"""

from .weixin_ilink import WeixinIlinkAdapter

__all__ = ["WeixinIlinkAdapter"]
