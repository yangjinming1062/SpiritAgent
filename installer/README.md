# Installer

## 1. 职责与边界

负责首次安装、运行时释放、修复和 launcher 快路径；认证与日常更新归 Client。构建发布见 [Scripts](../scripts/README.md)，更新信任边界见 [PROTOCOL](../docs/PROTOCOL.md#55-自更新签名client--backend--installer--backend)。安装 UI 与资源独立维护。

## 2. 设计意图

安装脚本与 payload 随安装器共同发布，Runner 使用 uv 管理的 Python 和独立 venv。资源嵌入不代表完全离线：安装仍需获取工具链、依赖和可选 OfficeCLI。

Skills 覆盖同名内置文件，但不删除用户自装内容；存在 `$SPIRITAGENT_HOME/.no-bundled-skills` 时跳过内置技能释放。安装器不解析平台字段，由 Client 与 Runner 过滤。

## 3. 架构地图

```text
src 前端 → src-tauri 编排 → install.ps1 / install.sh 阶段进程
                              ↓
                            payload
```

脚本按开发目录、Tauri resources、build.rs 内嵌 ZIP 的顺序解析，不在线下载脚本。嵌入解压到 `$SPIRITAGENT_HOME/bootstrap-payload/`；Windows 单 EXE 依赖这一兜底。资源解析见 [install_script.rs](src-tauri/src/install_script.rs)。

`skills` 是原始技能载荷，`payload` 是构建暂存区。运行时位置：

| 内容 | 路径 |
|---|---|
| Runner venv | `$SPIRITAGENT_HOME/runner/.venv` |
| 引导音频 | `$SPIRITAGENT_HOME/audio/onboarding/<lang>/` |
| 安装日志 | `$SPIRITAGENT_HOME/logs/bootstrap-installer.log` |

Home 默认位于 Windows `%LOCALAPPDATA%\SpiritAgent` 或 macOS `~/Library/Application Support/SpiritAgent`。路径定义见 [paths.rs](src-tauri/src/paths.rs)，修改须同步安装脚本与运行端。`install.cmd` 仅供 Windows 开发，不进入生产资源。

## 4. 关键设计决策

### 阶段与结果

阶段依次为 `welcome → install-python → unpack-runner → unpack-desktop → install-skills → finalize`。每阶段是独立进程，不能继承上一阶段脚本变量；完成标记只在 finalize 写入。

脚本通过带哨兵前缀的单行 NDJSON 返回结果，普通工具日志不得当作协议帧。取消中止当前脚本并置失败；修复通过 `uv venv --clear` 重建环境，不凭旧完成标记跳过。

前端事件定义见 [events.rs](src-tauri/src/events.rs) 与 [store.ts](src/store.ts)。

### 平台与联网

`install.ps1` 必须保持 UTF-8 BOM，构建按字节复制，避免 PowerShell 5.1 以本地代码页误解析。

Runner 依赖安装失败可按 `SPIRITAGENT_PYPI_INDEX_URL` / `PIP_INDEX_URL` 或默认镜像重试；uv、Python 和 OfficeCLI 不共享该回退。OfficeCLI 尽力安装，失败不阻断主体。

完成后自拷贝到稳定 setup 路径。macOS 清 quarantine 后按签名类型处理：未签名或损坏 ad-hoc 可补签，权威证书签名必须严格验证，不能降级为 ad-hoc。

macOS `/Applications/SpiritAgent.app` 兼作安装入口与 launcher。快路径同时检查完成标记、桌面二进制和 Runner 核心依赖；`--repair` / `--reinstall` 强制修复。健康探针须与 Client 更新器一致。

Windows ZIP 布局允许从 `$SPIRITAGENT_HOME/apps/SpiritAgent/SpiritAgent.exe` 启动。客户端 NSIS 的卸载入口只移除应用，本模块不另建卸载流程，Home 数据不随之清理。

### 内置技能文档

`skills` 面向运行时 Agent，不是仓库开发指令。描述简明说明适用任务，多工作流入口保留选择条件与导航，长说明放支持文件；不强制通用计划、固定修改次数或重复自检。

维护时保留平台、权限、作者和许可证，核对支持文件和两端解析。静态检查通过不等于模型使用行为已验证。

## 5. 与外部的契约

构建端提供准确版本的 wheel、桌面产物和资源；安装器按载荷执行，不猜测版本。安装位置、事件和健康检查按本文件及 [PROTOCOL](../docs/PROTOCOL.md) 维护，发布载荷门禁归 [Scripts](../scripts/README.md)。

## 6. 验证入口

命令见 [Scripts](../scripts/README.md#8-按改动选择验证)。分别验证首装、修复和快路径，检查真实嵌入文件、版本、路径、脚本 BOM、取消和失败重试。成功打包不等于安装可用；平台签名与启动须在目标系统验证。
