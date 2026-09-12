# Installer

## 1. 职责与边界

负责安装、修复、卸载与运行时释放；认证交互由客户端承载，更新协议见 [PROTOCOL.md §5.5](../docs/PROTOCOL.md)。安装 UI 与资源独立维护，不借用其他模块的样式或组件；产物由构建阶段显式输入，构建入口见 [scripts/README.md](../scripts/README.md)。

## 2. 设计意图

- 安装脚本与 payload 随安装器嵌入，版本一致；Python 与依赖引导的下载行为见 §4，不能把资源自包含等同于完全离线安装。payload 中的 runner wheel 由构建链强制与发布版本一致（见 [scripts/README.md](../scripts/README.md)），安装器只负责按包内内容覆盖本机，不做版本猜测。
- Tauri 负责编排，安装脚本只执行阶段任务；二者同目录维护、随版本共同演进，仓库级构建归 scripts。
- 使用 uv 托管 Python 与独立 venv，避免系统 Python 版本差异扩大兼容矩阵。
- Skills 按完整文件释放，不解析 frontmatter；客户端与 Runner 分别过滤和翻译，两套平台翻译表语义必须对齐，新增平台同时修改两端。

## 3. 架构地图

- `src/` 前端 → `src-tauri/` 安装编排 → `install.ps1` / `install.sh` 安装执行；脚本是独立工作进程，资源解压根由编排层传入。
- `skills/` 是原始技能 payload，`payload/` 是构建暂存区，包含 Runner wheel、客户端产物与安装脚本；嵌入与产物清单见 [scripts/README.md](../scripts/README.md)。
- 安装运行时位于 `$SPIRITAGENT_HOME/runner/.venv`；Windows 开发兜底 `install.cmd` 不随生产资源分发。

## 4. 关键设计决策

- 六阶段协议：welcome → install-python → unpack-runner → unpack-desktop → install-skills → finalize，最后写完成标记；分阶段提供进度与重试边界，Runner 配置由客户端连接后推送。
- 脚本结果用带哨兵前缀的单行 NDJSON，从 uv / pip / robocopy 混合 stdout 中提取，避免普通日志被误解析为阶段结果或 manifest。
- macOS 自拷贝签名：未签名时补 ad-hoc，损坏 ad-hoc 可重签，权威证书签名须严格校验，禁止静默降级为 ad-hoc。
- 依赖安装失败后镜像重试：优先 `SPIRITAGENT_PYPI_INDEX_URL` / `PIP_INDEX_URL`，缺省使用阿里云镜像；环境变量覆盖支持企业私有 index。
- macOS `/Applications/SpiritAgent.app` 同时是首装入口和后续 launcher；fast path 除完成标记外还检查 Runner 核心依赖可导入，损坏回完整修复，`--reinstall` / `--repair` 强制跳过 fast path。
- 卸载归 Installer，集中处理安装目录、注册表、计划任务等 OS 变更；客户端不自行卸载。
- ZIP 安装查找客户端时允许 `$SPIRITAGENT_HOME/apps/SpiritAgent/SpiritAgent.exe`，因为该布局不在常规安装位置。

## 5. 与外部的契约

- Tauri ↔ 安装脚本：资源与运行时布局见 §3，阶段及结果帧见 §4。
- 对客户端 / Runner：运行时安装位置见 §3，Skills 平台翻译约束见 §2；构建 payload 清单见 [scripts README](../scripts/README.md)。
- 更新与签名边界见 [PROTOCOL §5.5](../docs/PROTOCOL.md)。

## 6. 已知限制

- 默认镜像可能不符合企业网络策略，部署方须通过 §4 的环境变量指定私有 index。
