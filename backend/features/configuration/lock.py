import asyncio

# 系统设置与模型配置保存共享，引用校验与提交之间不能删除其系统依赖。
configuration_lock = asyncio.Lock()
