# 仓库构建与验证

本目录维护仓库级检查、提示词调试、安装器构建和发布。命令默认在仓库根执行；Client 自有脚本由 [package.json](../client/package.json) 调用，首次安装流程见 [Installer](../installer/README.md)。

## 按改动选择验证

先按[源码启动说明](../README.md#从源码启动)准备依赖。验证范围按改动选择：

| 改动 | 检查 |
|---|---|
| 文档 | 事实、相对路径、锚点、图表分支与渲染；`git diff --check` 只检查空白 |
| Python | 提交前 hook；Backend 依赖变化另跑分层检查 |
| Client | `pnpm --dir client lint`、`pnpm --dir client typecheck` |
| 手机远程页面 | `pnpm --dir remote lint`、`pnpm --dir remote typecheck`、`pnpm --dir remote build`；扫码、麦克风和后台恢复须在 iOS Safari、Android Chrome 实机验收 |
| 共享协议 `shared/` | 同时执行 Client 与 Remote 的 lint、typecheck 和构建 |
| Windows 桌面 helper | 在 Windows 执行 `pnpm --dir client build:native`，或在 `client/desktop-host/` 做对应目标的 `cargo check --locked`；交互与恢复另做实机验收 |
| preload、构建入口或资源 | `pnpm --dir client build` 并核对实际产物 |
| Installer 前端与编排 | `pnpm --dir installer build`，以及完整载荷下的目标平台 Tauri 构建和安装验收 |
| wheel 或安装载荷 | 导入面门禁、精确版本与对应平台构建 |
| 音频 | 专项静态检查，必要时合成并试听 |

提交前完整入口（以 [.pre-commit-config.yaml](../.pre-commit-config.yaml) 为准）：

```bash
uv tool run --python 3.13 pre-commit run -a
```

hook 包含自动修正，执行后检查差异并复验。局部可先用 `--files`，不替代提交前全量检查；hook 不包含 Client typecheck、Backend 分层检查或原生验收。构建可能修改构建信息和版本清单，交付时区分静态、构建与真实运行结果。

## Windows 桌面验收

记录 Windows 版本、Electron 与 helper 架构、显示器布局和 DPI；按下表实测并保留预期、结果和失败后的恢复情况。`cargo check`、模拟和 macOS 构建不能代替 Windows 验收，核心输入、层级或恢复不成立时保留窗口模式。

| 范围 | 必须验证的行为 |
|---|---|
| 输入与层级 | 中文输入法、焦点、复制粘贴、选择文件和拖放；内部开窗或重复点击取得前台，外部程序可覆盖界面，后台校正不抢前台，透明空白穿透；桌面视频无独立悬浮精灵，窗口模式精灵正常恢复 |
| 工作区与系统操作 | 已有及新启动窗口的还原、最大化、贴边、拖动、缩放受工作区限制；全屏收起、退出恢复页面与草稿且不抢前台；Win+D、Alt+Tab、任务视图和虚拟桌面 |
| 多屏与电源 | 混合 DPI、交互屏切换、拔屏回退、锁屏及休眠恢复；仅交互屏显示视频背景，副屏保持系统壁纸 |
| 接管与恢复 | 快捷键冲突拒绝接管；紧急恢复、切回窗口、换号、鉴权失败、退出及更新恢复工作区、任务栏和图标，保留外部窗口摆放；Explorer 重启重探测失败时回退 |
| 进程故障 | 分别终止主进程、各 renderer、host 和 guardian，检查恢复或失联兜底；恢复失败保留 journal，下次启动先恢复且不自动重试接管 |
| 桌面业务 | 主对话与轻语互斥、手动展开可并存，同会话无重复提交或播放；后台面板保留未读；内部窗口、设置；首帧切换、循环与短暂动作返回，加载失败保留旧画面，换号清除旧媒体 |
| Dock 目录 | 对照[启动器契约](../docs/PROTOCOL.md#桌面呈现与本机启动器)覆盖各来源、去重和排除项，包括无快捷方式及打包应用；中英文、exe / 包名搜索和中文路径；图标分批补齐，局部失败或超时不阻塞其余条目，重扫拒绝旧句柄 |
| Dock 配置 | 连续点选不重复加入；文件浏览、拖放和带参数快捷方式；打包应用重启或更新仍可启动，目录展示上限不影响已保存条目校验；目标失效修复保留 ID 与顺序，取消或写盘失败保留原配置；面板不穿透，Esc 或失去前台关闭 |
| Dock 运行 | 启动前已有窗口、新启动、最小化、末窗口关闭及虚拟桌面切换均收敛，纯后台进程排除；多窗口归并、最近或指定窗口恢复、单个或全部关闭及取消保存提示；遮挡后重复点击置前，模态和跨屏行为正确，切换中视频背景与界面层级稳定；旧 ID 或激活失败不重复启动；固定、拖拽排序和取消固定回流 |
| 打包 | 各目标架构分别打包，resources 内 helper 架构与应用一致 |

所有恢复进程同时被强制终止不能保证即时恢复，还须验证下次启动处理遗留 journal。

## 导入检查

[`check_imports.py`](check_imports.py) 检查 Backend / Runner 的 future annotations、TYPE_CHECKING 名字泄漏和包级 facade，使用 Python 3.13：

```bash
uv run --no-project --python 3.13 python scripts/check_imports.py --strict-imports
uv run --no-project --python 3.13 python scripts/check_imports.py --fix
```

不带 `--strict-imports` 时违规仅输出诊断、退出码为 0；`--fix` 只自动移除 future annotations，修改后重跑严格模式。

默认跳过模块根目录的 `build/`、`dist/` 及各层 `.venv`、`__pycache__`，避免旧构建副本与当前源码导出表混用；显式传入文件仍逐个检查。实际 wheel 另由 `check_runner_facade.py` 校验。

## Backend 分层检查

```bash
uv run --no-project --python 3.13 python scripts/check_services_architecture.py
```

[`check_services_architecture.py`](check_services_architecture.py) 覆盖导入解析、包级环、层间及跨域依赖。允许边以脚本声明为准，修改边界时同步 [Backend](../backend/README.md#services-依赖边界) 的设计理由。

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

[`client/scripts/`](../client/scripts/) 的关键门禁：`assert-root-install.cjs` 检查 Client 根依赖，`write-build-stamp.cjs` 记录提交来源，`assert-dist-built.cjs` 检查六个页面及其本地 JS、样式和预加载资源。`launch-dev-electron.cjs` 管理开发进程，打包钩子准备 Windows helper 和 DPI manifest。

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

`--target mac|win` 默认按宿主推断，必须在对应系统构建；`--skip-runner` / `--skip-desktop` 仍要求同版本既有产物，`--output` 覆盖默认 `release/`。正式暂存只接受目标版本唯一 wheel，并校验它是否满足 `server.py` 的本地导入。单独诊断：

```bash
uv run --no-project --python 3.13 python scripts/check_runner_facade.py
```

单独检查默认取修改时间最新的 wheel，可用 `--wheel` / `--server-py` 指定输入；它会在临时 venv 安装 wheel 和依赖，需要可用的依赖源，不能替代正式构建的精确版本门禁。暂存与 Tauri 资源补丁由 [build_helpers.py](lib/build_helpers.py) 实现，Tauri 构建结束后恢复资源配置。

| 平台 | `release/` 产物 |
|---|---|
| macOS | 安装器 DMG；同时保留 Tauri 原始文件名和 `SpiritAgent-Setup-<version>.dmg` 别名 |
| Windows | `SpiritAgent-Setup-<version>.exe` 和 `SpiritAgent-<version>-update.zip` |

Windows 安装器内嵌 NSIS 包；update ZIP 由 [UpdateManifest.ps1](lib/UpdateManifest.ps1) 打包桌面产物及 blockmap、`runner/` 下的 wheel 与 `server.py`、签名 `latest-runner.yml` 和 `manifest.json`。技能随桌面包交付，Backend 上传后按库存重建桌面清单。生成 ZIP 需要 PowerShell、openssl 和[更新签名私钥](release-keys/README.md)，缺失或签名失败中止构建。

`--sign-identity`（可配合 `--notary-profile`）和 `--cert-thumbprint` 作用于 `client/release/` 的桌面端产物，再复制进 payload；`build.py` 不签安装器本身。Client 的 `.app` 公证另由上一节的环境配置驱动，不能把桌面产物签名等同于整套安装器已签名。

## 发布

推送 `vX.Y.Z` tag 触发 [release.yml](../.github/workflows/release.yml)。构建只接受三段数字，其他 `v*` tag 会触发后失败。版本来自 tag；双平台产物与 notes 汇总为草稿 release，核对后发布。已有 release 仅补产物和 notes，不改变发布状态。

GitHub release 与客户端更新是两个入口：自动更新需在 Backend 管理端“版本管理”上传 update ZIP，再由当前后端分发，通信与验签见 [PROTOCOL](../docs/PROTOCOL.md#自更新签名)。当前构建入口只生成 Windows update ZIP，macOS DMG 不等于可直接上传的同类更新包。

CI 的 `SPIRITAGENT_UPDATE_SIGNING_KEY` Secret 存 PEM 内容；工作流落地临时文件后传路径。`MINIMAX_API_KEY` 缺失只使 notes 降级。工作流未配置平台证书签名或 macOS 公证，不能把 CI 构建成功视为已完成二者。

## Release notes

[`gen_release_notes.py`](gen_release_notes.py) 按版本排序选前一个 tag，或由 `--from-tag` 指定区间，生成中文说明；模型不可用时回退为分组提交列表。以下 tag 须替换为仓库实际版本：

```bash
python scripts/gen_release_notes.py v1.3.0
python scripts/gen_release_notes.py v1.3.0 --from-tag v1.2.0 --output notes.md
```

模型生成通过环境提供 `MINIMAX_API_KEY`，可选 `MINIMAX_BASE_URL` / `MINIMAX_MODEL`（默认值见脚本）。notes 来源是提交资料，发布前须核对产品影响、兼容要求和验证表述。

## 共享图标资源

[client/assets/icon.png](../client/assets/icon.png) 是母图，保留 alpha；修改时同步 ICO、ICNS 和 [Installer 图标](../installer/src-tauri/icons/)，核对两端打包配置与最终产物。
