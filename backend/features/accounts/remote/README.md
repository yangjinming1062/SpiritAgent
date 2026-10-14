# 远程授权

本模块持有扫码授权、设备会话和提交账本。身份与恢复契约归 [远程访问](../../../../docs/PROTOCOL.md#远程访问)，REST 入口为 [remote.py](../../../entrypoints/http/routes/remote.py)。

`security.py` 管理令牌与内容接口策略，身份鉴权归 [auth/deps.py](../deps.py)；上传中间件在解析大请求体前调用相同鉴权。扫码兑换与账户安全变更按用户行锁串行化，撤权先提交数据库，再收敛连接与设备发起的回合。

`PromptSubmission` 的读写与重启中断标记归 [submissions.py](../../conversation/submissions.py)，账户受理态归 `runtime/chat_accounts.py`，连接适配归 `entrypoints/desktop`，跨业务调用归 `workflows/chat`。新增身份入口须同步内容准入、上传校验、限流及账户维护。
