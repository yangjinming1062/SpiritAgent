# Installer

Tauri 安装器负责首次安装与环境修复，日常运行及更新归 Client。macOS 可在已安装环境健康时直接启动桌面端；构建和发布归 [Scripts](../scripts/README.md#构建安装器)。

## 任务入口

| 修改任务 | 入口 |
|---|---|
| 启动、快路径及修复 | [lib.rs](src-tauri/src/lib.rs)、[bootstrap.rs](src-tauri/src/bootstrap.rs)；健康探针与 [Client 更新器](../client/main/runner/updater.ts)一致 |
| 安装、取消和事件 | [install.sh](install.sh)、[install.ps1](install.ps1) 定义阶段，[powershell.rs](src-tauri/src/powershell.rs) 管进程，[events.rs](src-tauri/src/events.rs) 与 [store.ts](src/store.ts) 连接编排和 UI |
| 路径与内嵌载荷 | [paths.rs](src-tauri/src/paths.rs)、[install_script.rs](src-tauri/src/install_script.rs)、[embedded_payload.rs](src-tauri/src/embedded_payload.rs)、[build.rs](src-tauri/build.rs) |

## 资源与路径

载荷包含安装脚本、桌面包、单个 Runner wheel、技能和引导音频。uv、Python、Runner 第三方依赖及可选 OfficeCLI 仍可能联网下载，不能将载荷完整等同离线可安装。

脚本依次从开发仓库覆盖、Tauri resources 和内嵌 ZIP 查找；`SPIRITAGENT_SETUP_DEV_REPO_ROOT` 只替换脚本，不准备载荷，载荷仅从后两者读取。Windows 单 EXE 解压到 Home 下 `bootstrap-payload/payload/`。`skills/` 经构建暂存并打入桌面 `extraResources`；入库的 `payload/onboarding-audio/` 不能随暂存清理。PowerShell 5.1 所需的 `install.ps1` UTF-8 BOM 必须按字节保留，`install.cmd` 仅用于开发。

| 内容 | 位置 |
|---|---|
| Home | 默认 Windows `%LOCALAPPDATA%\SpiritAgent`，macOS `~/Library/Application Support/SpiritAgent`；`SPIRITAGENT_HOME` 可覆盖 |
| Runner venv | Home 下 `runner/.venv` |
| 引导音频 | Home 下 `audio/onboarding/<lang>/` |
| 安装日志 | Home 下 `logs/bootstrap-installer.log` |
| 桌面应用 | Windows `%LOCALAPPDATA%\Programs\SpiritAgent`，macOS `/Applications/SpiritAgent.app` |

改变路径须同时核对 `paths.rs`、两端脚本和 Client 查找逻辑，不能只改安装写入端。

## 安装与修复

仅 macOS 在未指定 `--repair`／`--reinstall` 时尝试快路径：完成标记、桌面二进制和 Runner 包内 `python -I -m spirit_agent_runner --check` 均有效才启动并退出。探针超时或启动失败回安装 UI；Windows 从桌面快捷方式启动。

阶段真源为两端脚本 manifest：准备 → Python → Runner → 桌面包 → 技能 → 完成。每阶段是独立进程，不能依赖前阶段 shell 变量；Runner 使用 uv 管理 Python 和独立 venv，优先已有 3.13、再 3.14，否则安装 3.13。仅 finalize 写完成标记。

编排经 `-Manifest`／`-Stage` 调用脚本，环境变量 `SPIRITAGENT_BUNDLED_*` 与 `SPIRITAGENT_INSTALLER_FORMAT` 传递载荷，显式同义参数优先。协议只解析带哨兵的单行 JSON，普通日志不能充当结果。[失败页](src/routes/failure.tsx) 提供日志和重试入口。

取消与关闭均等待当前流程结束，包含 manifest 读取；关闭中拒绝新安装，重复关闭共用退出。进程树尽力由 macOS 进程组／Windows Job Object 终止，失败回退只停脚本，残余输出读取有预算。取消可能留下半成品或已挂载 DMG；重试从头执行，`uv venv --clear` 重建 Runner，不凭旧标记跳过阶段。

## 平台与失败处理

| 范围 | 处置 |
|---|---|
| 依赖下载 | Runner 依赖可按 `SPIRITAGENT_PYPI_INDEX_URL`／`PIP_INDEX_URL` 或默认镜像回退；uv、Python、OfficeCLI 不共享该回退 |
| 技能与 OfficeCLI | 同名覆盖，用户内容保留；`.no-bundled-skills` 同时跳过技能和 OfficeCLI，后者安装失败不阻断主体；后续技能同步由 [Client](../client/main/lifecycle/bundled-skills.ts) 管理 |
| 安装器副本 | 尽力复制到 Home 下 `spiritagent-setup[.exe]`，失败不阻断。修复优先原完整安装器加 `--repair`；macOS 暂存符号链接不进入内嵌 ZIP，脱离 `.app` resources 的副本不保证独立修复 |
| macOS 签名 | 自拷贝清 quarantine 后检查，未签名或损坏 ad-hoc 可补签，证书签名只验不降级；桌面 `.app` 仅清扩展属性 |
| Windows 桌面 | 默认静默运行内嵌 NSIS；手动 ZIP 模式需自备 ZIP，释放到 Home 下 `apps/SpiritAgent/`，完成页支持启动但不创建快捷方式；NSIS 卸载保留 Home 数据 |

## 内置技能文档

`skills/` 是面向运行时 Agent 的载荷，按自身格式维护，默认包含 computer-use、officecli、ocr-and-documents。`SKILL.md` 留适用条件、流程和必要约束，平台差异及高级操作放 `references/` 并由 `skill_view(name, file_path)` 按需读取。

变更须保留平台、权限、作者和许可证，核对引用文件及 Client／Runner 解析；不默认加载整套参考资料，也不将领域检索或表达风格无条件加入随包技能。静态检查不能证明模型使用效果。

## 契约与验证

Tauri Rust 与前端 API 保持相同次版本，依赖变更同时更新 [Cargo.toml](src-tauri/Cargo.toml)、[package.json](package.json) 和锁文件。构建门禁隔离安装实际 wheel 并核入口、版本和静态定义；安装阶段再次健康检查，失败不继续。

按目标平台验证首装、修复、macOS 快路径、取消和失败重试，同时检查实际内嵌文件、路径与 BOM；签名、启动和进程树终止需要实机。运行 Cargo 测试前把 `SPIRITAGENT_HOME` 指向专用临时目录，ZIP 回退测试会创建并删除桌面占位文件。命令见 [Scripts](../scripts/README.md#构建安装器)。
