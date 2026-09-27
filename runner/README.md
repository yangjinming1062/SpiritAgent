# Runner

执行本机终端、文件、浏览器、代码、进程、多模态、系统感知与 Skills 工具，并探测实际能力。不装配人格或管理对话，不持 Backend 登录凭据；模型请求经 Client 代理。

## 任务入口

| 改动 | 起点与必须联动 |
|---|---|
| 新增或禁用工具 | [registry.py](tools/registry.py)、[工具集 catalog](tools/toolsets/catalog.py) → [跨端同步契约](../docs/PROTOCOL.md#握手与工具同步) |
| RPC、能力与取消 | [server.py](server.py)、[call_journal.py](utils/call_journal.py)；核对 [Client 桥](../client/main/runner/rpc-ws.ts) |
| 本地与 SSH 执行 | [环境工厂](envs/factory.py)、[终端工具](tools/terminal/terminal_tool.py)、[清理](envs/cleanup.py) |
| 浏览器或桌面操作 | [浏览器 supervisor](tools/browser/supervisor.py)、[桌面工具](tools/multimodal/cu_tool.py)；真实应用状态验证 |
| 学习技能 | [skills_tool.py](tools/skills/skills_tool.py)、[skills_guard.py](tools/skills/skills_guard.py)；检查平台与作用域 |

## 设计意图

- 工具共享环境生命周期，各能力通过公共基础设施协作。
- 当前是高信任执行模式：对端准入和工具集开关构成执行授权，通用终端与文件工具保留本机访问能力。
- 网络、路径检查降低风险；不提供逐次裁决或通用强隔离沙箱。

## 代码边界

`server.py → tools → envs → utils`：连接派发、工具能力、环境与基础设施逐层依赖，底层不反向导入。环境清理与活跃进程检查经回调衔接。

## 连接、配置与能力

- Runner 主动连接 Client 的 OS IPC，重连前重读端点和 token。
- 配置仅保存在内存，由 Client 在执行前推送完整快照；传输和调用语义见 [PROTOCOL](../docs/PROTOCOL.md#本机工具)。
- 能力须实际探测系统 API，不以依赖可导入代替可用性。
- 进程启动生成唯一 `run_generation`，重连不轮换；周期探测变化才通知，异常撤销相应能力。
- Client 的消费限制见 [能力与进程代次](../docs/PROTOCOL.md#能力与进程代次)。
- 反向模型调用受 Client 桥实例累计预算限制；Runner 另有单连接守卫，重连只重置后者。
- 工作线程等待主循环，超时取消等待。
- 图片工具结果直接交付主回合，必要时缩图，不先调用另一模型转写。

## 执行环境

### 网络与路径

HTTP 统一使用 `httpx[socks]`。SSRF 在 URL 预检和实际建连时均校验；连接已检查 IP，保留原 Host、TLS SNI 和证书校验，每跳重定向重新检查，DNS 解析移出事件循环。

Windows 在启动阶段加入 Job Object 管理进程树，不在模块导入时产生此副作用。真实路径经句柄规范化，处理短名、链接、联接点和未创建后缀，比较忽略大小写并拒绝备用数据流。

### 终端与子进程

- 本地终端按 Git Bash / Darwin-BSD 提供环境约束并拦截不适用的 Linux 管理命令；SSH 不套本机规则。
- SSH 密码使用临时 askpass（OpenSSH ≥ 8.4，Windows ≥ 8.9），密钥优先，脚本随环境清理；密码和私钥路径作为本机机密处理。

代码子进程 RPC 使用一次性能力 token，首帧鉴权。依赖与平台 marker 显式声明，不在运行期补装，也不以可选导入掩盖漏依赖。

### 工具结果与实际效果

- 工具说明随实际能力使用：文件工具不可用或无法访问目标环境时可用终端；代码工具的导入帮助函数不解锁被禁用的能力，`retry` 只重试异常、不判断副作用是否安全。
- 桌面操作的后台支持取决于平台与动作，全局键盘和坐标操作可能影响前台；工具返回调用结果，实际应用效果须由后续状态核实。

## 调用日志与取消

- `utils.call_journal` 管原子认领、参数指纹、持有者和终态。
- 取消发生在认领线程未结束时，先等待认领收尾再记录未知结果；完成与取消写入串行，未持有认领凭据不得补写终态。
- 已保存结果重放不再次裁切，也不执行工具。
- 日志不可写、无调用标识、保留期及 unknown 的处理见 [PROTOCOL](../docs/PROTOCOL.md#调用日志与未知结果)；任务取消不证明线程或外部副作用已停止。

## 按预设学习技能

`ContextVar` 固定调用作用域并在结束时恢复。学习技能保存在 `$SPIRITAGENT_HOME/learned-skills/<user_id>/<system_preset_id>`，读取合并本域与静态技能，本域优先。

修改静态技能先复制到当前域，删除只影响本域副本；路径和符号链接不得跨域。社区技能执行威胁扫描和结构检查，技能自带忽略规则只对内置或可信来源生效。平台过滤与学习作用域分别校验，见 [PROTOCOL](../docs/PROTOCOL.md#skills-平台过滤)。

## 已知限制

通用终端保留本机权限，网络与路径校验不构成强沙箱；副作用恢复限制集中在 [调用日志契约](../docs/PROTOCOL.md#调用日志与未知结果)，不能以取消或查无记录证明操作未发生。

## 契约与验证

静态与 wheel 检查见 [Scripts](../scripts/README.md#按改动选择验证)。OS API、路径、终端和进程树在对应宿主验证，SSH 单独验证；调用修改覆盖取消、日志重放与作用域隔离。
