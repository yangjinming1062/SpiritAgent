# Runner

## 1. 职责与边界

在用户本机执行终端、文件、浏览器、代码、系统感知、音频与 Skills 工具，并上报真实能力；不装配人设、管理对话或持有后端凭据。需要模型能力时经客户端反向代理。全局边界见 [ARCHITECTURE.md §1](../docs/ARCHITECTURE.md)。

## 2. 设计意图

- 环境状态与工具逻辑分离，多类工具共享环境生命周期而不互相依赖；包边界见 §3。
- 能力上报必须真实探测设备与系统 API，避免仅检查模块存在就让界面展示不可用能力。
- 社区 Skills 强制威胁扫描与结构检查；技能自带的忽略文件只对内置或可信来源生效，防止不可信输入关闭自身门禁。
- 每个客户端启动专属 Runner，按进程隔离本地权限、环境变量与执行上下文。

## 3. 架构地图

依赖方向：`utils/` ← `envs/` ← `tools/` ← `server.py`；底层不反向依赖上层。

`envs/` 管理本地 / SSH 环境与共享生命周期，工具统一从它获取环境能力，清理与活跃进程检查经回调解耦。`tools/` 按终端、文件、浏览器、代码、进程、Skills、多模态与系统能力分域。

wheel 发布与安装布局见 [scripts/README.md](../scripts/README.md) 和 [installer/README.md](../installer/README.md)。

## 4. 关键设计决策

- 本地 IPC：主动连接客户端端点，采用 WebSocket sans-I/O 帧解析；重连重读端点以跟随客户端重启。传输选型及鉴权见 [ARCHITECTURE §4.1](../docs/ARCHITECTURE.md) 与 [PROTOCOL §2.1](../docs/PROTOCOL.md)。
- HTTP 统一使用 `httpx[socks]`，减少同步 / 异步重复依赖与 wheel 审计面。
- SSRF 双校验：请求前检查 URL，建连时重新解析并检查全部地址，直接连已校验 IP，同时保留原 Host、TLS SNI 与证书校验；每跳重定向重新检查，DNS 放工作线程，防止预检到建连的地址竞态。
- 反向 RPC：由客户端持凭据与限流，工作线程等待主循环结果，超时取消；接受 Responses 输入或旧消息数组，由客户端统一契约形状，供应商兼容过滤归后端，见 [PROTOCOL §3](../docs/PROTOCOL.md)。
- 视觉图片直接注入主对话多模态结果，超尺寸缩图；不先借另一模型转文字，避免多一跳且损失原图。反向模型调用保留给纯文本处理，如浏览器快照压缩。
- Windows 进程树：启动时显式加入关闭即杀全树的 Job Object，子孙进程和 PTY 继承，崩溃后内核清理；模块导入不产生此副作用。
- Windows 路径：经句柄解析真实路径，覆盖短名、符号链接、联接点与未创建的深层子路径；比较忽略大小写、剥离设备前缀并阻断备用数据流。
- 本地终端提示按 Git Bash / Darwin-BSD 区分，执行前阻断 Linux 包管理及服务命令，避免误判宿主；SSH 不套用拦截，远端可能是真实 Linux。
- 依赖必须显式声明含平台 marker，运行中不增补；捕获导入失败仅用于真实能力探测或 OS 框架 / 平台加载，不以可选导入掩盖漏依赖。
- 代码子进程 RPC 每次生成一次性能力 token，首帧鉴权，避免 Windows loopback 端点被无关进程使用。
- SSH 密码用临时 askpass：OpenSSH 不从 stdin 读密码，强制 askpass 要求 OpenSSH ≥ 8.4（Windows 8.9+）；密钥优先并快速失败，密码脚本随环境清理。密码明文只存本机终端配置，与私钥路径同级保护。

### 按预设学习技能

调用作用域通过 `ContextVar` 固定并在结束时恢复，协议见 [PROTOCOL](../docs/PROTOCOL.md#预设记忆与学习作用域)。学习技能位于 `$SPIRITAGENT_HOME/learned-skills/<user_id>/<system_preset_id>`；读取合并当前目录与人工安装的静态技能，当前域优先。修改静态技能时复制到当前域，删除只能删除当前域副本；路径和符号链接不能跨域。

## 5. 与外部的契约

- 对客户端：握手、能力与 RPC 见 [PROTOCOL §2](../docs/PROTOCOL.md)；反向代理见 [§3](../docs/PROTOCOL.md)。
- 执行安全：全局防线见 [ARCHITECTURE §7](../docs/ARCHITECTURE.md)，模块内建连与路径约束见 §4。
- Skills 平台过滤：工具侧实施过滤，双端翻译表见 [Installer §2](../installer/README.md)。

## 6. 已知限制

- Runner 服务进程不提供交互式 TTY / stdin；工具需要终端交互时使用受管 PTY，RPC 传输使用独立本地 IPC。
- 学习隔离约束作用于技能接口；用户授权的通用文件与终端工具仍拥有原有本机访问能力。
