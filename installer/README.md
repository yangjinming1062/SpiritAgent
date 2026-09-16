# Installer

## 1. 职责与边界

负责安装、修复与运行时释放；认证交互由客户端承载，更新与签名边界见 [PROTOCOL §5.5](../docs/PROTOCOL.md#55-自更新签名client--backend--installer--backend)。安装 UI 与资源独立维护，不借用其他模块的样式或组件；产物由构建阶段显式输入，构建入口见 [scripts/README.md](../scripts/README.md)。

## 2. 设计意图

- 安装脚本与 payload 随安装器嵌入，版本一致；安装期需联网获取 Python 工具链、Runner 依赖与 OfficeCLI（获取与回退行为见 §4），不能把资源自包含等同于完全离线安装。payload 中的 runner wheel 由构建链强制与发布版本一致（见 [scripts/README.md](../scripts/README.md)），安装器只负责按包内内容覆盖本机，不做版本猜测。
- Tauri 负责编排，安装脚本只执行阶段任务；二者同目录维护、随版本共同演进，仓库级构建归 scripts。
- 使用 uv 托管 Python 与独立 venv，避免系统 Python 版本差异扩大兼容矩阵。
- Skills 按完整文件释放，不解析 frontmatter；客户端与 Runner 分别过滤和翻译，两套平台翻译表语义必须对齐，新增平台同时修改两端。`$SPIRITAGENT_HOME/.no-bundled-skills` 标记可让 install-skills 阶段跳过内置技能释放。

## 3. 架构地图

- `src/` 前端 → `src-tauri/` 安装编排 → `install.ps1` / `install.sh` 安装执行；脚本是独立工作进程，payload 各目录与桌面格式由编排层经 `SPIRITAGENT_BUNDLED_*` 等环境变量传入。
- 安装脚本按 dev checkout（`SPIRITAGENT_SETUP_DEV_REPO_ROOT`）→ Tauri `bundle.resources` → build.rs 嵌入 payload zip（解压至 `$SPIRITAGENT_HOME/bootstrap-payload/`）三级解析，见 [install_script.rs](src-tauri/src/install_script.rs)；安装器不自联网获取脚本，脚本版本即安装器构建版本。Windows 发布产物为单 exe，依赖嵌入兜底。
- `skills/` 是原始技能 payload，`payload/` 是构建暂存区，包含 Runner wheel、客户端产物、onboarding 引导音频与安装脚本；嵌入与产物清单见 [scripts/README.md](../scripts/README.md)。
- 运行时布局：Runner venv 位于 `$SPIRITAGENT_HOME/runner/.venv`，onboarding 音频按语言释放至 `$SPIRITAGENT_HOME/audio/onboarding/<lang>/`，安装日志写入 `$SPIRITAGENT_HOME/logs/bootstrap-installer.log`。`$SPIRITAGENT_HOME` 缺省为 Windows `%LOCALAPPDATA%\SpiritAgent`、macOS `~/Library/Application Support/SpiritAgent`，解析入口见 [paths.rs](src-tauri/src/paths.rs)，须与安装脚本及 Runner 的解析保持一致。
- Windows 开发兜底 `install.cmd` 不随生产资源分发。

## 4. 关键设计决策

- 六阶段协议：welcome → install-python → unpack-runner → unpack-desktop → install-skills → finalize，最后写完成标记；分阶段提供进度与重试边界，Runner 配置由客户端连接后推送。每个阶段是独立进程，uv 路径、Python 版本等脚本内状态不跨阶段保留，须在阶段内重新推导。
- 脚本结果用带哨兵前缀的单行 NDJSON，从 uv / pip / robocopy 混合 stdout 中提取，避免普通日志被误解析为阶段结果或 manifest。
- 用户取消即时中止当前阶段的脚本子进程并整体置败；完成标记只在 finalize 写入，重装时以 `uv venv --clear` 重建 venv，是修复陈旧 / 损坏环境的路径。
- `install.ps1` 必须保存为带 BOM 的 UTF-8：PowerShell 5.1 将无 BOM 脚本按系统 ANSI 代码页解码，中文注释在 GBK 等双字节代码页下会打乱令牌解析（UnexpectedToken），首个 `-Manifest` 调用即失败；构建链按字节复制该文件入产物，编码以源文件为准。
- 安装器完成后自拷贝至 `$SPIRITAGENT_HOME/spiritagent-setup(.exe)`，为快捷方式提供稳定目标；macOS 对副本先清 quarantine 再按签名状态处理：未签名补 ad-hoc，损坏 ad-hoc 可重签，权威证书签名须严格校验，禁止静默降级为 ad-hoc。
- 安装期联网获取：uv 与 Python 工具链、Runner wheel 依赖、OfficeCLI。Runner 依赖安装失败后镜像重试：优先 `SPIRITAGENT_PYPI_INDEX_URL` / `PIP_INDEX_URL`，缺省使用阿里云镜像，环境变量覆盖支持企业私有 index；uv、Python 与 OfficeCLI 的下载无镜像回退，OfficeCLI 安装尽力而为、失败不阻断。
- macOS `/Applications/SpiritAgent.app` 同时是首装入口和后续 launcher；fast path 除完成标记外还检查桌面端二进制存在与 Runner venv 可导入核心依赖，venv 健康判定须与客户端 [updater.ts](../client/main/runner/updater.ts) 的探针保持一致，改动两端同步；`--reinstall` / `--repair` 强制跳过 fast path。
- 卸载由客户端 NSIS 安装包的系统卸载入口承载，仅移除应用本体；本模块不注册卸载流程，`$SPIRITAGENT_HOME` 数据不随卸载清理。
- ZIP 安装查找客户端时允许 `$SPIRITAGENT_HOME/apps/SpiritAgent/SpiritAgent.exe`，因为该布局不在常规安装位置。

### 内置技能文档

`skills/` 随安装包交付给运行时 Agent，不是维护本仓库的指令。名称与描述用于发现技能，正文按需读取；描述应简短写明实际任务，不能仅因工具可用或出现宽泛关键词就要求加载。多个技能覆盖同类任务时写明选择依据，尊重用户指定的工具。

多工作流技能的入口保留选择条件、必要约束与导航，长篇模式说明和示例放支持文件。保留平台、权限、数据与工具限制；通用计划、固定修改次数和重复自检不设为强制流程。维护时保留平台字段、作者与许可证，核对描述和正文的一致性、支持文件引用及客户端 / Runner 的读取兼容性；说明文字通过静态检查不代表模型行为已经实测。

## 5. 与外部的契约

- Tauri ↔ 安装脚本：资源与运行时布局见 §3，阶段及结果帧见 §4。
- Tauri ↔ 前端：`bootstrap` IPC 通道推送 manifest / stage / log / complete / failed 事件帧，定义见 [events.rs](src-tauri/src/events.rs)，前端消费见 [store.ts](src/store.ts)。
- 对客户端 / Runner：运行时安装位置见 §3，Skills 平台翻译约束见 §2，venv 健康探针两端同步见 §4；构建 payload 清单见 [scripts README](../scripts/README.md)。
- 更新与签名边界见 [PROTOCOL §5.5](../docs/PROTOCOL.md#55-自更新签名client--backend--installer--backend)。
