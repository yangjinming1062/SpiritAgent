# scripts/

两类制品，刻意保持薄。**Install 脚本不在此处** —— 它们与 installer 同目录（[installer/install.{sh,ps1,cmd}](../installer/README.md)），让 Tauri 程序与其 worker 脚本作为一个自洽单元分发。

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
python scripts/check_runner_facade.py
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
python scripts/check_imports.py --strict-imports   # 全量扫描 backend + runner
python scripts/check_imports.py --fix              # 自动清理 future annotations
```

## 5. Backend 分层架构检查 — `check_services_architecture.py`

backend `services/` 的分层守护（纯标准库实现）。backend/README §3.2 把环检测、层白名单与域隔离委托给它，修改 backend 依赖关系后运行：

```bash
python scripts/check_services_architecture.py
```

覆盖 5 类违规：站内导入不可解析（含相对导入的语义化解析）；包级依赖环；层间白名单（contracts 纯净、application 不导入 adapters、infrastructure 不认识业务、bootstrap 不被反向导入、main 只导入 bootstrap、common / components / modules 不得反向导入服务实现）；domains 跨业务域隔离（各域可单向导入会话底座 conversation，companion / journal 对 memory 的已登记例外）；application 内未声明的流程依赖边。例外清单以脚本内 `UPWARD_ALLOWED` / `APPLICATION_FLOW_EDGES` / `DOMAIN_FLOW_EDGES`（含 `DOMAIN_BASE`）为权威，调整时同步 backend/README.md §3。

## 6. Onboarding 引导词音频生成与校验 — `onboarding-audio/`

包含预渲染引导词音频元信息 `manifest.json` 与合成/校验脚本 `generate_onboarding_audio.py`。详见 [scripts/onboarding-audio/README.md](onboarding-audio/README.md)。

## 7. 提示词调试与检查 — `debug_prompt.py`

用于呈现聊天预设的基础系统提示词、用户画像、工具集与输入项，不执行完整回合。生活空间按音色追加的语音协议见[陪伴提示词编排](../backend/README.md#对话上下文与记忆)，不包含在本脚本输出中；核对实际发送负载使用 [LLM 调试日志](../backend/README.md#llm-调试日志)。桌面视觉动作使用独立推理提示词。

```bash
# 查看默认完成 onboarding 后的完整提示词与请求分段
uv run --project backend python scripts/debug_prompt.py

# 自定义角色、用户与消息，并以原始 JSON 输出
uv run --project backend python scripts/debug_prompt.py -m "你好呀！" --persona-name "星奈" --json

# 连接数据库读取所选预设的记忆（人设仅用于陪伴）
uv run --project backend python scripts/debug_prompt.py --db --user-id 1 --preset companion
```
