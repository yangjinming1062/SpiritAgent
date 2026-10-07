# Runner

本机工具执行的工程入口。模块职责与信任边界归 [ARCHITECTURE](../docs/ARCHITECTURE.md)，传输、工具同步与调用恢复归 [本机工具契约](../docs/PROTOCOL.md#本机工具)；本文维护工具实现、执行环境与资源生命周期。

## 任务入口

| 改动 | 起点与联动 |
|---|---|
| 新增、禁用或探测工具 | [registry.py](tools/registry.py)、[catalog.py](tools/toolsets/catalog.py)；跨端工具集枚举按握手契约同步 |
| RPC、配置与取消 | [server.py](server.py)、[call_journal.py](utils/call_journal.py)、[capabilities.py](utils/capabilities.py) → [Client bridge](../client/main/runner/bridge.ts)、[派发入口](../client/main/ipc/runner.ts)、[宿主派发](../client/renderer/app/runtime/handlers/tool-dispatch.ts) |
| 本地与 SSH 执行 | [factory.py](envs/factory.py)、[terminal_tool.py](tools/terminal/terminal_tool.py)、[cleanup.py](envs/cleanup.py) |
| 文件与补丁 | [file_tools.py](tools/files/file_tools.py)、本机 [native_ops.py](tools/files/native_ops.py)、SSH 与补丁 [helpers.py](tools/files/helpers.py)、[fuzzy_match.py](tools/files/fuzzy_match.py)、路径准入 [file_safety.py](utils/file_safety.py) |
| 后台进程与代码 | [process_tool.py](tools/process/process_tool.py)、[code_execution_tool.py](tools/execute_code/code_execution_tool.py) |
| 浏览器 | [tools/browser/tools](tools/browser/tools/)、[supervisor.py](tools/browser/supervisor.py)、[session.py](tools/browser/session.py)、[camofox.py](tools/browser/camofox.py) |
| 桌面操作与图像 | [cu_tool.py](tools/multimodal/cu_tool.py)、[vision_tool.py](tools/multimodal/vision_tool.py)；实际应用效果须后续核实 |
| 系统活动与窗口 | [activity.py](tools/system/activity.py)、[activity_tools.py](tools/system/activity_tools.py) → [Client 坐标桥](../client/main/ipc/sprite.ts) |
| 学习技能 | [skills_tool.py](tools/skills/skills_tool.py)、[skill_manager_tool.py](tools/skills/skill_manager_tool.py)、[memory_scope.py](utils/memory_scope.py) |

## 代码边界与注册

`server.py → tools → envs → utils` 逐层依赖；环境清理与活跃进程检查经回调衔接。`server.py` 启动时拒绝 Windows、macOS 以外的宿主，并在 Windows 显式初始化 Job Object 管理进程树；依赖和平台 marker 归 [pyproject.toml](pyproject.toml)，不在运行时补装。

- 工具导入早于首次配置推送，依赖配置的说明用无参工厂注册；每次 `get_tools` 根据当前配置生成。
- 配置推送与清单装配共用 `_CONFIG_LOCK`，阻塞探测在线程执行；资源向注册中心登记 shutdown hook，主循环退出时释放。
- 可用性按探测函数共享缓存：失败结果缓存 30 秒，成功结果保留 60 秒；full config 使缓存失效，旧探测不回填新配置。依赖可导入不等于 OS API 可用。
- `system_awareness` 单独控制活动、焦点、锁屏、空闲、窗口和屏幕坐标等工具，不连带禁用其他工具集。
- 能力快照的麦克风探测只枚举设备，macOS 截图只检查 `screencapture`，不证明系统授权。周期通知与 Client 当前消费限制归 [能力与进程代次](../docs/PROTOCOL.md#能力与进程代次)。
- `request_llm` 保留为 Client 代理通道，当前内置工具未使用；工作线程通过主循环等待，超时取消等待。图片工具结果直接交付主回合；图像理解和浏览器截图超限缩图，`computer_use` 超限省略图片并提示缩小范围。

## 执行环境

### 网络与路径

HTTP 统一使用 `httpx[socks]`。[url_safety.py](utils/url_safety.py) 的图像下载在预检、实际建连及每跳重定向检查地址，连接已核验 IP 并保留 Host、TLS SNI 与证书校验，DNS 解析移出事件循环。

浏览器只预检工具传入的 URL，CDP `browser_navigate` 另复核落点；新标签、下载、Camofox、页内跳转、脚本和原始 CDP 命令不提供相同复核。`browser.allow_private_urls` 只影响浏览器，隐藏的 `security.allow_private_urls` 也影响图像下载，云元数据地址始终阻断。`security.website_blocklist` 支持域名和共享规则文件，但规则加载失败会记录诊断并放行，不能作为强隔离边界。

文件读写禁区分别由 `file_safety.py` 和 `file_tools.py` 校验。Windows 真实路径经句柄规范化，处理短名、链接、联接点和未创建后缀，比较忽略大小写；备用数据流按基础路径判定，保护目录自身的数据流写入同样拒绝。

### 终端与子进程

终端说明随 `terminal.env_type` 生成：本地按 Windows Git Bash 或 macOS BSD 环境约束并拦截不适用的 Linux 管理命令；SSH 描述远端，不套本机拦截。

[factory.py](envs/factory.py) 按 task 持有执行环境，终端和文件工具通过 `use_environment` 取得租约；创建参数包含类型、cwd、超时与 SSH 目标。配置变化处理如下：

| 旧环境状态 | 新调用 |
|---|---|
| 参数一致 | 复用 |
| 无租约、前台命令和活跃后台进程 | 停止旧环境，按新配置重建 |
| 仍在使用，仅 cwd／超时变化 | 沿用到空闲 |
| 仍在使用，执行类型或 SSH 目标／凭据变化 | 拒绝调用，等待结束；停止后台进程须由实际任务授权 |

后台进程记录所属环境，列出、轮询和终止不依赖取得新环境。stdin 写入和关闭按 session 串行，非阻塞 I/O 与等锁共用 5 秒预算并检查取消；`bytes_written` 为实际 UTF-8 字节数。SSH 后台进程不支持 stdin，终止无法确认时保留 session 并报告错误。

SSH 密码通过临时 askpass 和 `SSH_ASKPASS_REQUIRE=force` 传递，密钥优先；需要 OpenSSH ≥ 8.4（Windows ≥ 8.9），代码不检测版本，版本不足可能认证失败或超时。askpass 随环境清理。文件上传以暂存副本 hash 和上传前源版本建基线，本机并发修改下轮补传；远端未变时不覆盖本机晚改，双方均变时仍按远端覆盖处理。

### 代码执行

[execute_code](tools/execute_code/code_execution_tool.py) 每次启动独立 Python 子进程，经一次性 token 调用父进程工具；本机使用 UDS（Windows 回环 TCP）首帧鉴权，SSH 使用逐请求鉴权的文件 RPC。导入帮助函数不解锁禁用工具，`retry` 只重试异常，不判断副作用是否安全。

本机默认 `code_execution.mode=project`，采用终端当前 cwd，并优先可用的项目 Python；`strict` 使用暂存目录和 Runner Python。模式控制解释器、目录与环境传递，不构成强沙箱；SSH 仍在远端执行。脚本输出有头尾裁切和工具调用预算，参数定义以源码为准。

### 浏览器与桌面生命周期

浏览器后端优先级为 `browser.cdp_url` → `browser.camofox.url` → 本机 Chromium。CDP 会话按 task 串行启停，工具持有期间不作空闲回收，结束重新计时；失联主管仍有其他调用持有时拒绝重建，清理核对实例身份，启动失败释放本次资源。

Windows Chromium 启动器重启时只接管本次创建且 exe、独立 profile 匹配的实际主进程；profile 锁以独占打开判断，不以残留 `lockfile` 判断占用。

- 切换标签或当前页导航后重取元素 ref，后台标签导航不改变当前页；关闭最后一页后导航创建新页。
- 输入先核对可编辑目标，空字符串清空内容，失败不向其他焦点补发。JS 弹窗由 `browser_dialog` 应答；打开弹窗的动作已经执行，不应重复该动作。
- 下载按触发时间匹配，等待开始与完成共用预算；取消或超时后仍可能完成，后续调用不领取旧下载。
- Windows 键盘输入要求所选窗口在前台，同进程其他窗口不满足；非 ASCII 粘贴后尽力恢复剪贴板，失败也走恢复路径。桌面后端退出前等待当前动作收尾，关闭后不再创建。
- 系统窗口快照使用 DWM 可见边界并排除最小化、cloaked 窗口。桌面工具的后台支持依平台和动作，全局输入可能影响前台。

### 文件结果与补丁

`security.redact_secrets` 默认开启；文件读取、搜索及部分工具结果通过模式识别掩码疑似凭据，磁盘原文保持不变，可关闭且不保证识别所有秘密。整文件覆盖若包含原文被掩码改写的行，`write_file` 拒绝写入，避免用占位符替换真实凭据；局部修改走 `patch`。

非 exact 补丁命中按 old_string 将 new_string 缩进重锚到文件，保留相对嵌套；写入前拒绝序列化转义漂移。模糊匹配不能绕过路径准入。

## 调用日志与取消

[call_journal.py](utils/call_journal.py) 持有原子认领、参数指纹、持有者与终态。取消发生在认领线程未结束时，先等待认领再记未知结果；完成与取消写入串行，无认领凭据不能补写终态。保存结果重放不再次执行或裁切。保留期、日志不可写和结果未知的恢复边界统一归 [调用日志契约](../docs/PROTOCOL.md#调用日志与未知结果)。

## 按预设学习技能

`ContextVar` 固定调用域并在结束恢复；允许预设在 [memory_scope.py](utils/memory_scope.py) 登记，Backend 新增或改名须同步。目录与隔离契约归 [学习作用域](../docs/PROTOCOL.md#预设记忆与学习作用域)，读取依次合并本域、静态技能和 `skills.external_dirs`。

读取与管理共用候选定位：首个有匹配的根优先，同根裸名歧义拒绝猜选，可用 `category/skill-name` 定位；创建仍用单段名称，frontmatter description 必须是字符串。

修改共享技能前复制到当前域，复制或修改失败撤回本次新副本，删除只影响本域；管理操作串行，路径和符号链接不得跨域。写入仅检查名称、frontmatter、路径与大小，写入及 `skill_view` 不扫描内容威胁。技能平台过滤与学习域独立生效，不扩大授权。

## 契约与验证

静态与 wheel 检查归 [Scripts](../scripts/README.md#按改动选择验证)。OS API、文件路径、终端、进程树和桌面效果在对应宿主验证，SSH 单独验证；调用修改覆盖取消、日志重放、资源退出与作用域隔离。
