# 远程授权

负责扫码配对与设备会话，身份和恢复约束见 [远程访问](../../../../docs/PROTOCOL.md#远程访问)。

[use_cases.py](use_cases.py) 管理配对、兑换和撤权；[security.py](security.py) 定义令牌、Cookie、Origin 与撤权操作；账户鉴权由 [deps.py](../deps.py) 提供，HTTP 与连接收尾在 [remote.py](../../../entrypoints/http/routes/remote.py)。上传中间件在解析大请求体前使用同一身份校验。

兑换、刷新及账户安全变更按账户锁串行化，再锁定待兑换记录。设备令牌只保存哈希；撤权先提交数据库，再由入口关闭对应连接和释放租约。已受理回合由账户运行态持有，设备断开不能撤销已持久受理。

用户提交账本与重启中断标记归 [会话 submissions](../../conversation/submissions.py)，受理态归 [chat_accounts.py](../../../runtime/chat_accounts.py)，业务执行归 [对话编排](../../../workflows/chat/README.md)。新增身份入口须同时核对内容准入、上传、限流和账户维护；扫码重放、并发撤权、重连及已受理任务按 [Scripts](../../../../scripts/README.md) 验证。
