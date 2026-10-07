# Installer

Tauri 安装器负责首次安装、运行时释放与环境修复；macOS 还提供启动已安装桌面端的快路径。日常运行和自更新由 Client 管理，构建与发布入口见 [Scripts](../scripts/README.md#构建安装器)。

## 任务入口

| 改动 | 入口与联动 |
|---|---|
| 启动、快路径与强制修复 | [lib.rs](src-tauri/src/lib.rs)、[bootstrap.rs](src-tauri/src/bootstrap.rs)；健康探针与 [Client 更新器](../client/main/runner/updater.ts)一致 |
| 安装阶段、取消与界面状态 | [install.sh](install.sh) / [install.ps1](install.ps1) 定义阶段，[powershell.rs](src-tauri/src/powershell.rs) 管理子进程，[bootstrap.rs](src-tauri/src/bootstrap.rs) 编排并发送 [events.rs](src-tauri/src/events.rs) 事件，[store.ts](src/store.ts) 消费 |
| 路径、嵌入资源与载荷 | [paths.rs](src-tauri/src/paths.rs)、[install_script.rs](src-tauri/src/install_script.rs)、[embedded_payload.rs](src-tauri/src/embedded_payload.rs)、[build.rs](src-tauri/build.rs)；[构建门禁](../scripts/README.md#构建安装器) |

## 资源与路径

安装器携带脚本、桌面安装包、Runner wheel 与 `server.py`、技能和引导音频。uv、Python、Runner 的第三方依赖及可选 OfficeCLI 仍可能需要联网下载；载荷齐全不等于离线可安装。

脚本依次从 `SPIRITAGENT_SETUP_DEV_REPO_ROOT` 指定仓库的 `installer/`、Tauri resources、内嵌 ZIP 查找；载荷只取后两处。内嵌 ZIP 解压到 `$SPIRITAGENT_HOME/bootstrap-payload/payload/`，供 Windows 单 EXE 使用。开发时指定仓库根只替换脚本，不会替开发者准备载荷。

`skills/` 是技能源，构建时暂存到 `payload/`，并经 Client 的 `extraResources` 打入桌面包。`payload/onboarding-audio/` 是入库音频，清理构建暂存时须保留。`install.ps1` 保持 UTF-8 BOM，暂存按字节复制以兼容 PowerShell 5.1；`install.cmd` 仅供 Windows 开发，不进生产载荷。

| 内容 | 路径 |
|---|---|
| Runner venv | `$SPIRITAGENT_HOME/runner/.venv` |
| 引导音频 | `$SPIRITAGENT_HOME/audio/onboarding/<lang>/` |
| 安装日志 | `$SPIRITAGENT_HOME/logs/bootstrap-installer.log` |
| 桌面端 | macOS `/Applications/SpiritAgent.app`；Windows `%LOCALAPPDATA%\Programs\SpiritAgent` |

Home 默认位于 Windows `%LOCALAPPDATA%\SpiritAgent` 或 macOS `~/Library/Application Support/SpiritAgent`，可由 `SPIRITAGENT_HOME` 覆盖。修改路径须同步 [paths.rs](src-tauri/src/paths.rs)、安装脚本、桌面查找与运行端。

## 安装与修复

仅 macOS 在未指定 `--repair` / `--reinstall` 时尝试快路径：完成标记、桌面二进制与 Runner 依赖均健康时，直接打开已安装的桌面端并退出；进程启动失败回到安装 UI。健康探针须与 [Client 更新器](../client/main/runner/updater.ts) 同步。Windows 从桌面快捷方式启动应用，不走此快路径。

安装顺序为 `welcome → install-python → unpack-runner → unpack-desktop → install-skills → finalize`，由两端脚本的 manifest 定义。每阶段独立进程，不继承脚本变量；Runner 使用 uv 管理的 Python 和独立 venv，优先查找 3.13、再查 3.14，无现成运行时则安装 3.13。只有 finalize 写完成标记。

编排通过 `-Manifest` / `-Stage` 调用脚本，载荷目录与格式经 `SPIRITAGENT_BUNDLED_*` / `SPIRITAGENT_INSTALLER_FORMAT` 下发；手动调用的同义参数优先。结果为带哨兵前缀的单行 JSON，普通日志不参与协议解析。UI 使用固定主题、自绘标题栏；[失败页](src/routes/failure.tsx) 提供重试与日志入口。

取消尽力终止当前阶段的进程树（macOS 进程组、Windows Job Object），失败时退回只终止脚本。退出后读取残余输出有超时，残留进程不会无限拖住结果。取消或失败可能留下半成品或挂载的 DMG；重试从头执行各阶段，`uv venv --clear` 重建 Runner 环境，不凭旧完成标记跳过。

## 平台与失败处理

| 范围 | 约束与处置 |
|---|---|
| Runner 依赖下载 | 可按 `SPIRITAGENT_PYPI_INDEX_URL` / `PIP_INDEX_URL` 或默认镜像重试 |
| uv、Python、OfficeCLI | 不共享 Runner 依赖的镜像回退；OfficeCLI 尽力安装，失败不阻断主体 |
| 内置技能 | 覆盖同名文件、保留用户自装内容；`.no-bundled-skills` 同时跳过技能释放与 OfficeCLI 安装。平台过滤由 Client / Runner 完成，后续自更新由 [Client](../client/main/lifecycle/bundled-skills.ts) 同步技能 |
| 安装器副本 | 完成后尽力自拷贝到 `$SPIRITAGENT_HOME/spiritagent-setup[.exe]`，失败不阻断安装；修复优先使用原完整安装器并加 `--repair`。macOS 暂存可使用符号链接，而内嵌 ZIP 跳过符号链接，脱离 `.app` resources 的自拷贝副本不能保证独立修复 |
| macOS 签名 | 仅针对安装器自拷贝：清 quarantine 后检查类型；未签名或损坏 ad-hoc 可补签，证书签名的副本只做验证（失败记录日志）、不降级；桌面 `.app` 只清除扩展属性 |
| Windows 桌面与卸载 | 标准流程静默运行内嵌 NSIS；手动 `-InstallerFormat zip` 需自备 ZIP，解包到 `$SPIRITAGENT_HOME/apps/SpiritAgent/`，完成页启动支持此回退路径，不创建快捷方式。NSIS 卸载只移除应用，Home 数据保留 |

## 内置技能文档

`skills/` 面向运行时 Agent。入口说明任务适用条件，长说明放支持文件；维护时保留平台、权限、作者和许可证，核对支持文件及 Client / Runner 解析。安装载荷文档遵循自身格式，静态检查不能证明模型使用效果。

## 契约与验证

载荷版本与导入面由构建门禁校验。安装改动在目标平台分别验证首装、修复、macOS 快路径、取消与失败重试，并检查真实内嵌文件、路径和 BOM；签名、启动及进程树终止需要实机验证。
