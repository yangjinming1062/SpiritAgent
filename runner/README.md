# Runner

Runner 在用户设备执行本机工具，不持有后端账户凭据。跨端准入、能力同步和未知结果定义归 [本机工具契约](../docs/PROTOCOL.md#本机工具)，本文维护工具装配、执行环境及资源所有权。

## 任务入口

| 改动 | 入口 |
|---|---|
| 新工具、禁用或可用性 | 各 `features/` 的工具定义、[目录与执行器](spirit_agent_runner/runtime/tools.py)、[ToolDefinition](spirit_agent_runner/contracts/tooling.py) |
| RPC、配置和调用恢复 | [contracts/rpc.py](spirit_agent_runner/contracts/rpc.py)、[transport/rpc.py](spirit_agent_runner/transport/rpc.py)、[runtime/application.py](spirit_agent_runner/runtime/application.py)、[calls.py](spirit_agent_runner/runtime/calls.py)、[journal.py](spirit_agent_runner/runtime/journal.py) |
| 本地／SSH 与代码执行 | [execution/manager.py](spirit_agent_runner/features/execution/manager.py)、[environments](spirit_agent_runner/features/execution/environments/)、[code](spirit_agent_runner/features/execution/code/)、[process](spirit_agent_runner/features/execution/process/) |
| 文件与补丁 | [files](spirit_agent_runner/features/files/)、[file_safety.py](spirit_agent_runner/infrastructure/file_safety.py) |
| 浏览器 | [能力装配](spirit_agent_runner/features/browser/__init__.py)、[session.py](spirit_agent_runner/features/browser/session.py)、[supervisor.py](spirit_agent_runner/features/browser/supervisor.py)、[camofox.py](spirit_agent_runner/features/browser/camofox.py) |
| 桌面、系统与图像 | [desktop](spirit_agent_runner/features/desktop/)、[vision.py](spirit_agent_runner/features/media/vision.py)、[images.py](spirit_agent_runner/infrastructure/images.py) |
| 技能 | [skills](spirit_agent_runner/features/skills/)、[scope.py](spirit_agent_runner/contracts/scope.py) |
| 启停与交付 | [__main__.py](spirit_agent_runner/__main__.py)、[bootstrap.py](spirit_agent_runner/bootstrap.py)、[pyproject.toml](pyproject.toml) |

## 装配、调用与资源

`bootstrap` 是唯一组合根。transport 只调用 runtime 公共接口；runtime 持配置、工具目录与在途调用，经 `ToolDefinition` 执行，不导入具体能力。features 拥有工具规则和资源，contracts 提供上下文与数据契约，infrastructure 提供跨能力原语。依赖方向由 [架构门禁](../scripts/check_runner_architecture.py) 检查。

工具说明、schema、探测及处理函数由能力就近定义；依赖配置的说明在列举时基于同一快照生成。`ConfigStore` 更新替换配置并推进代次，调用取得独立快照，`ToolContext` 固定 task、call、学习域及取消信号；`ContextVar` 传递作用域，工作线程保留该上下文。普通 RPC 和代码子调用共用执行器，后者另受白名单、参数与预算约束，不能绕过禁用工具。

| 资源 | 所有者与释放 |
|---|---|
| 执行环境、后台进程 | ExecutionManager，终端和文件经公开租约 API 访问；同步线程取消后等实际执行结束才释放租约 |
| 浏览器 | BrowserManager 持会话、启动句柄和 CDP 主管，调用持有期间不空闲回收 |
| 桌面 | DesktopManager 懒启动后端，退出等当前动作收尾，关闭后不重建 |
| 文件／技能 | FileManager 持操作缓存和读写版本，SkillManager 串行写入 |

资源显式启动，退出先关闭传输与在途调用、收尾日志，再依次关闭能力；`atexit` 仅在组合根兜底。配置更新和工具清单串行装配，阻塞探测在线程运行，按探测函数与配置代次缓存；旧结果不得回填新代次。JSON 编解码只发生在 RPC 边界，字符串结果保持字符串。

`system_awareness` 独立控制活动和系统状态工具；设备可探测不等于系统已授权。图像理解／浏览器截图超限可缩图，computer-use 超限省略图片并提示缩小范围，不能把被省略内容当作模型已看到。

## 开发与交付

源码与安装版共用 `spirit_agent_runner` 模块和单 wheel。仓库根执行 `uv sync --project runner --frozen` 安装 editable 包，再运行 Client；显式开发 Python 与仓库配置优先于 Home 安装环境。在 `runner/` 检查：

```bash
uv run --frozen python -I -m spirit_agent_runner --version
uv run --frozen python -I -m spirit_agent_runner --check
```

启动使用 `python -I -X utf8 -m spirit_agent_runner --desktop-endpoint <path>`，token 仅从 `SPIRITAGENT_DESKTOP_TOKEN` 读取。isolated mode 防止 cwd／PYTHONPATH 同名包遮蔽安装包，editable 安装负责源码定位。`--check` 校验依赖及静态工具定义，不启动运行时资源；正常运行限 Windows／macOS，Windows 用 Job Object 管进程树。wheel 隔离安装、版本和更新验证归 [Scripts](../scripts/README.md#构建安装器)。

## 执行环境

### 网络与路径

HTTP 共用 `httpx[socks]`。[url_safety.py](spirit_agent_runner/infrastructure/url_safety.py) 的图像下载在预检、建连和每跳重定向校验地址，以已核 IP 建连并保留 Host、TLS SNI 与证书校验，DNS 移出事件循环。

浏览器只预检传入 URL，CDP 导航另查落点；新标签、下载、Camofox、页内脚本和原始 CDP 没有相同复核。`browser.allow_private_urls` 只管浏览器，`security.allow_private_urls` 还影响图像下载，云元数据始终阻断。网站黑名单加载失败会诊断并放行，不能当作强隔离。文件禁区由 `file_safety.py` 与文件工具分别核对；Windows 句柄规范化覆盖短名、链接、联接点及未建后缀，大小写不敏感，备用数据流按基础路径校验。

### 终端与子进程

说明按 `terminal.env_type` 生成：本机分别适配 Windows Git Bash／macOS BSD 并阻挡不适用的 Linux 管理命令，SSH 不套本机规则。ExecutionManager 按 task 复用环境，参数和配置变化决定租约处理：

| 变化 | 处理 |
|---|---|
| 参数不变 | 复用 |
| 无租约且无前台命令、活跃后台进程 | 按新配置重建 |
| 使用中仅 cwd／超时变化 | 沿用至空闲 |
| 使用中执行类型或 SSH 目标／凭据变化 | 拒绝新调用，等待结束；不自行终止任务 |

后台进程保留原环境归属，列出、轮询和终止不要求创建新环境。stdin 按 session 串行，非阻塞 I/O 与等锁共享预算并检查取消，写入量用实际 UTF-8 字节；SSH 后台不支持 stdin，终止未确认保留 session。

SSH 密钥优先，密码通过临时 askpass 和 `SSH_ASKPASS_REQUIRE=force` 传递，随环境清理。需 OpenSSH ≥ 8.4（Windows ≥ 8.9），代码不探测版本。文件上传以暂存 hash 和源版本作基线，本机晚改下轮补传；远端未变不覆盖本机晚改，双方均变仍按远端覆盖处理。

### 代码执行

[execute_code](spirit_agent_runner/features/execution/code/code_execution_tool.py) 每次启动独立 Python 进程，经一次性 token 调用父进程工具；本机用 UDS（Windows 回环 TCP）首帧鉴权，SSH 用逐请求鉴权的文件 RPC。导入帮助函数不解锁工具，`retry` 只判异常，不判断副作用能否安全重试。

本机 `project` 模式采用终端 cwd，解释器依次选可用的指定／托管 Python、激活 venv、Runner Python；`strict` 用暂存目录和 Runner Python。两种模式控制解释器、目录和环境传递，均非强沙箱；SSH 在远端执行。输出裁切和工具预算以代码定义为准。

### 浏览器与桌面生命周期

后端依次选择 CDP URL、Camofox URL、本机 Chromium。CDP 会话按 task 串行启停，持有期间不回收；失联主管仍有调用持有时拒绝重建，清理复核实例身份。Windows 启动恢复只接管本次创建、exe 和独立 profile 均匹配的主进程，profile 占用以独占打开判断，不能凭残留 lockfile 判定。

Camofox 只暴露其支持的导航、快照、输入与视觉操作；标签、下载、Cookie、存储、仿真、原生弹窗、CDP 及批处理不提供。后端切换须重新核对实际清单，不能只改说明。

- 切页或当前页导航使元素 ref 失效，后台页导航不改变当前页；最后一页关闭后导航创建新页。
- 输入先核可编辑目标，空字符串清空，失败不向其他焦点补发；JS 弹窗由专门工具应答，不重做已打开弹窗的动作。
- 下载按触发时间关联，开始与完成共用预算；取消／超时后可能继续完成，后续调用不领取旧下载。
- Windows 键盘要求所选窗口真实前台，同进程其他窗口不算；非 ASCII 粘贴尽力恢复剪贴板，失败也清理。全局输入可能影响前台，点击失败后指针可能已移动，重试前重读状态。
- 系统快照使用 DWM 边界，排除隐藏、最小化和 cloaked 窗口，按前到后排列。探测是单次读取，未知值按工具契约返回；启动成功只表示请求已接受，不证明应用就绪或聚焦。

### 文件结果与补丁

SSH 搜索按退出状态区分无匹配、分页截断与失败，部分匹配不能掩盖命令失败，诊断和结果共用脱敏。`security.redact_secrets` 默认开启但仅模式识别，可关闭且不保证穷尽；磁盘原文不变。整文件覆盖含被掩码改写的原行时 `write_file` 拒绝，局部修改用 patch。

非 exact 补丁按实际 old_string 重锚 new_string 缩进并保留相对嵌套；写前拒绝序列化转义漂移，模糊匹配不绕过路径准入。

## 调用日志与取消

[journal.py](spirit_agent_runner/runtime/journal.py) 保存原子认领、参数指纹、持有者与终态。认领线程未结束时取消，须先等认领再记未知；完成和取消串行写，无认领凭据不能补写终态。重放已存结果不执行也不再裁切。

`calls.cancel` 按发起连接的 `req_id` 或跨连接 `call_id` 定位，两者互斥；旧结果用 `calls.get` 查询。保留期、日志不可写及未知恢复归 [调用日志契约](../docs/PROTOCOL.md#调用日志与未知结果)。

## 按预设学习技能

调用固定学习域，结束恢复上下文；[scope.py](spirit_agent_runner/contracts/scope.py) 的允许预设须与后端目录一致，由 [generate_protocol.py](../scripts/generate_protocol.py) 核对。目录与隔离归 [学习作用域](../docs/PROTOCOL.md#预设记忆与学习作用域)。

读取合并本域、静态技能和外部根。读取与管理共用候选定位：首个命中根优先，同根裸名歧义须指定 `category/skill-name`；创建只用单段名称，description 必须字符串。修改共享技能先复制到本域，失败撤回本次副本，删除只影响本域；写入串行且防符号链接跨域。

校验仅覆盖名称、frontmatter、路径和大小，写入与 `skill_view` 不做内容威胁扫描。平台过滤和学习域隔离各自生效，技能内容不能扩大工具授权。

## 契约与验证

静态门禁检查导入方向、循环和资源归属；包内健康检查只证明可加载和定义有效。命令见 [Scripts](../scripts/README.md#按改动选择验证)，修改调用须覆盖取消、日志重放、资源退出和作用域隔离。OS API、路径、进程树和桌面效果按宿主实测，SSH 单独验证。
