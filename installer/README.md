# Installer

负责首次安装、运行时释放、修复与 launcher 快路径；认证和日常更新归 Client。安装 UI 与资源独立维护，构建发布见 [Scripts](../scripts/README.md)，更新信任边界见 [PROTOCOL](../docs/PROTOCOL.md#自更新签名)。

## 任务入口

| 改动 | 入口与联动 |
|---|---|
| 启动、快路径与强制修复 | [lib.rs](src-tauri/src/lib.rs)、[bootstrap.rs](src-tauri/src/bootstrap.rs)；健康探针与 [Client 更新器](../client/main/runner/updater.ts)一致 |
| 安装阶段、取消与界面状态 | [install.sh](install.sh) / [install.ps1](install.ps1) → [powershell.rs](src-tauri/src/powershell.rs)（以 CLI 参数选择 `-Manifest` 或 `-Stage`，payload 路径与格式经 `SPIRITAGENT_BUNDLE*` 等环境变量下发，手动运行时同义参数优先于环境变量；执行脚本并解析哨兵结果行）→ [bootstrap.rs](src-tauri/src/bootstrap.rs) → [events.rs](src-tauri/src/events.rs)、[store.ts](src/store.ts) |
| 路径、嵌入资源与载荷 | [paths.rs](src-tauri/src/paths.rs)、[install_script.rs](src-tauri/src/install_script.rs)、[embedded_payload.rs](src-tauri/src/embedded_payload.rs)、[build.rs](src-tauri/build.rs)；[构建门禁](../scripts/README.md#构建安装器) |

## 设计意图

- 安装脚本与 payload 随安装器共同发布，Runner 使用 uv 管理的 Python 与独立 venv。
- 嵌入资源保证脚本和载荷随版本交付；安装仍需联网获取工具链、依赖及可选 OfficeCLI。
- Skills 覆盖同名内置文件，但保留用户自装内容；存在 `$SPIRITAGENT_HOME/.no-bundled-skills` 时跳过内置技能释放及 OfficeCLI 安装。安装器不解析平台字段，过滤归 Client 与 Runner。

## 资源与路径

`src` 前端经 `src-tauri` 编排调用安装脚本，脚本消费 payload。

安装脚本依次从开发目录（`SPIRITAGENT_SETUP_DEV_REPO_ROOT` 指向仓库根时取其 `installer/` 下脚本）、Tauri resources、build.rs 内嵌 ZIP 解析；payload 只取后两处，均不在线下载。嵌入资源解压到 `$SPIRITAGENT_HOME/bootstrap-payload/`，Windows 单 EXE 依赖此兜底。实现见 [install_script.rs](src-tauri/src/install_script.rs)。

`skills` 是原始技能载荷，`payload` 是构建暂存区；其中 `payload/onboarding-audio/` 是入库的预制音频，不能当临时产物清理。

| 内容 | 路径 |
|---|---|
| Runner venv | `$SPIRITAGENT_HOME/runner/.venv` |
| 引导音频 | `$SPIRITAGENT_HOME/audio/onboarding/<lang>/` |
| 安装日志 | `$SPIRITAGENT_HOME/logs/bootstrap-installer.log` |
| 桌面端 | macOS `/Applications/SpiritAgent.app`；Windows `%LOCALAPPDATA%\Programs\SpiritAgent`（由两端脚本的 unpack-desktop 写入，[bootstrap.rs](src-tauri/src/bootstrap.rs) 按同一路径查找，修改须三处同步） |

Home 默认位于 Windows `%LOCALAPPDATA%\SpiritAgent` 或 macOS `~/Library/Application Support/SpiritAgent`。路径定义见 [paths.rs](src-tauri/src/paths.rs)，修改须同步安装脚本与运行端。`install.cmd` 仅供 Windows 开发，不进入生产资源。

## 安装与修复

仅 macOS 在未指定 `--repair` / `--reinstall` 时尝试启动快路径：完成标记、桌面二进制与 Runner 依赖均健康时，安装器直接打开已安装的 `/Applications/SpiritAgent.app` 并退出；启动失败回到安装 UI。Windows 从快捷方式启动桌面，不经过此快路径。

安装阶段为 `welcome → install-python → unpack-runner → unpack-desktop → install-skills → finalize`，定义在两端安装脚本的 manifest。每阶段独立进程，不继承脚本变量，只有 finalize 写完成标记。

脚本结果用带哨兵前缀的单行 NDJSON，普通日志不作为协议帧。取消终止当前脚本及其后代进程（macOS 为脚本自建的进程组，Windows 为 Job Object）并置失败；脚本退出后只在 `PIPE_DRAIN_TIMEOUT` 内读取残余输出，脱离进程组或 Job 的残留进程不会拖住结果。被终止的阶段可能留下未完成的产物（如半装的桌面端、未卸载的 DMG），重试从头执行各阶段；修复使用 `uv venv --clear` 重建环境，不凭旧标记跳过。

## 平台与失败处理

| 范围 | 约束与处置 |
|---|---|
| PowerShell | `install.ps1` 保持 UTF-8 BOM，构建按字节复制，兼容 PowerShell 5.1 |
| Runner 依赖下载 | 可按 `SPIRITAGENT_PYPI_INDEX_URL` / `PIP_INDEX_URL` 或默认镜像重试 |
| uv、Python、OfficeCLI | 不共享 Runner 依赖的镜像回退；OfficeCLI 尽力安装，失败不阻断主体 |
| 安装器位置 | 完成后自拷贝到稳定路径 `$SPIRITAGENT_HOME/spiritagent-setup`（Windows 带 `.exe`），可手动重跑或加 `--repair` 修复；仓库内不创建指向它的快捷方式 |
| macOS 签名 | 仅针对安装器自拷贝：清 quarantine 后检查类型；未签名或损坏 ad-hoc 可补签，证书签名的副本只做验证（失败记录日志）、不降级；桌面 `.app` 只清除扩展属性 |
| macOS launcher | Tauri 安装器（productName“唤生”，与显示名同为“唤生”的桌面端 `SpiritAgent.app` 不同；含自拷贝 `spiritagent-setup`）兼作启动入口，分支见[安装与修复](#安装与修复) |
| Windows 桌面与卸载 | 桌面端由内嵌 NSIS 包静默安装到 `%LOCALAPPDATA%\Programs\SpiritAgent`；手动以 `-InstallerFormat zip` 运行 `install.ps1`（需自备桌面 ZIP，构建不产出）时解包到 `$SPIRITAGENT_HOME/apps/SpiritAgent/`，安装完成页的启动命令可回退到该布局，但不创建快捷方式。NSIS 卸载只移除应用，Home 数据保留，本模块不另建卸载流程 |

健康探针须与 Client 更新器一致。

## 内置技能文档

`skills` 面向运行时 Agent，不是仓库开发指令。描述简明说明适用任务，多工作流入口保留选择条件与导航，长说明放支持文件；不强制通用计划、固定修改次数或重复自检。

维护时保留平台、权限、作者和许可证，核对支持文件和两端解析。静态检查通过不等于模型使用行为已验证。

## 契约与验证

载荷须有准确版本，安装器不猜测版本。检查命令见 [Scripts](../scripts/README.md#按改动选择验证)；分别验证首装、修复、macOS 快路径，检查真实嵌入文件、版本、路径、BOM、取消与失败重试。签名和启动在目标平台验证。
