# scripts/

仓库级构建、发布、静态检查与调试入口。安装阶段的执行脚本由 [Installer](../installer/README.md)维护，与 Tauri 编排和 payload 一起分发。

本文命令默认在仓库根目录执行。按任务进入：构建与发布读 §1–§3，Python 导入与后端分层读 §4–§5，引导音频读 §6，提示词排查读 §7，选择验证范围读 §8，共享图标读 §9。

## 1. 构建安装器 — `build.py`

单一入口，端到端编排 **runner (uv build wheel) → client (electron-builder) → stage → Tauri (installer)**。跨平台的纯文本编辑、版本同步与 payload 暂存由 `scripts/lib/build_helpers.py` 统一定义；Windows 构建在此之上追加产出 `release/SpiritAgent-{ver}-update.zip` 自更新产物（zip 组装与签名逻辑在 `scripts/lib/UpdateManifest.ps1`，build.py 以 PowerShell dot-source 调用）。

```bash
uv run python scripts/build.py --version 0.16.0
```

- 写版本号到 `client/package.json`、`installer/package.json`、`installer/src-tauri/tauri.conf.json`、`installer/src-tauri/Cargo.toml`、`runner/pyproject.toml`
- Staging 到 `installer/payload/`：copy 与 `runner/pyproject.toml` 版本精确匹配的 wheel（`dist/` 里的历史 wheel 一律不参与打包，缺对应版本直接失败）与 `server.py`，symlink/junction skills 与 install 脚本，desktop 产物拷入 `payload/client/`
- staging 后对「将进入安装包的 wheel + server.py」再跑一次 `scripts/check_runner_facade.py`；wheel 与 `server.py` 导入面不一致则中止构建，避免发布「新 server + 旧 utils」一类错位包
- macOS code-sign + notarize（`--sign-identity` / `--notary-profile`）；Windows signtool（`--cert-thumbprint`）。证书参数缺省时产出未签名安装器
- Tauri 2 对 `bundle.resources` 缺失文件**报错**，而 client 产物文件名含版本号、无法静态写进配置；构建脚本在 tauri build 之前临时把当前 host 的实际 client artifact 追加进 `bundle.resources`，build 后 restore（git 状态保持干净）
- **单机构建受 host/target 约束** —— macOS 产物只能出自 macOS host，Windows 产物只能出自 Windows host；脚本校验不匹配即失败。跨平台产物由 §2 的 CI 矩阵在两个 runner 上分别产出

```bash
# 单独跑 runner 导入面门禁（默认取 runner/dist 下最新 wheel）
uv run --no-project --python 3.13 python scripts/check_runner_facade.py
```

**Windows 最终产物是单个 `SpiritAgent-Setup-{ver}.exe`**（不是 NSIS wrapper）。NSIS wrapper 会出现"双安装器"问题——NSIS 把 `SpiritAgent-Setup.exe` 装到 Program Files，用户还得再手动跑一次。直接发 `SpiritAgent-Setup.exe` 用户双击即看到 Tauri 安装 UI。Windows 脚本用 `tauri build --no-bundle` 跳过 NSIS，产物直接拷贝到 `release/SpiritAgent-Setup-{ver}.exe`。

**后端（Docker）不参与** —— 有独立的 docker-compose 部署流。

## 2. 发布 — tag 触发 GitHub Actions

发布以 **git tag 为唯一版本来源**：推送 `v<semver>` tag 触发 [.github/workflows/release.yml](../.github/workflows/release.yml)，`windows-latest` 与 `macos-latest` 两个 job 各跑一次 `build.py`（版本号从 tag 提取，经 `set_version` 写入各清单文件，不再手工维护），完成后汇总为 **draft release**——挂上双平台安装器、Windows update.zip 与 LLM 生成的 notes；在 Releases 页检查后 Publish。若 tag 已存在 release（如在 Releases 页手动创建），CI 只补挂产物与 notes，不改动其发布状态。

仓库 secrets：`MINIMAX_API_KEY`（notes 生成，缺失时 CI 回退为提交列表）；`SPIRITAGENT_UPDATE_SIGNING_KEY`（PEM **内容**，Windows job 落地为临时文件供 `Resolve-UpdateSigningKey` 读取，缺失则 update zip 签名失败、构建中止）。macOS 证书未配置期间 CI 产出未签名 dmg，用户首次打开需右键 → 打开。

## 3. Release notes — `gen_release_notes.py`

依据两个 tag 之间的提交生成中文 release notes：优先调 MiniMax（OpenAI Responses 契约，base URL / 模型与 backend `MiniMaxChatProvider` 一致）；未配置 `MINIMAX_API_KEY` 或调用失败时，回退为按 conventional commit 类型分组的提交列表，发布流程不中断。CI 的 notes job 调用它；本地预览：

```bash
python scripts/gen_release_notes.py v1.3.0                                  # 自动定位上一个 tag
MINIMAX_API_KEY=... python scripts/gen_release_notes.py v1.3.0 --output notes.md
python scripts/gen_release_notes.py v1.3.0 --from-tag v1.2.0 --output notes.md
```

## 4. Import 检查 — `check_imports.py`

backend + runner 的 static import-shape 检查器，防「名字到运行时才炸」与 facade 被过度精简一类回归。被 `.pre-commit-config.yaml` 注册为本地 hook 并以 `--strict-imports` 启动（违规即失败）；直接运行且不带该参数时仅打印诊断、exit 0。覆盖 3 类违规：

