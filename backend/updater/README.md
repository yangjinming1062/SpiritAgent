# 服务更新器

独立容器执行管理后台发起的服务更新，范围固定为 `backend`；不承担业务请求。一次性接入和日常操作见 [Backend](../README.md#服务更新)，本页维护执行器边界。

## 关键入口

| 修改任务 | 入口 |
|---|---|
| 启动、挂载与连接 | [__main__.py](__main__.py)、[config.py](config.py)、[server.py](server.py) |
| 预检、执行和运行版本核对 | [engine.py](engine.py)、[compose.py](compose.py) |
| 仓库探测与 fast-forward | [gitops.py](gitops.py) |
| 子进程、状态、日志与脱敏 | [runner.py](runner.py)、[state.py](state.py)、[redact.py](redact.py) |

## 部署与执行边界

仓库按宿主机绝对路径同路径挂载，Compose 在其 `backend/` 下运行，项目名、覆盖文件和环境变量沿该目录的 `.env` 解析，防止操作另一组容器或卷。部署密钥和 `known_hosts` 从 `deploy/` 只读挂载，凭据不写入日志或状态。

代码只以 fast-forward 推进，目标必须仍是检查时所选 upstream 最新提交。Git 决定本地修改能否安全保留；分叉、覆盖风险或获取失败即停止，不自动暂存或清理。同版本部署重试仍执行 Git 检查。容器以 root 操作仓库，新写文件可能需要宿主机相应权限处理。

后端通过共享 Unix socket 调用，容器回环入口仅用于内部排障，不映射宿主机端口。响应契约 [contracts.py](contracts.py) 与 [service_schemas.py](../features/updates/service_schemas.py) 同步；Backend 校验完整响应，非法任务或预检项返回错误，不能过滤后呈现为空状态。

## 中断与验证

任务和有界日志存于专用卷，保留与阶段超时由 `config.py` 定义。重启将遗留任务标为中断，运行状态重新查询；子进程在超时、取消及异常时收尾。代码已拉取不等于部署成功，须核对健康状态与运行镜像提交标识；失败不自动回退镜像或数据库。

Git 用临时仓库覆盖正常更新、保留/冲突修改、未跟踪覆盖、获取失败、分叉、远端变化和同版本重试；Compose 用隔离项目验证目录身份、构建/健康失败与中断恢复。检查命令归 [Scripts](../../scripts/README.md)。
