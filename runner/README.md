# Runner

执行本机终端、文件、浏览器、代码、进程、多模态、系统感知与 Skills 工具，并探测实际能力。不装配人格或管理对话，不持 Backend 登录凭据。

## 任务入口

| 改动 | 起点与必须联动 |
|---|---|
| 新增或禁用工具 | [registry.py](tools/registry.py)、[工具集 catalog](tools/toolsets/catalog.py) → [跨端同步契约](../docs/PROTOCOL.md#握手与工具同步) |
| RPC、能力与取消 | [server.py](server.py)、[call_journal.py](utils/call_journal.py)、[capabilities.py](utils/capabilities.py)；Client 侧握手与能力见 [bridge.ts](../client/main/runner/bridge.ts)，传输见 [rpc-ws.ts](../client/main/runner/rpc-ws.ts)，配置快照推送函数（握手时由 bridge 调用）、`execute_tool` / `execute_scoped_tool` 派发（透传 `call_id`）与取消见 [ipc/runner.ts](../client/main/ipc/runner.ts)，`call_id` 去重在渲染层 [tool-dispatch.ts](../client/renderer/app/runtime/handlers/tool-dispatch.ts) |
| 本地与 SSH 执行 | [环境工厂](envs/factory.py)、[终端工具](tools/terminal/terminal_tool.py)、[清理](envs/cleanup.py) |
| 文件读写与补丁 | [file_tools.py](tools/files/file_tools.py)（本机操作见 [native_ops.py](tools/files/native_ops.py)，SSH 操作与补丁解析、应用见 [helpers.py](tools/files/helpers.py)，模糊匹配见 [fuzzy_match.py](tools/files/fuzzy_match.py)）；读写禁区分在 [file_safety.py](utils/file_safety.py)（凭据文件、写禁区与安全写根）与 file_tools.py（设备路径、系统与用户凭据目录、设置文件） |
| 后台进程与代码执行 | [process_tool.py](tools/process/process_tool.py)、[code_execution_tool.py](tools/execute_code/code_execution_tool.py) |
| 浏览器或桌面操作 | 浏览器工具在 [tools/browser/tools](tools/browser/tools/)，CDP 会话由 [supervisor](tools/browser/supervisor.py) 管理，后端优先级为 `browser.cdp_url` → `browser.camofox.url` → 本机 Chromium：Camofox 由 [camofox.py](tools/browser/camofox.py) 的 `is_camofox_mode` 判定并在各工具内分支，CDP 连接或本机启动见 [_common.py](tools/browser/tools/_common.py) 的 `ensure_supervisor`；[桌面工具](tools/multimodal/cu_tool.py)（`key` 拦截的组合键与 `type` 拦截的命令正则在文件内）、[图像理解](tools/multimodal/vision_tool.py)；真实应用状态验证 |
| 桌面情境与窗口快照 | [activity.py](tools/system/activity.py)、`system.*` 工具注册见 [activity_tools.py](tools/system/activity_tools.py) → [Client 窗口桥](../client/main/ipc/sprite.ts)；坐标与绑定见 [动作契约](../docs/PROTOCOL.md#动作目录与播放) |
| 学习技能 | [skills_tool.py](tools/skills/skills_tool.py)、[skill_manager_tool.py](tools/skills/skill_manager_tool.py)、作用域 [memory_scope.py](utils/memory_scope.py)；检查平台与作用域 |

## 设计意图

- 工具共享环境生命周期，各能力通过公共基础设施协作。
- macOS 情境探测使用 PyObjC；平台依赖见 [pyproject.toml](pyproject.toml)。
- Windows 窗口快照使用 [DWM 可见边界](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowrect)，避免 DPI 虚拟化和透明边框干扰；排除最小化和 cloaked 窗口。
- 当前是高信任执行模式：对端准入和工具集开关构成执行授权，通用终端与文件工具保留本机访问能力。
- 网络、路径检查降低风险；不提供逐次裁决或通用强隔离沙箱。

## 代码边界

`server.py → tools → envs → utils`：连接派发、工具能力、环境与基础设施逐层依赖，底层不反向导入。环境清理与活跃进程检查经回调衔接。

## 连接、配置与能力

- Runner 主动连接 Client 的 OS IPC，重连前重读端点和 token。
- 配置仅保存在内存，由 Client 在执行前推送完整快照；传输和调用语义见 [PROTOCOL](../docs/PROTOCOL.md#本机工具)。
- 能力须实际探测系统 API，不以依赖可导入代替可用性。当前屏幕捕获在 macOS 只检查 `screencapture` 存在，麦克风在各平台只枚举输入设备，均不代表已获系统授权。
- 进程启动生成唯一 `run_generation`，重连不轮换；周期探测变化才通知，异常撤销相应能力。
- Client 的消费限制见 [能力与进程代次](../docs/PROTOCOL.md#能力与进程代次)。
- 保留经 Client 代理的 `request_llm` 反向模型通道，当前内置工具不使用；启用时同时受 Client 累计预算与 Runner 单连接预算（重连只重置后者）限制，见[反向模型请求](../docs/PROTOCOL.md#反向模型请求)。
- 工作线程等待主循环，超时取消等待。
- 图片工具结果直接交付主回合，不先调用另一模型转写；图像理解与浏览器截图超限时缩图，`computer_use` 超限则省略图片并提示缩小捕获范围。

## 执行环境

### 网络与路径

HTTP 统一使用 `httpx[socks]`。图像理解的远程下载经 `create_safe_async_client` 在 URL 预检和实际建连时均校验：连接已检查 IP，保留原 Host、TLS SNI 和证书校验，每跳重定向重新检查，DNS 解析移出事件循环。浏览器预检工具给出的 URL，仅 CDP 后端的 `browser_navigate` 复核跳转落点（新标签、下载与 Camofox 不复核），页内跳转、脚本与原始 CDP 命令不经校验。浏览器另读 `browser.allow_private_urls`（不影响图像下载）；隐藏的全局 `security.allow_private_urls` 同时放开图像下载与浏览器，云元数据地址始终拦截。实现见 [url_safety.py](utils/url_safety.py)。

Windows 在启动阶段加入 Job Object 管理进程树，不在模块导入时产生此副作用。真实路径经句柄规范化，处理短名、链接、联接点和未创建后缀，比较忽略大小写；备用数据流按基础路径判定是否受保护，受保护目录自身的数据流写入同样拒绝。

### 终端与子进程

- 本地终端按 Git Bash / Darwin-BSD 提供环境约束并拦截不适用的 Linux 管理命令；SSH 不套本机命令拦截。
- SSH 密码经临时 askpass 与 `SSH_ASKPASS_REQUIRE=force` 传递（需 OpenSSH ≥ 8.4，Windows 版 ≥ 8.9；代码不检测版本，也不处理版本不足，askpass 可能不被调用，表现为认证失败或连接超时），密钥优先，脚本随环境清理；密码和私钥路径作为本机机密处理。

代码执行每次生成一次性能力 token：本机经 UDS（Windows 为回环 TCP）首帧鉴权，SSH 远端文件 RPC 在每个请求中携带并校验。依赖与平台 marker 显式声明，不在运行期补装，也不以可选导入掩盖漏依赖。

### 工具结果与实际效果

- 工具说明随实际能力使用：文件工具不可用或无法访问目标环境时可用终端；代码工具的导入帮助函数不解锁被禁用的能力，`retry` 只重试异常、不判断副作用是否安全。
- 桌面操作的后台支持取决于平台与动作，全局键盘和坐标操作可能影响前台；工具返回调用结果，实际应用效果须由后续状态核实。

## 调用日志与取消

- `utils.call_journal` 管原子认领、参数指纹、持有者和终态。
- 取消发生在认领线程未结束时，先等待认领收尾再记录未知结果；完成与取消写入串行，未持有认领凭据不得补写终态。
- 已保存结果重放不再次裁切，也不执行工具。
- 日志不可写、无调用标识、保留期及 unknown 的处理见 [PROTOCOL](../docs/PROTOCOL.md#调用日志与未知结果)；任务取消不证明线程或外部副作用已停止。

## 按预设学习技能

`ContextVar` 固定调用作用域并在结束时恢复；作用域只接受 [memory_scope.py](utils/memory_scope.py) 中登记的预设 id，Backend 新增或改名预设须同步。学习技能保存在 `$SPIRITAGENT_HOME/learned-skills/<user_id>/<system_preset_id>`，读取按本域、静态技能、`skills.external_dirs` 的顺序合并，先匹配者优先。

修改静态技能在写入前一刻复制到当前域，写入失败时撤回副本，删除只影响本域副本；路径和符号链接不得跨域。学习技能写入只做名称、frontmatter、路径与大小校验，不做内容威胁扫描：高信任模式下终端与文件工具同样能写入技能目录，单一入口的扫描不构成边界。`skill_view` 读取时拦截命中注入特征的内容，但文件与终端工具读取不经此闸，同样不构成边界。平台过滤与学习作用域分别校验，见 [PROTOCOL](../docs/PROTOCOL.md#skills-平台过滤)。

## 已知限制

通用终端保留本机权限，网络与路径校验不构成强沙箱；副作用恢复限制集中在 [调用日志契约](../docs/PROTOCOL.md#调用日志与未知结果)，不能以取消或查无记录证明操作未发生。

## 契约与验证

静态与 wheel 检查见 [Scripts](../scripts/README.md#按改动选择验证)。OS API、路径、终端和进程树在对应宿主验证，SSH 单独验证；调用修改覆盖取消、日志重放与作用域隔离。
