# scripts/

两类制品，刻意保持薄。**Install 脚本不在此处** —— 它们与 installer 同目录（[installer/install.{sh,ps1,cmd}](../installer/README.md)），让 Tauri 程序与其 worker 脚本作为一个自洽单元分发。

## 1. 构建安装器 — `build.py` / `build_client.{sh,ps1}`

单一入口，端到端编排 **runner (uv build wheel) → client (electron-builder) → stage → Tauri (installer)**。跨平台的纯文本编辑、版本同步与 payload 暂存由 `scripts/lib/build_helpers.py` 统一定义，`build_client.sh`（macOS）与 `build_client.ps1`（Windows）作为系统原生包装层调用共享逻辑。Windows 构建额外产出 `release/SpiritAgent-{ver}-update.zip` 自更新产物。

```bash
uv run python scripts/build.py --version 0.16.0
scripts/build_client.sh --version 0.16.0 --target mac
pwsh scripts/build_client.ps1 -Version 0.16.0
```

- 写版本号到 `client/package.json`、`installer/package.json`、`installer/src-tauri/tauri.conf.json`、`installer/src-tauri/Cargo.toml`、`runner/pyproject.toml`
- Staging 到 `installer/payload/`（symlink/junction skills 与 install 脚本，copy config + runner wheel + client artifact）
- macOS code-sign + notarize（`--sign-identity` / `--notary-profile`）；Windows signtool（`-CertThumbprint`）
- Tauri 2 默认对 `bundle.resources` 缺失文件**报错**；构建脚本在 tauri build 之前临时 patch `tauri.conf.json` 的 `bundle.resources` 列表，把占位文件替换为当前 host 的实际 client artifact，build 后 restore（git 状态保持干净）
- **跨平台 build 不可行** —— macOS code-sign 必须 mac host，Windows 必须 win host。脚本校验 `host/target` 匹配

**Windows 最终产物是单个 `SpiritAgent-Setup-{ver}.exe`**（不是 NSIS wrapper）。NSIS wrapper 会出现"双安装器"问题——NSIS 把 `SpiritAgent-Setup.exe` 装到 Program Files，用户还得再手动跑一次。直接发 `SpiritAgent-Setup.exe` 用户双击即看到 Tauri 安装 UI。Windows 脚本用 `tauri build --no-bundle` 跳过 NSIS，产物直接拷贝到 `release/SpiritAgent-Setup-{ver}.exe`。

**后端（Docker）不参与** —— 有独立的 docker-compose 部署流。

## 2. Import 检查 — `check_imports.py`

backend + runner 的 static import-shape 检查器（被 `.pre-commit-config.yaml` 注册为本地 hook 并以 `--strict-imports` 启动），防 c66ab1a 一类回归。覆盖 4 类违规：

- 禁止 future annotations：Python 3.13 原生支持 PEP 585/604，禁止 `from __future__ import annotations`
- `TYPE_CHECKING` 名字泄漏：仅在 `if TYPE_CHECKING:` 内 import、却被类体注解等运行时求值路径引用的名字
- runner 工具子包之间的 sibling 跨子包 eager import（终端 ↔ 文件、代码执行 → 线程上下文这类循环）
- Facade 一致性：`from <local_pkg> import X` 走的 `<local_pkg>` 必须在其 `__init__.py` 里 re-export `X`，防止 facade 被过度精简

## 3. Backend 分层架构检查 — `check_services_architecture.py`

backend `services/` 的分层守护（可纳入 pre-commit / 发布前检查）：

```bash
backend/.venv/Scripts/python.exe scripts/check_services_architecture.py
```

覆盖 5 类违规：站内导入不可解析（含相对导入的语义化解析）；包级依赖环；层间白名单（contracts 纯净、application 不导入 adapters、infrastructure 不认识业务、bootstrap 不被反向导入、main 只导入 bootstrap）；domains 跨业务域隔离（含已登记的会话底座与时区只读例外）；application 内未声明的流程依赖边。例外清单以脚本内 `UPWARD_ALLOWED` / `APPLICATION_FLOW_EDGES` 为权威，调整时同步 backend/README.md §3。

## 4. Onboarding 引导词音频生成与校验 — `onboarding-audio/`

包含预渲染引导词音频元信息 `manifest.json` 与合成/校验脚本 `generate_onboarding_audio.py`。详见 [scripts/onboarding-audio/README.md](onboarding-audio/README.md)。

## 5. 提示词调试与检查 — `debug_prompt.py`

用于呈现与调试伙伴完成 onboarding 引导流程以及用户发出消息时实际装配的完整聊天提示词（系统提示词、用户画像、工具集与请求负载）。桌面视觉动作使用独立推理提示词，不属于本脚本的聊天请求负载。

```bash
# 查看默认完成 onboarding 后的完整提示词与请求分段
uv run --project backend python scripts/debug_prompt.py

# 自定义角色、用户与消息，并以原始 JSON 输出
uv run --project backend python scripts/debug_prompt.py -m "你好呀！" --persona-name "星奈" --json

# 连接数据库读取所选预设的记忆（人设仅用于陪伴）
uv run --project backend python scripts/debug_prompt.py --db --user-id 1 --preset companion
```
