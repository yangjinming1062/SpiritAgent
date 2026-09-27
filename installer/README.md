# Installer

负责首次安装、运行时释放、修复与 launcher 快路径；认证和日常更新归 Client。安装 UI 与资源独立维护，构建发布见 [Scripts](../scripts/README.md)，更新信任边界见 [PROTOCOL](../docs/PROTOCOL.md#自更新签名)。

## 任务入口

| 改动 | 入口与联动 |
|---|---|
| 启动、快路径与强制修复 | [lib.rs](src-tauri/src/lib.rs)、[bootstrap.rs](src-tauri/src/bootstrap.rs)；健康探针与 [Client 更新器](../client/main/runner/updater.ts)一致 |
| 安装阶段、取消与界面状态 | [install.sh](install.sh) / [install.ps1](install.ps1) → [bootstrap.rs](src-tauri/src/bootstrap.rs) → [events.rs](src-tauri/src/events.rs)、[store.ts](src/store.ts) |
| 路径、嵌入资源与载荷 | [paths.rs](src-tauri/src/paths.rs)、[install_script.rs](src-tauri/src/install_script.rs)、[build.rs](src-tauri/build.rs)；[构建门禁](../scripts/README.md#构建安装器) |

## 设计意图

- 安装脚本与 payload 随安装器共同发布，Runner 使用 uv 管理的 Python 与独立 venv。
- 嵌入资源保证脚本和载荷随版本交付；安装仍需联网获取工具链、依赖及可选 OfficeCLI。
- Skills 覆盖同名内置文件，但保留用户自装内容；存在 `$SPIRITAGENT_HOME/.no-bundled-skills` 时跳过内置技能释放。安装器不解析平台字段，过滤归 Client 与 Runner。

## 资源与路径

`src` 前端经 `src-tauri` 编排调用安装脚本，脚本消费 payload。

脚本依次从开发目录、Tauri resources、build.rs 内嵌 ZIP 解析，不在线下载。嵌入资源解压到 `$SPIRITAGENT_HOME/bootstrap-payload/`，Windows 单 EXE 依赖此兜底。实现见 [install_script.rs](src-tauri/src/install_script.rs)。

`skills` 是原始技能载荷，`payload` 是构建暂存区。

| 内容 | 路径 |
|---|---|
| Runner venv | `$SPIRITAGENT_HOME/runner/.venv` |
| 引导音频 | `$SPIRITAGENT_HOME/audio/onboarding/<lang>/` |
| 安装日志 | `$SPIRITAGENT_HOME/logs/bootstrap-installer.log` |

Home 默认位于 Windows `%LOCALAPPDATA%\SpiritAgent` 或 macOS `~/Library/Application Support/SpiritAgent`。路径定义见 [paths.rs](src-tauri/src/paths.rs)，修改须同步安装脚本与运行端。`install.cmd` 仅供 Windows 开发，不进入生产资源。

## 安装与修复

仅 macOS 在未指定 `--repair` / `--reinstall` 时尝试启动快路径：完成标记、桌面二进制与 Runner 依赖均健康才直接启动桌面；启动失败回到安装 UI。Windows 从快捷方式启动桌面，不经过此快路径。

安装阶段为 `welcome → install-python → unpack-runner → unpack-desktop → install-skills → finalize`，定义在两端安装脚本的 manifest。每阶段独立进程，不继承脚本变量，只有 finalize 写完成标记。

脚本结果用带哨兵前缀的单行 NDJSON，普通日志不作为协议帧。取消中止当前脚本并置失败；修复使用 `uv venv --clear` 重建环境，不凭旧标记跳过。

## 平台与失败处理

| 范围 | 约束与处置 |
|---|---|
| PowerShell | `install.ps1` 保持 UTF-8 BOM，构建按字节复制，兼容 PowerShell 5.1 |
| Runner 依赖下载 | 可按 `SPIRITAGENT_PYPI_INDEX_URL` / `PIP_INDEX_URL` 或默认镜像重试 |
| uv、Python、OfficeCLI | 不共享 Runner 依赖的镜像回退；OfficeCLI 尽力安装，失败不阻断主体 |
| 安装器位置 | 完成后自拷贝到稳定 setup 路径 |
| macOS 签名 | 清 quarantine 后检查类型；未签名或损坏 ad-hoc 可补签，权威证书签名须严格验证，不降级 |
| macOS launcher | `/Applications/SpiritAgent.app` 兼作安装入口，启动分支见[安装与修复](#安装与修复) |
| Windows 启动与卸载 | ZIP 允许从 `$SPIRITAGENT_HOME/apps/SpiritAgent/SpiritAgent.exe` 启动；NSIS 卸载只移除应用，Home 数据保留，本模块不另建卸载流程 |

健康探针须与 Client 更新器一致。

## 内置技能文档

`skills` 面向运行时 Agent，不是仓库开发指令。描述简明说明适用任务，多工作流入口保留选择条件与导航，长说明放支持文件；不强制通用计划、固定修改次数或重复自检。

维护时保留平台、权限、作者和许可证，核对支持文件和两端解析。静态检查通过不等于模型使用行为已验证。

## 契约与验证

载荷须有准确版本，安装器不猜测版本。检查命令见 [Scripts](../scripts/README.md#按改动选择验证)；分别验证首装、修复、macOS 快路径，检查真实嵌入文件、版本、路径、BOM、取消与失败重试。签名和启动在目标平台验证。
