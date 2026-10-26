# 仓库构建与验证

本目录维护仓库级检查、提示词调试、安装器构建和发布。命令默认在仓库根执行；Client 自有脚本由 [package.json](../client/package.json) 调用，首次安装流程见 [Installer](../installer/README.md)。

## 按改动选择验证

命令默认在仓库根运行，依赖准备见[源码启动](../README.md#从源码启动)。按变化选择专项检查，提交前执行完整入口：

```bash
uv tool run --python 3.13 pre-commit run -a
```

该命令含自动修正，**仅在修改完成、准备提交时执行**，随后检查差异并复验；开发过程中使用只读检查。hook 清单以 [.pre-commit-config.yaml](../.pre-commit-config.yaml) 为准，不包含 Client typecheck、Backend 分层或原生实机验收。

| 改动 | 专项验证 |
|---|---|
| 文档 | 核对源码事实、相对路径、标题锚点、图表分支与渲染；`git diff --check` 只查空白 |
| Python | 导入门禁；Backend 或 Runner 边界变化另跑本页架构检查 |
| Client | `pnpm --dir client lint`、`pnpm --dir client typecheck`；preload、资源或构建入口变化再跑 `pnpm --dir client build` 并检查产物 |
| Remote | `pnpm --dir remote lint`、`pnpm --dir remote typecheck`、`pnpm --dir remote build`；扫码、麦克风和后台恢复另测 iOS Safari 与 Android Chrome |
| 共享协议／预设 | `backend/.venv/Scripts/python.exe scripts/generate_protocol.py --check`（其他平台用 Backend Python），再跑 Client 和 Remote 的 lint、typecheck、build |
| Windows 原生 | `pnpm --dir client build:native` 构建 Rust host 与同架构托盘 DLL；`cargo check --locked` 只覆盖 Rust，另执行 Windows 实机清单 |
| macOS 原生 | 在 macOS 执行 `pnpm --dir client build:native`，再以 `pnpm --dir client exec electron-builder --dir --mac --arm64` 构建对应包（x64 替换架构参数），并执行本页实机清单 |
| Installer／wheel | `pnpm --dir installer build`、完整载荷下的目标平台 Tauri 构建及安装修复；wheel 另做隔离安装检查 |
| 音频／模型行为 | 音频专项检查与试听；模型修改核对实际请求、真实返回和消费结果，生成链范围见 [PIPELINE](../docs/PIPELINE.md#验证) |

协议修改后用 `generate_protocol.py` 更新生成契约；它同时核对 Backend 与 Runner 预设清单，`--check` 只查漂移。构建可能修改版本清单与构建信息，检查最终差异，并区分静态、构建与真实运行证据。

## 持续生活验收

体验标准归 [DESIGN](../docs/DESIGN.md#当前能力与体验验收)，执行边界与已知差距见 [生活模块](../backend/features/life/README.md)。以下是验收方法，不是已有运行结论。

完成伙伴引导并配置所需模型、能力和有限额度，记录代码版本、时区、平台与供应商。首轮可连续观察七个本地日；未覆盖跨日、等待或恢复时延长观察，不能只以天数达标。选择可核对来源或成果的生活目标，不每天人工下发步骤冒充自主推进。

以生活页、手机伙伴页和自然交流观察变化，必要时经[生活接口](../backend/entrypoints/http/routes/companion_life.py)关联事项、步骤、业务任务与成果。记录“缘由 → 行动或等待 → 结果 → 后续影响”，并观察用户不查看执行详情能否察觉延续。

| 场景 | 核对内容 |
|---|---|
| 自主开始与跨日继续 | 动机有性格或经历依据；条件允许时实际行动，等待有原因，后续选择承接已有结果 |
| 事实与分享 | 阅读范围、来源和作品可核查；计划、虚拟情景、执行与成果不混淆；经历进入后续选择、聊天或日记，允许不分享 |
| 暂停、放下与转向 | 明确指令、普通闲聊和自主决定的来源分别核对；用户暂停不被擅自恢复，旧事项保留迟到成果及转向关系 |
| 安静与多端 | 静止档不主动打扰，允许的云端生活继续；三端读取同一事项；自主档验证选新画面，静止档验证已有静态目标恢复，切换不新增制作 |
| 失败、额度与恢复 | 在隔离环境覆盖能力不可用、额度耗尽、明确失败、未知提交、断线与重启；沿原请求核对，不伪造成功、不重复付费、不清零已用额度、不串账户 |

自然使用与故障注入分开留证；记录人工介入、配置变化、失败样本和未覆盖项，材料脱敏。数据库恢复与平台呈现仍须各自验收，单次成功或静态检查不能证明长期行为稳定。

## macOS 桌面验收

平台与构建前提见 [macOS 模块](../client/desktop-macos/README.md)。记录系统、Electron、架构、屏幕和权限状态；开发版与打包版都需验证，跨平台构建结果不能替代实机行为。

| 范围 | 必测条件与结果 |
|---|---|
| 加载与打包 | arm64、x64 分别构建；缺文件、错误架构、接口不匹配时拒绝交接并保留来源窗口；正式包验证签名与公证 |
| 图层与输入 | Finder 图标位于背景之上，原生框选、键盘和拖放可用；空白穿透，功能区可交互；外部应用可覆盖界面，显式操作才置前 |
| 屏幕与工作区 | Dock 各方向、自动隐藏、菜单栏、混合 Retina、负坐标、旋转、拔屏与切屏；产品避让系统区域，不改变其他应用工作区 |
| Spaces 与全屏 | 切换 Space、Mission Control、Stage Manager、全屏时媒体与交互层同步；旧窗口 ID 不可操作，首次最小化窗口不猜归属，恢复不抢焦点 |
| 辅助功能 | 无权限仍能进入、交接、浏览、固定与启动，窗口投影提供引导、关闭迁移禁用；授予或撤销后刷新，动作前重核权限，启动受理不冒称窗口已定位 |
| 目录与运行窗口 | 系统／共享／用户应用、多层目录、中文、符号链接、坏包及卸载；搜索、图标分批、拖入、修复、排序和固定恢复；本屏恢复、跨屏迁移、新启动定位、最小化及模态限制均核实真实窗口 |
| 生命周期 | 双向交接页面、会话、草稿和阅读位置；录音／转写／上传阻止切换；换号、撤权、退出、更新取消、renderer 崩溃、休眠与进入中取消均收敛；迟到动作不作用新会话，原生崩溃后首启留窗口模式 |

## Windows 桌面验收

记录 Windows、Electron、host／DLL 架构、屏幕与 DPI。依据 [Windows 宿主](../client/desktop-windows/README.md)及[启动器契约](../docs/PROTOCOL.md#桌面呈现与本机启动器)验证，保留失败后的恢复结果；输入、层级或恢复不成立时使用窗口模式。

| 范围 | 必测条件与结果 |
|---|---|
| 层级与输入 | 透明图标层及传统 WorkerW；视频在原生图标下，透明空白穿透；输入法、焦点、文件选择、拖放、复制粘贴与外部应用覆盖正常，后台校正不抢前台 |
| 原生桌面 | 用户／公共／OneDrive 项目、回收站、大量图标和不同排列；双击、右键、框选、改名、删除撤销及拖放保持原生行为，退出保留用户整理与显隐偏好 |
| 托盘显示与交互 | 本屏原生行嵌入底栏、主题前景和第三方位图可辨；不接管其他屏或第三方面板；图标增减、显隐设置、滚动裁剪与命中一致；单／双击、右键、悬停、Win+B、隐藏图标菜单及系统弹窗正确定位，反复开关不漂移、不误判全屏；结构不支持时回窗口 |
| 工作区与系统行为 | 底部预留采用共享布局，顶部无额外空带，内部窗口不重复扣除；已有／新启动窗口的还原、最大化、贴边与拖动遵守实际工作区；弹层不扩大预留，Win+D、Alt+Tab、任务视图及全屏可恢复且不抢焦点 |
| 多屏与电源 | 混合 DPI、负坐标、竖屏、三屏、切屏、拔屏、首选屏缺失与重接；只交互屏受影响，其余屏任务栏和图标可用；锁屏、休眠恢复；缺屏回退保留首选，重接不自动接管 |
| 正常与异常恢复 | 快捷键冲突拒绝接管；返回窗口、切屏、换号、撤权、退出、更新与紧急恢复释放 AppBar，并恢复托盘样式、任务栏几何／裁剪／显隐；Explorer 重启重识别，失败回退；外部程序摆放保留 |
| 进程与 journal | 分别终止主进程、renderer、host、guardian，核对失联兜底；host 死亡或续租失效恢复样式；不完整 journal 拒绝读取并留诊断，恢复失败保留记录，下次启动先恢复；使用新 hash DLL，不覆盖已加载文件 |
| 交接与界面 | 窗口／桌面接续页面、会话、草稿、附件和阅读位置，目标失败回来源；录音／转写／上传阻止切换；宽窄屏与高 DPI 下头像居中、两翼滚动、原生命中随裁剪；菜单互斥、键盘与焦点恢复、实底内容和降低动态降级可用 |
| 业务与媒体 | 同会话无重复提交或播放，未读与未听按真实阅读／收听分别清除；生活、动态、日记提醒独立；首帧切换、循环、短动作返回、静态回退、加载失败保留旧画面，换号清除旧媒体 |
| 目录与固定 | 各来源、去重、排除项、无快捷方式和打包应用；长名称／中文搜索、分批图标与局部失败不阻断目录；缓存过期重新验证启动，重扫拒绝旧句柄；固定与启动分离，拖放、带参数快捷方式、排序、修复和写盘失败保留配置 |
| 运行与窗口动作 | 本屏优先、异屏迁移、多窗口／模态、最大化／最小化、末窗关闭与虚拟桌面切换；关闭保留保存提示且不波及异屏；慢响应、并发预算耗尽、前台暂空、取消与旧授权回执不导致重复启动或抢前台；新启动和 Jump List 定位本屏，匹配歧义不误搬窗口 |
| 安装产物 | 各架构 helper、托盘样式 DLL 与应用一致，无外部 VC／UCRT 依赖；manifest 指向随包 hash 文件，打包页面加载图标与本地资源 |

所有恢复进程同时被强制终止时不保证即时复原，必须另验下次启动处理待恢复 journal。

## 导入检查

```bash
uv run --no-project --python 3.13 python scripts/check_imports.py --strict-imports
uv run --no-project --python 3.13 python scripts/check_runner_architecture.py
```

[导入检查](check_imports.py)覆盖 Backend／Runner 的 future annotations、TYPE_CHECKING 泄漏与包级 facade。不加 `--strict-imports` 时违规仅诊断、退出码仍为 0；`--fix` 只移除 future annotations，修正后须重跑严格模式。默认跳过构建目录、虚拟环境和缓存，显式文件仍检查。

[Runner 分层检查](check_runner_architecture.py)静态核对依赖、跨能力私有访问、导入环、导入副作用与资源所有权；`--runner <目录>` 可检查临时样本，不启动 Runner。规则语义归 [Runner](../runner/README.md#装配调用与资源)，实际 wheel 和资源回收须另验。

## Backend 架构检查

```bash
uv run --no-project --python 3.13 python scripts/check_services_architecture.py
```

[检查器](check_services_architecture.py)核对 Backend 导入、依赖环、目录方向、跨业务 ORM／私有入口、公开返回 ORM、入口 SQL 和禁用目录；纯类型与静态词表不算业务依赖。`--backend <目录>` 可检查临时样本，规则语义归 [Backend](../backend/README.md#模块依赖边界)。

## 提示词调试

模型决策契约的只读门禁：

```bash
uv run --project backend --locked python scripts/check_llm_contracts.py
```

[检查器](check_llm_contracts.py)从显式声明 `preserve_choices` 的调用点读取实际输出 Schema，复用运行时校验，并核对公共视觉包装的参数传递；目录无效、无有效检查项或相关入口无法解析时均报错。它同时检查可识别的固定候选正则模式，不连接数据库或供应商，提交门禁包含此项。动态候选集合、格式修复、回退中的决策保持和真实模型效果仍按受影响链路另验，临时验证程序不提交。

```bash
uv run --project backend python scripts/debug_prompt.py
uv run --project backend python scripts/debug_prompt.py -m "你好" --persona-name "星奈" --json
uv run --project backend python scripts/debug_prompt.py --db --user-id 1 --preset companion
```

[调试器](debug_prompt.py)展示基础预设、画像、工具与单条输入，默认用模拟资料，`--db` 需要配置及数据库。它不执行模型回合，也不覆盖全部动态协议；真实请求使用 [Backend 调试日志](../backend/README.md#llm-调试日志)核查，不能把调试输出当成供应商实际输入。

## 引导音频

文案、tag、生成和静态校验见 [专项说明](onboarding-audio/README.md)。

## Client 开发与打包脚本

[Client scripts](../client/scripts/) 的安装、构建和打包门禁由 [package.json](../client/package.json)调用：根依赖校验、构建来源记录、全入口页面／资源检查，以及目标平台原生模块准备。`build` 与 `beforePack` 都验证产物，直接调用 builder 仍需先构建。

macOS `afterPack` 检查架构，并由 [macos-app-identity.cjs](../client/scripts/macos-app-identity.cjs)给主执行文件派生应用专属 UUID，避免与其他 Electron 应用混淆本地网络授权（[Apple TN3178](https://developer.apple.com/documentation/technotes/tn3178-checking-for-and-resolving-build-uuid-problems)）。该步修复 ad-hoc 签名，不改变 Framework／helper；主执行文件不再匹配 Electron 官方 dSYM。整包本地验签可用 `CSC_IDENTITY_AUTO_DISCOVERY=false pnpm --dir client exec electron-builder --dir --mac --arm64 -c.mac.identity=-`。

`afterSign` 调用 [notarize.cjs](../client/scripts/notarize.cjs)，关闭 builder 内建公证；支持 keychain profile、Apple ID 和 API key，未配置跳过、部分配置报错，超时与临时文件由脚本清理。正式签名和公证必须在 macOS 实机验证，不能由 ad-hoc 结果替代。

## 手机远程页面构建

`pnpm --dir remote dev` 启动浏览器开发入口，`pnpm --dir remote build` 产出 `remote/dist/`。Backend 同源托管 `/remote/`。

[Backend Dockerfile](../backend/Dockerfile)从仓库根上下文构建 Remote 与管理端共享 HTTP 产物，再复制至镜像；[.dockerignore](../.dockerignore)排除桌面产物、数据与凭据。管理端固定文件名脚本不能长期缓存。运行与设备恢复见 [Remote](../remote/README.md)。

## 构建安装器

[build.py](build.py)同步版本、构建 Runner wheel 与 Client、暂存载荷，再构建 Tauri Installer。准备 uv、Node.js／pnpm、Rust／Cargo 与目标平台工具链；Backend 镜像独立构建。

```bash
uv run --no-project --python 3.13 python scripts/build.py --version 1.2.3
```

示例版本会修改版本清单。`--target mac|win` 默认按宿主推断，必须在对应系统构建；`--skip-runner`／`--skip-desktop` 仍要求同版本产物，`--output` 更改输出目录。

Runner 在临时源码副本构建；暂存只接受目标版本的唯一 wheel，核对源码字节并在仓库外 venv 安装检查入口、版本、依赖和工具定义。诊断命令如下，默认选择最新 wheel，正式检查应使用 `--wheel <路径>` 指定产物：

```bash
uv run --no-project --python 3.13 python scripts/check_runner_package.py
```

载荷和 Tauri 配置由 [build_helpers.py](lib/build_helpers.py)准备，构建结束恢复配置。macOS 输出安装器 DMG 及规范文件名副本；Windows 输出 Setup EXE 和 update ZIP。Windows ZIP 包含桌面产物、blockmap、单一 Runner wheel 与签名清单，需 PowerShell、openssl 及[签名私钥](release-keys/README.md)。

`--sign-identity`／`--notary-profile` 与 `--cert-thumbprint` 作用于桌面产物，不签 Installer 本身；Client `.app` 公证另由上一节配置控制。产物、载荷与安装修复验收见 [Installer](../installer/README.md)。

## 发布

推送三段数字 `vX.Y.Z` tag 触发 [release.yml](../.github/workflows/release.yml)，双平台产物与说明汇总为草稿，核对后发布；其他 `v*` tag 会触发后失败。已有 release 仅补产物和说明，不改变发布状态。

GitHub 安装包与应用内更新分开交付。Windows 更新 ZIP 需上传 Backend 管理端，由该后端分发；macOS 当前 DMG 不能当作同类更新 ZIP。CI 的签名 Secret 解释和私钥边界归 [签名配置](release-keys/README.md)，工作流尚未配置平台证书签名与 macOS 公证。

跨端契约同时发布 Backend、Client 与 Remote。切换前按[恢复契约](../docs/PROTOCOL.md#备份校验与覆盖恢复)建立数据、素材和代码恢复点，停止新任务并收敛在途工作；存量数据库变化需新增迁移。核对记录、关联、素材与草稿后开放写入，失败恢复同一备份点。版本说明需要的模型配置见下节。

## Release notes

[gen_release_notes.py](gen_release_notes.py)按版本排序寻找前一 tag，或接受显式起点；用实际 tag 替换示例：

```bash
uv run --project backend python scripts/gen_release_notes.py v1.3.0
uv run --project backend python scripts/gen_release_notes.py v1.3.0 --from-tag v1.2.0 --output notes.md
```

环境需提供 `LLM_API_KEY`、`LLM_BASE_URL`（含 `/v1`）和 `LLM_MODEL_NAME`，调用 OpenAI 兼容 `chat/completions`；缺失或失败即报错，CI 使用同名 repo secrets。说明由提交资料生成，发布前核对产品影响、兼容要求与验证结论。

## 共享图标资源

[client/assets/icon.png](../client/assets/icon.png) 是母图，保留 alpha；修改时同步 ICO、ICNS 和 [Installer 图标](../installer/src-tauri/icons/)，核对两端打包配置与最终产物。
