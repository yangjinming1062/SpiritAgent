import asyncio

# 系统保存、用户保存与备份恢复共享，引用校验与提交之间不能删除其系统依赖。
configuration_lock = asyncio.Lock()