- 禁止 future annotations：Python 3.13 原生支持 PEP 585/604，禁止 `from __future__ import annotations`；`--fix` 可自动清理
- `TYPE_CHECKING` 名字泄漏：仅在 `if TYPE_CHECKING:` 内 import、却被类体注解等运行时求值路径引用的名字
- Facade 一致性：经绝对或相对包级导入 `from <pkg> import X` 引入的名字，必须在目标包 `__init__.py` 的 re-export 集合（`__all__` 与模块级 `from .x import y` 的并集）内

```bash
uv run --no-project --python 3.13 python scripts/check_imports.py --strict-imports   # 全量扫描 backend + runner
uv run --no-project --python 3.13 python scripts/check_imports.py --fix              # 自动清理 future annotations
```

## 5. Backend 分层架构检查 — `check_services_architecture.py`

backend services 的分层检查器，使用 Python 3.13 标准库。依赖约束的理由见 [Backend §3.2](../backend/README.md#32-services-五层)，修改导入与包边界后运行：

```bash
uv run --no-project --python 3.13 python scripts/check_services_architecture.py
```

覆盖 5 类违规：站内导入不可解析（含相对导入的语义化解析）；包级依赖环；层间白名单（contracts 纯净、application 不导入 adapters、infrastructure 不认识业务、bootstrap 不被反向导入、main 只导入 bootstrap、common / components / modules 不得反向导入服务实现）；domains 跨业务域隔离（各域可单向导入会话底座 conversation，companion / journal 对 memory 的已登记例外）；application 内未声明的流程依赖边。例外清单以脚本内 `UPWARD_ALLOWED` / `APPLICATION_FLOW_EDGES` / `DOMAIN_FLOW_EDGES`（含 `DOMAIN_BASE`）为权威，调整允许依赖时同步 [Backend §3.2](../backend/README.md#32-services-五层)中的理由；本文不另列允许边的完整副本。

## 6. Onboarding 引导词音频生成与校验 — `onboarding-audio/`

包含预渲染引导词音频元信息 `manifest.json` 与合成/校验脚本 `generate_onboarding_audio.py`。详见 [scripts/onboarding-audio/README.md](onboarding-audio/README.md)。

## 7. 提示词调试与检查 — `debug_prompt.py`

用于呈现聊天预设的基础系统提示词、用户画像、工具集与输入项，不执行完整回合。生活空间按音色追加的语音协议见[对话编排](../backend/services/application/chat/README.md#提示词与运行时数据)，不包含在本脚本输出中；核对实际发送负载使用 [LLM 调试日志](../backend/README.md#llm-调试日志)。桌面视觉动作使用独立推理提示词。

```bash
# 查看默认完成 onboarding 后的完整提示词与请求分段
uv run --project backend python scripts/debug_prompt.py

# 自定义角色、用户与消息，并以原始 JSON 输出
uv run --project backend python scripts/debug_prompt.py -m "你好呀！" --persona-name "星奈" --json

# 连接数据库读取所选预设的记忆（人设仅用于陪伴）
uv run --project backend python scripts/debug_prompt.py --db --user-id 1 --preset companion
```

## 8. 按改动选择验证

验证深度按 [RULES](../RULES.md#原则四目标驱动执行解决问题--完成任务)确定，以下命令各自只覆盖对应环节：

| 改动 | 入口与覆盖范围 |
|---|---|
| 文档 | 核对来源、相对路径、锚点和文字章节号；`git diff --check` 检查 diff 中的空白错误，不验证内容或链接 |
| Python 实现 / 导入 | 提交前 hook 检查 Ruff、语法和导入形状；独立导入检查见 §4，后端依赖变化另跑 §5 |
| 客户端实现与模块边界 | `pnpm --dir client lint`、`pnpm --dir client typecheck`；前者检查依赖规则，后者覆盖渲染与主进程配置 |
| 客户端构建入口、preload 或资源 | `pnpm --dir client build` 并核对实际产物；构建会写本机构建信息和输出目录 |
| Runner wheel / 发布载荷 | §1 的 wheel 导入面门禁与对应平台构建；默认检查最新 wheel，正式构建检查待发布的精确版本 |
| 引导预制音频 | §6 的静态清单检查；合成与试听按专项 README 执行 |
| 提交前 | `uv tool run --python 3.13 pre-commit run -a`；hook 可自动修正文件，若有修改需检查 diff 后重跑至通过 |

客户端命令需先按[源码启动说明](../README.md#从源码启动)安装依赖。Python 静态检查使用 3.13，提示词与音频脚本经 `uv run --project backend` 使用后端依赖；读取数据库的调试命令还需可用的后端配置。

文档局部修改可先通过 pre-commit 的 `--files` 参数检查所改文件，提交前仍执行上表全量入口。实际运行了哪些检查就报告哪些结果；静态、构建、真实供应商、平台安装与桌面体验验证分别说明。

## 9. 共享图标资源

[client/assets/icon.png](../client/assets/icon.png)是应用图标母图，保留真实透明通道。替换时同步导出同目录的多尺寸 ICO、ICNS 与 [Installer 图标](../installer/src-tauri/icons/)，导出不得铺底色；否则安装后可能仍显示旧图标。产物引用分别见 [client/package.json](../client/package.json)和 [tauri.conf.json](../installer/src-tauri/tauri.conf.json)，核对桌面程序与安装器各自实际使用的资源。
