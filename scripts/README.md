# 仓库构建与验证

本目录维护仓库级检查、提示词调试、安装器构建和发布。命令默认在仓库根执行；Client 自有脚本由 [package.json](../client/package.json) 调用，首次安装流程见 [Installer](../installer/README.md)。

## 按改动选择验证

先按[源码启动说明](../README.md#从源码启动)准备依赖。验证范围按改动选择：

| 改动 | 检查 |
|---|---|
| 文档 | 事实、相对路径、锚点、图表分支与渲染；`git diff --check` 只检查空白 |
| Python | 提交前 hook；Backend 依赖变化另跑分层检查 |
| Client | `pnpm --dir client lint`（含路径解析后的模块／窗口边界、平台桥访问与静态值依赖环）、`pnpm --dir client typecheck` |
| 手机远程页面 | `pnpm --dir remote lint`、`pnpm --dir remote typecheck`、`pnpm --dir remote build`；扫码、麦克风和后台恢复须在 iOS Safari、Android Chrome 实机验收 |
| 共享协议 `shared/` | 同时执行 Client 与 Remote 的 lint、typecheck 和构建 |
| Windows 桌面 helper | 在 Windows 执行 `pnpm --dir client build:native`，或在 `client/desktop-host/` 做对应目标的 `cargo check --locked`；交互与恢复另做实机验收 |
| preload、构建入口或资源 | `pnpm --dir client build` 并核对实际产物 |
| Installer 前端与编排 | `pnpm --dir installer build`，以及完整载荷下的目标平台 Tauri 构建和安装验收 |
| wheel 或安装载荷 | 隔离安装门禁、精确版本与对应平台构建 |
| 音频 | 专项静态检查，必要时合成并试听 |

提交前完整入口（以 [.pre-commit-config.yaml](../.pre-commit-config.yaml) 为准）：

```bash
uv tool run --python 3.13 pre-commit run -a
```

hook 包含自动修正，仅在修改完成、准备提交时执行，随后检查差异并复验；开发过程使用只读专项检查，不反复运行格式化。hook 不包含 Client typecheck、Backend 架构检查或原生验收。构建可能修改构建信息和版本清单，交付时区分静态、构建与真实运行结果。

## Windows 桌面验收

记录 Windows 版本、Electron 与 helper 架构、显示器布局和 DPI；按下表实测并保留预期、结果和失败后的恢复情况。`cargo check`、模拟和 macOS 构建不能代替 Windows 验收，核心输入、层级或恢复不成立时保留窗口模式。

| 范围 | 必须验证的行为 |
|---|---|
| 输入与层级 | 在透明图标层与传统 WorkerW 结构分别验证视频位于原生图标下方；中文输入法、焦点、复制粘贴、选择文件和拖放；内部开窗或重复点击取得前台，外部程序可覆盖界面，后台校正不抢前台，透明空白交给 Explorer；桌面视频无独立悬浮精灵，窗口模式精灵正常恢复 |
| 原生桌面文件 | 文件、文件夹、快捷方式、回收站及 OneDrive／公共桌面项目沿用原生图标与名称；双击、右键、框选、重命名、复制粘贴、删除撤销及文件拖放可用；覆盖大量图标、不同大小、自动与手动排列、原本隐藏图标的偏好，退出保留用户整理结果 |
| 工作区与系统操作 | 顶部无新增预留或空带，底部按共享布局预留 96 DIP 并计入已有系统占用，图标避开完整底栏与头像；已有及新启动窗口的还原、最大化、贴边、拖动、缩放使用实际工作区，内部窗口不重复扣除底栏；菜单、轻语及功能窗口不扩大 AppBar 预留；全屏收起、退出恢复页面与草稿且不抢前台；Win+D、Alt+Tab、任务视图和虚拟桌面 |
| 多屏与电源 | 混合 DPI、负坐标、竖屏及三屏；首次优先副屏、交互屏切换、拔屏退出且重接不自动接管、启动缺屏、锁屏及休眠恢复；仅交互屏显示视频背景，其余屏的任务栏、开始菜单、图标双击、右键、拖放、最大化及贴边保持可用，覆盖原本隐藏图标或任务栏的偏好 |
| 接管与恢复 | 快捷键冲突拒绝接管；紧急恢复、切回窗口、换号、鉴权失败、退出及更新注销 AppBar、恢复工作区与任务栏，桌面文件继续可操作并保留外部窗口摆放；Explorer 重启后重新识别层级与注册，失败时回退 |
| 进程故障 | 分别终止主进程、各 renderer、host 和 guardian，检查恢复或失联兜底，确认无残留底部预留；恢复失败保留 journal，下次启动先恢复且不自动重试接管 |
| 主窗口与模式交接 | 窗口模式先显示精灵，菜单、托盘、快捷键、通知及投喂复用同一主窗口；原生关闭恢复精灵，最小化不恢复；两种模式接续账户页面、会话、草稿、附件和阅读位置，目标恢复失败回到来源；录音、转写和上传期间所有切换入口禁用并说明原因 |
| 底栏与弹层 | 常规宽屏、窄屏、竖屏及 125%／150% DPI 下头像始终屏幕居中，左右图标悬殊或大量程序时不遮挡右下角控制入口，所有程序常驻；三个彩色功能图标打开、最小化、恢复、置前、关闭与未读状态清楚，最小化不使图标变淡；头像左键打开陪伴聊天并定位未读，右键与键盘菜单键打开账户菜单，聊天状态与未读声波分别表达，无未读时外围柔和呼吸，有未读时中央横向青白色发光声波可见且头像仍可辨识，装饰不裁切或遮挡相邻入口；账户菜单、目录与控制面板互斥、向上避让、菜单间隙不穿透，支持键盘、Esc 与焦点归还；明暗主题、中英文、关闭模糊及减少动态效果下的静态柔光环或静态声波 |
| 桌面内容可读性 | 明暗主题、复杂壁纸及后方高对比文字下，功能窗口、轻语、应用目录、桌景制作表单和共享弹层使用实底，不透出后方文字；圆角、边框、阴影和底栏玻璃感正常，透明空白仍可操作 Explorer |
| 桌面控制 | 收起入口异常提示；面板日期时间、伙伴及连接状态、桌景准备或错误及跳转；交互屏、打扰档位、返回窗口模式和退出 |
| 桌面业务 | 主对话与轻语互斥、手动展开可并存，同会话无重复提交或播放；后台面板保留未读；头像仅由陪伴文字未读或未听语音切换为发光声波，不随录音或实时音量变化，进入会话清除文字未读，语音完整播完后清除未听，清除后恢复呼吸光环，动态与日记独立提示；首帧切换、循环与短暂动作返回，加载失败保留旧画面，换号清除旧媒体 |
| Dock 目录 | 对照[启动器契约](../docs/PROTOCOL.md#桌面呈现与本机启动器)覆盖各来源、去重和排除项，包括无快捷方式及打包应用；所有程序入口在空固定区及 Dock 溢出时常驻，网格适配窄屏、高 DPI 和长名称；中英文、exe / 包名搜索和中文路径，全部已扫描条目可搜索；图标按可见区域分批补齐，局部失败或超时不阻塞其余条目，固定标记刷新失败仍接收同代图标，重扫拒绝旧句柄；面板打开超过缓存期限后打包应用仍可校验并启动，卸载或 AppsFolder 读取失败须拒绝启动 |
| Dock 配置 | 程序点击只启动或恢复，不写固定配置；独立固定按钮防止重复加入，已固定项可启动，连续固定不关闭面板或改变网格排序；空固定区可将运行程序拖到所有程序入口固定，原位拖回不误固定；文件浏览、拖放和带参数快捷方式；打包应用重启或更新仍可启动；目标失效修复保留 ID 与顺序，取消或写盘失败保留原配置；面板不穿透，支持方向键、Enter、Tab 和 Esc；外部点击及失焦收起，文件选择期间保留，启动后不抢回焦点，迟到结果不关闭新面板 |
| Dock 运行 | 启动前已有窗口、新启动、最小化、末窗口关闭及虚拟桌面切换均收敛，纯后台进程排除；多窗口归并、最近或指定窗口恢复、单个或全部关闭及取消保存提示；遮挡后重复点击置前，模态和跨屏行为正确，切换中视频背景与界面层级稳定；运行标记和窗口菜单只包含交互屏，跨屏拖动及时更新，关闭全部不波及其他屏幕；固定程序点击优先本屏，否则只迁移最近窗口，覆盖最大化及最小化；新启动、快捷方式、打包应用和 Jump List 新窗口定位本屏，并发或匹配歧义不误搬 Windows 独立启动的窗口；程序右键显示并执行 Windows Jump List 的 New Window、Agents Window 等任务，旧任务 ID、损坏任务和无任务程序静默回退；旧 ID 或激活失败不重复启动；固定、拖拽排序和取消固定回流 |
| 打包 | 各目标架构分别打包，resources 内 helper 架构与应用一致；桌面三个 SVG 图标在打包后的本地页面正常加载 |

所有恢复进程同时被强制终止不能保证即时恢复，还须验证下次启动处理遗留 journal。

## 导入检查

[`check_imports.py`](check_imports.py) 检查 Backend / Runner 的 future annotations、TYPE_CHECKING 名字泄漏和包级 facade，使用 Python 3.13：

```bash
uv run --no-project --python 3.13 python scripts/check_imports.py --strict-imports
uv run --no-project --python 3.13 python scripts/check_imports.py --fix
```

不带 `--strict-imports` 时违规仅输出诊断、退出码为 0；`--fix` 只自动移除 future annotations，修改后重跑严格模式。

默认跳过模块根目录的 `build/`、`dist/` 及各层 `.venv`、`__pycache__`，避免旧构建副本与当前源码导出表混用；显式传入文件仍逐个检查。实际 wheel 另由 `check_runner_package.py` 隔离安装校验。

[`check_runner_architecture.py`](check_runner_architecture.py) 检查 Runner 的层间依赖、跨能力私有访问、模块导入环、旧根包引用，以及导入时注册和资源所有权。`contracts` 保持纯契约；基础设施显式接收配置，不读取调用作用域；运行时不导入具体能力，能力间通过注入端口协作，文件能力可依赖执行能力的公开租约 API。常量、类型别名和 `ContextVar` 绑定允许保留在模块级。该门禁已纳入 pre-commit，也可独立运行：

```bash
uv run --no-project --python 3.13 python scripts/check_runner_architecture.py
```

使用 `--runner <包目录>` 可检查临时样本；它只静态解析源码，不导入 Runner 或启动资源。资源回收、线程取消与真实工具行为仍须单独验证。

## Backend 架构检查

```bash
uv run --no-project --python 3.13 python scripts/check_services_architecture.py
```

[`check_services_architecture.py`](check_services_architecture.py) 覆盖导入解析、模块与业务实现环、目录方向、跨业务 ORM/私有入口、公开返回 ORM、入口 SQL 和旧根目录。纯 DTO、类型别名与静态词表不构成业务实现依赖。默认检查 Backend；`--backend <目录>` 可对临时样例验证规则。修改边界时同步 [Backend](../backend/README.md#模块依赖边界) 的数据归属与设计理由。

## 提示词调试

```bash
uv run --project backend python scripts/debug_prompt.py
uv run --project backend python scripts/debug_prompt.py -m "你好" --persona-name "星奈" --json
uv run --project backend python scripts/debug_prompt.py --db --user-id 1 --preset companion
```

[`debug_prompt.py`](debug_prompt.py) 展示基础预设、画像、工具与单条输入，不执行模型回合，也不包含全部动态语音协议。默认使用模拟资料；`--db` 需要后端配置和数据库。真实发送请求仍通过 [Backend 调试日志](../backend/README.md#llm-调试日志) 核对。

## 引导音频

文案、tag、生成和静态校验见 [专项说明](onboarding-audio/README.md)。

## Client 开发与打包脚本

[`client/scripts/`](../client/scripts/) 的关键门禁：`assert-root-install.cjs` 检查 Client 根依赖，`write-build-stamp.cjs` 记录提交来源，`assert-dist-built.cjs` 检查所有入口页面及其本地 JS、样式和预加载资源。`launch-dev-electron.cjs` 管理开发进程，打包钩子准备 Windows helper 和 DPI manifest。

`build` 收尾与 `beforePack` 均校验渲染产物，直接调用 `builder` 仍须先完成构建；Windows helper 的架构或静态运行库门禁失败会中止打包。开发准备见 [桌面宿主构建](../client/desktop-host/README.md#构建)。

macOS `afterSign` 调用 [notarize.cjs](../client/scripts/notarize.cjs)，electron-builder 内建公证关闭。支持 keychain profile、Apple ID 及 API key，参数与优先级见脚本；未配置时跳过，部分配置报错。外部命令有超时，临时 ZIP 和内联 key 文件在结束时清理；真实签名与公证须在 macOS 验证。

## 手机远程页面构建

`pnpm --dir remote build` 编译独立浏览器入口，产物位于 `remote/dist/`。Backend 在 `/remote/` 同源提供页面；开发可运行 `pnpm --dir remote dev`，但 Secure Cookie 和手机录音仍要求 HTTPS 同源环境。

[Backend Dockerfile](../backend/Dockerfile) 先使用 Remote 独立锁定的 Node.js / pnpm 依赖构建网页，再复制到 Python 镜像的 `static/remote/`；构建上下文仍为仓库根。[.dockerignore](../.dockerignore) 只允许必要的 Remote 与共享协议构建源进入上下文，排除桌面产物、运行数据和凭据。部署时配置公网 HTTPS 地址，反向代理须保留 WS 升级及请求 Origin。

## 构建安装器

[`build.py`](build.py) 依次同步版本、构建 Runner wheel、构建 Client、暂存 payload、构建 Tauri Installer。准备 uv、Node.js / pnpm、Rust / Cargo 和目标平台工具链；Windows helper 前提见上节。Backend Docker 独立部署。

以下版本仅为示例，命令会修改 Client、Installer 和 Runner 的版本清单：

```bash
uv run --no-project --python 3.13 python scripts/build.py --version 1.2.3
```

`--target mac|win` 默认按宿主推断，必须在对应系统构建；`--skip-runner` / `--skip-desktop` 仍要求同版本既有产物，`--output` 覆盖默认 `release/`。Runner 从临时源码副本构建，避免旧 `build/lib` 混入产物；正式暂存只接受目标版本唯一 wheel，核对包内 Python 文件清单与字节均和源码一致，并在仓库外的临时 venv 安装，校验模块入口、版本、依赖和静态工具定义。单独诊断：

```bash
uv run --no-project --python 3.13 python scripts/check_runner_package.py
```

单独检查默认取修改时间最新的 wheel，可用 `--wheel` 指定输入；需要可用的依赖源，不能替代正式构建的精确版本门禁。包内 `--version` 与 `--check` 不启动运行时资源，后者也供 Installer 和 Client 安装健康检查使用。暂存与 Tauri 资源补丁由 [build_helpers.py](lib/build_helpers.py) 实现，Tauri 构建结束后恢复资源配置。

| 平台 | `release/` 产物 |
|---|---|
| macOS | 安装器 DMG；同时保留 Tauri 原始文件名和 `SpiritAgent-Setup-<version>.dmg` 别名 |
| Windows | `SpiritAgent-Setup-<version>.exe` 和 `SpiritAgent-<version>-update.zip` |

Windows 安装器内嵌 NSIS 包；update ZIP 由 [UpdateManifest.ps1](lib/UpdateManifest.ps1) 打包桌面产物及 blockmap、`runner/` 下的单 wheel、签名 `latest-runner.yml` 和 `manifest.json`。技能随桌面包交付，Backend 上传后按库存重建桌面清单。生成 ZIP 需要 PowerShell、openssl 和[更新签名私钥](release-keys/README.md)，缺失或签名失败中止构建。

`--sign-identity`（可配合 `--notary-profile`）和 `--cert-thumbprint` 作用于 `client/release/` 的桌面端产物，再复制进 payload；`build.py` 不签安装器本身。Client 的 `.app` 公证另由上一节的环境配置驱动，不能把桌面产物签名等同于整套安装器已签名。

## 发布

推送 `vX.Y.Z` tag 触发 [release.yml](../.github/workflows/release.yml)。构建只接受三段数字，其他 `v*` tag 会触发后失败。版本来自 tag；双平台产物与 notes 汇总为草稿 release，核对后发布。已有 release 仅补产物和 notes，不改变发布状态。

GitHub release 与客户端更新是两个入口：自动更新需在 Backend 管理端“版本管理”上传 update ZIP，再由当前后端分发，通信与验签见 [PROTOCOL](../docs/PROTOCOL.md#自更新签名)。当前构建入口只生成 Windows update ZIP，macOS DMG 不等于可直接上传的同类更新包。

跨端契约变更需同步发布 Backend、Client 和 Remote。先按[备份与覆盖恢复](../docs/PROTOCOL.md#备份校验与覆盖恢复)建立数据与素材恢复点，停止新任务并收敛在途工作；数据库变化通过新增 Alembic 迁移承接。核对存量副本中的记录、关联、素材和草稿后切换，验收通过再开放写入；失败恢复同一备份点的数据、素材和代码。

CI 的 `SPIRITAGENT_UPDATE_SIGNING_KEY` Secret 存 PEM 内容；工作流落地临时文件后传路径。notes 用的 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL_NAME` 均为 repo secrets，缺失会使 notes 任务失败。工作流未配置平台证书签名或 macOS 公证，不能把 CI 构建成功视为已完成二者。

## Release notes

[`gen_release_notes.py`](gen_release_notes.py) 按版本排序选前一个 tag，或由 `--from-tag` 指定区间，生成中文说明。以下 tag 须替换为仓库实际版本：

```bash
uv run --project backend python scripts/gen_release_notes.py v1.3.0
uv run --project backend python scripts/gen_release_notes.py v1.3.0 --from-tag v1.2.0 --output notes.md
```

模型生成要求环境提供 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL_NAME`（OpenAI 兼容 `chat/completions` 接口，base URL 含 `/v1`），任一缺失或请求失败脚本报错退出。notes 来源是提交资料，发布前须核对产品影响、兼容要求和验证表述。

## 共享图标资源

[client/assets/icon.png](../client/assets/icon.png) 是母图，保留 alpha；修改时同步 ICO、ICNS 和 [Installer 图标](../installer/src-tauri/icons/)，核对两端打包配置与最终产物。
