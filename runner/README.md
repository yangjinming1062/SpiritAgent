# Runner

本机工具执行的工程入口。模块职责与信任边界归 [ARCHITECTURE](../docs/ARCHITECTURE.md)，传输、工具同步与调用恢复归 [本机工具契约](../docs/PROTOCOL.md#本机工具)；本文维护工具实现、执行环境与资源生命周期。

## 任务入口

| 改动 | 起点与联动 |
|---|---|
| 新增、禁用或探测工具 | 所属 `features/` 能力的 `get_tool_definitions`、[工具目录与执行器](spirit_agent_runner/runtime/tools.py)、[工具定义契约](spirit_agent_runner/contracts/tooling.py)；工具集 id 跨端同步 |
| RPC、配置、调用恢复与取消 | [纯契约](spirit_agent_runner/contracts/rpc.py)、[传输边界](spirit_agent_runner/transport/rpc.py)、[运行时](spirit_agent_runner/runtime/application.py)、[调用管理](spirit_agent_runner/runtime/calls.py)、[调用日志](spirit_agent_runner/runtime/journal.py) → [Client bridge](../client/main/runner/bridge.ts) |
| 本地／SSH、后台进程与代码 | [执行管理器](spirit_agent_runner/features/execution/manager.py)、[环境适配器](spirit_agent_runner/features/execution/environments/)、[代码执行](spirit_agent_runner/features/execution/code/)、[进程工具](spirit_agent_runner/features/execution/process/) |
| 文件与补丁 | [文件工具](spirit_agent_runner/features/files/file_tools.py)、[文件管理器](spirit_agent_runner/features/files/manager.py)、[访问与补丁组件](spirit_agent_runner/features/files/)、[路径准入](spirit_agent_runner/infrastructure/file_safety.py) |
| 浏览器 | [能力装配](spirit_agent_runner/features/browser/__init__.py)、[会话管理器](spirit_agent_runner/features/browser/session.py)、[CDP 主管](spirit_agent_runner/features/browser/supervisor.py)、[Camofox](spirit_agent_runner/features/browser/camofox.py) |
| 桌面操作、系统活动与图像 | [桌面管理器](spirit_agent_runner/features/desktop/manager.py)、[桌面工具](spirit_agent_runner/features/desktop/)、[图像工具](spirit_agent_runner/features/media/vision.py)、[共享图像处理](spirit_agent_runner/infrastructure/images.py) → [Client 坐标桥](../client/main/ipc/sprite.ts) |
| 学习技能 | [技能能力](spirit_agent_runner/features/skills/)、[学习域契约](spirit_agent_runner/contracts/scope.py) |
| 入口、资源装配与打包 | [模块入口](spirit_agent_runner/__main__.py)、[bootstrap](spirit_agent_runner/bootstrap.py)、[pyproject.toml](pyproject.toml) → [构建链](../scripts/README.md#构建安装器) |

## 装配、调用与资源

`bootstrap` 是唯一组合根。`transport` 只对接运行时公开接口；`runtime` 持有配置、工具目录、可用性缓存和在途调用，通过 `ToolDefinition` 调用能力，不导入具体工具。`features` 负责各能力规则与资源，`contracts` 提供调用上下文、工具定义和共享结果，`infrastructure` 负责跨能力的网络、路径、图像与文本处理。层间方向由 [架构门禁](../scripts/check_runner_architecture.py) 检查。

各能力显式提供 `ToolDefinition`，就近维护说明、schema、可用性探测和处理函数；依赖配置的说明在 `tools.list` 时按该次快照生成。执行器按定义中的 `is_async` 选择异步调用或工作线程。

- `ConfigStore` 每次更新替换配置并推进代次；调用取得独立快照，`ToolContext` 固定 task、call、学习域与取消信号。`ContextVar` 只传递当前作用域，工作线程保留对应上下文，不持有资源注册表。
- 普通 RPC 与代码子调用共用 `ToolExecutor`，统一工具禁用和配置作用域；代码子调用另有白名单、参数限制和预算。结果只在 RPC 边界进行 JSON 编解码，字符串结果保持字符串。
- 工具清单与配置更新串行装配，阻塞探测在线程执行。可用性按探测函数与配置代次缓存，失败 30 秒、成功 60 秒；更新使缓存失效，旧探测不得回填新代次。
- 执行管理器拥有环境租约、后台进程和空闲回收；浏览器管理器拥有会话、启动句柄和 CDP 主管；桌面管理器拥有懒启动后端；文件管理器拥有操作缓存与读写版本，技能管理器串行写入。文件通过执行能力的公开租约 API 取得环境，代码执行通过注入接口调用工具。
- 服务显式启动线程与任务。退出时先停止传输和在途调用、收尾日志，再关闭浏览器、桌面、执行环境和文件状态；同步工作线程取消后仍等待实际执行结束才释放租约。`atexit` 只在组合根登记退出兜底。

`system_awareness` 单独控制活动、焦点、锁屏、空闲、窗口和屏幕坐标等工具。麦克风能力探测只枚举设备，macOS 截图只检查 `screencapture`，不证明系统授权；周期通知与 Client 当前消费限制归 [能力与进程代次](../docs/PROTOCOL.md#能力与进程代次)。图片结果直接交付主回合；图像理解和浏览器截图超限缩图，`computer_use` 超限省略图片并提示缩小范围。

## 开发与交付

发行包 `spirit-agent` 从 `runner/spirit_agent_runner/` 构建为单个 wheel。在仓库根运行 `uv sync --project runner --frozen` 安装源码 editable 包，再启动 Client 开发入口；显式开发 Python 与仓库配置优先于 Home 下的安装环境。在 `runner/` 中检查：

```bash
uv run --frozen python -I -m spirit_agent_runner --version
uv run --frozen python -I -m spirit_agent_runner --check
```

统一启动入口为 `python -I -X utf8 -m spirit_agent_runner --desktop-endpoint <path>`，token 仅经 `SPIRITAGENT_DESKTOP_TOKEN` 传递。启动与健康检查使用 Python isolated mode，防止当前目录或 `PYTHONPATH` 中的同名包遮蔽安装包；editable 安装直接定位源码。`--version` 输出发行版本；`--check` 校验依赖与静态工具定义，返回版本和工具数量，不启动运行时资源。正常启动仅支持 Windows、macOS；Windows 使用 Job Object 管理进程树。依赖和平台 marker 见 [pyproject.toml](pyproject.toml)。

Installer 和 Client 使用包内健康入口；隔离安装门禁、签名及更新步骤归 [Scripts](../scripts/README.md#构建安装器) 与 [更新契约](../docs/PROTOCOL.md#自更新签名)。

## 执行环境

### 网络与路径

HTTP 统一使用 `httpx[socks]`。[url_safety.py](spirit_agent_runner/infrastructure/url_safety.py) 的图像下载在预检、实际建连及每跳重定向检查地址，连接已核验 IP 并保留 Host、TLS SNI 与证书校验，DNS 解析移出事件循环。

浏览器只预检工具传入的 URL，CDP `browser_navigate` 另复核落点；新标签、下载、Camofox、页内跳转、脚本和原始 CDP 命令不提供相同复核。`browser.allow_private_urls` 只影响浏览器，隐藏的 `security.allow_private_urls` 也影响图像下载，云元数据地址始终阻断。`security.website_blocklist` 支持域名和共享规则文件，但规则加载失败会记录诊断并放行，不能作为强隔离边界。

文件读写禁区分别由 `file_safety.py` 和 `file_tools.py` 校验。Windows 真实路径经句柄规范化，处理短名、链接、联接点和未创建后缀，比较忽略大小写；备用数据流按基础路径判定，保护目录自身的数据流写入同样拒绝。

### 终端与子进程

终端说明随 `terminal.env_type` 生成：本地按 Windows Git Bash 或 macOS BSD 环境约束并拦截不适用的 Linux 管理命令；SSH 描述远端，不套本机拦截。

[执行管理器](spirit_agent_runner/features/execution/manager.py) 按 task 持有执行环境，终端和文件工具通过 `use_environment` 取得租约；创建参数包含类型、cwd、超时与 SSH 目标。配置变化处理如下：

| 当前环境状态 | 调用处理 |
|---|---|
| 参数一致 | 复用 |
| 无租约、前台命令和活跃后台进程 | 停止环境，按新配置重建 |
| 仍在使用，仅 cwd／超时变化 | 沿用到空闲 |
| 仍在使用，执行类型或 SSH 目标／凭据变化 | 拒绝调用，等待结束；停止后台进程须由实际任务授权 |

后台进程记录所属环境，列出、轮询和终止不依赖取得新环境。stdin 写入和关闭按 session 串行，非阻塞 I/O 与等锁共用 5 秒预算并检查取消；`bytes_written` 为实际 UTF-8 字节数。SSH 后台进程不支持 stdin，终止无法确认时保留 session 并报告错误。

SSH 密码通过临时 askpass 和 `SSH_ASKPASS_REQUIRE=force` 传递，密钥优先；需要 OpenSSH ≥ 8.4（Windows ≥ 8.9），代码不检测版本，版本不足可能认证失败或超时。askpass 随环境清理。文件上传以暂存副本 hash 和上传前源版本建基线，本机并发修改下轮补传；远端未变时不覆盖本机晚改，双方均变时仍按远端覆盖处理。

### 代码执行

[execute_code](spirit_agent_runner/features/execution/code/code_execution_tool.py) 每次启动独立 Python 子进程，经一次性 token 调用父进程工具；本机使用 UDS（Windows 回环 TCP）首帧鉴权，SSH 使用逐请求鉴权的文件 RPC。导入帮助函数不解锁禁用工具，`retry` 只重试异常，不判断副作用是否安全。

本机默认 `code_execution.mode=project`，采用终端当前 cwd；解释器依次选择可用的用户指定或托管 Python、激活的虚拟环境、Runner Python。`strict` 使用暂存目录和 Runner Python。模式控制解释器、目录与环境传递，不构成强沙箱；SSH 在远端执行。脚本输出有头尾裁切和工具调用预算，参数定义以源码为准。

### 浏览器与桌面生命周期

浏览器后端优先级为 `browser.cdp_url` → `browser.camofox.url` → 本机 Chromium。CDP 会话按 task 串行启停，工具持有期间不作空闲回收，结束重新计时；失联主管仍有其他调用持有时拒绝重建，清理核对实例身份，启动失败释放本次资源。

Camofox 仅同步其实际支持的导航、快照、点击、输入、滚动、按键、图片和视觉工具；标签页、下载、Cookie、存储、仿真、原生弹窗、CDP 与批处理工具在该后端不会提供给模型。

Windows Chromium 启动器重启时只接管本次创建且 exe、独立 profile 匹配的实际主进程；profile 锁以独占打开判断，不以残留 `lockfile` 判断占用。

- 切换标签或当前页导航后重取元素 ref，后台标签导航不改变当前页；关闭最后一页后导航创建新页。
- 输入先核对可编辑目标，空字符串清空内容，失败不向其他焦点补发。JS 弹窗由 `browser_dialog` 应答；打开弹窗的动作已经执行，不应重复该动作。
- 下载按触发时间匹配，等待开始与完成共用预算；取消或超时后仍可能完成，后续调用不领取旧下载。
- Windows 键盘输入要求所选窗口在前台，同进程其他窗口不满足；非 ASCII 粘贴后尽力恢复剪贴板，失败也走恢复路径。桌面后端退出前等待当前动作收尾，关闭后不再创建。
- 系统窗口快照使用 DWM 可见边界并排除最小化、隐藏和 cloaked 窗口，结果按前到后排列并可能带有当前 Runner 实例标识。`system.get_idle_seconds` 与 `system.snapshot` 是单次即时探测，未知值沿各自工具契约返回（空闲为 `-1`、焦点为空对象、锁屏和全屏为 `false`），调用方不得无界轮询。`system.open_application` 返回 `opened: true` 只表示启动请求已接受，不保证应用已就绪或获得焦点；`system.click_at` 发送真实全局输入，失败后指针仍可能已移动，重试前需重新核对状态。桌面工具的后台支持依平台和动作，全局输入可能影响前台。

### 文件结果与补丁

SSH 内容搜索按命令退出状态区分未命中、分页截断与执行失败；失败时返回错误并保留命令输出作为诊断，不将已有的部分匹配当作成功结果。诊断与匹配内容共用脱敏配置。

`security.redact_secrets` 默认开启；文件读取、搜索及部分工具结果通过模式识别掩码疑似凭据，磁盘原文保持不变，可关闭且不保证识别所有秘密。整文件覆盖若包含原文被掩码改写的行，`write_file` 拒绝写入，避免用占位符替换真实凭据；局部修改走 `patch`。

非 exact 补丁命中按 old_string 将 new_string 缩进重锚到文件，保留相对嵌套；写入前拒绝序列化转义漂移。模糊匹配不能绕过路径准入。

## 调用日志与取消

[journal.py](spirit_agent_runner/runtime/journal.py) 持有原子认领、参数指纹、持有者与终态。取消发生在认领线程未结束时，先等待认领再记未知结果；完成与取消写入串行，无认领凭据不能补写终态。保存结果重放不再次执行或裁切。保留期、日志不可写和结果未知的恢复边界统一归 [调用日志契约](../docs/PROTOCOL.md#调用日志与未知结果)。

`calls.cancel` 以发起连接的 RPC `req_id` 或业务 `call_id` 定位在途调用，两者不能同时提供。后者用于 Client 重连后的取消，查询结果仍通过 `calls.get`，不调用执行入口取得旧结果。

## 按预设学习技能

`ToolContext` 固定学习域并通过 `ContextVar` 传递，结束后恢复；允许预设在 [学习域契约](spirit_agent_runner/contracts/scope.py) 登记，后端预设目录新增或改名须与该契约一致，由[跨端生成器](../scripts/generate_protocol.py)校验后才生成契约。目录与隔离契约归 [学习作用域](../docs/PROTOCOL.md#预设记忆与学习作用域)，读取依次合并本域、静态技能和 `skills.external_dirs`。

读取与管理共用候选定位：首个有匹配的根优先，同根裸名歧义拒绝猜选，可用 `category/skill-name` 定位；创建仍用单段名称，frontmatter description 必须是字符串。

修改共享技能前复制到当前域，复制或修改失败撤回本次新副本，删除只影响本域；管理操作串行，路径和符号链接不得跨域。写入仅检查名称、frontmatter、路径与大小，写入及 `skill_view` 不扫描内容威胁。技能平台过滤与学习域独立生效，不扩大授权。

## 契约与验证

依赖方向、导入环与模块资源归属由静态门禁检查；包内健康检查只验证可加载性与静态定义。静态与 wheel 检查归 [Scripts](../scripts/README.md#按改动选择验证)。OS API、文件路径、终端、进程树和桌面效果在对应宿主验证，SSH 单独验证；调用修改覆盖取消、日志重放、资源退出与作用域隔离。
