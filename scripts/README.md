# scripts/

仓库级构建、发布、检查与调试入口。命令默认在仓库根目录执行；安装阶段脚本归 [Installer](../installer/README.md)，协作与验证要求归 [RULES](../RULES.md#代码验证规范)。

## 1. 构建安装器 — `build.py`

构建链为 Runner wheel → Client → payload 暂存 → Tauri Installer，Backend Docker 独立部署。以下版本号为示例，执行会同步修改版本清单：

```bash
uv run python scripts/build.py --version 0.16.0
```

暂存只接受与目标版本精确匹配的 wheel，并对实际入包的 wheel 与 `server.py` 执行导入面检查，不能混入历史 wheel。单独诊断可运行：

```bash
uv run --no-project --python 3.13 python scripts/check_runner_facade.py
```

单独检查默认选最新 wheel，不等同于正式构建的精确版本门禁。构建期间临时加入实际桌面资源，完成后恢复 Tauri 配置。

Windows 产出单个 setup EXE 和 update ZIP，不再套一层安装器；macOS 产物在 macOS 构建，Windows 在 Windows 构建，不支持跨宿主替代验证。签名参数见脚本，未提供平台证书时安装器可未签名，但 Windows 更新包仍要求更新签名密钥。

## 2. 发布 — tag 触发 GitHub Actions

推送 `v<semver>` tag 触发 [release.yml](../.github/workflows/release.yml)，tag 是发布版本来源。双平台分别构建，汇总安装器、更新包和 notes 为草稿 release，核对后发布；已存在 release 时仅补产物和 notes，不改变发布状态。

`SPIRITAGENT_UPDATE_SIGNING_KEY` 缺失会使更新包签名失败并中止构建，配置方式见 [密钥说明](release-keys/README.md)。notes 的 `MINIMAX_API_KEY` 缺失可降级，不是构建硬门槛。macOS 证书未配置时产物未签名，不能宣称完成公证。

## 3. Release notes — `gen_release_notes.py`

根据 tag 间提交生成中文说明，模型不可用时回退为分组提交列表，不阻断发布。以下 tag 为示例，须使用实际存在的版本：

```bash
python scripts/gen_release_notes.py v1.3.0
python scripts/gen_release_notes.py v1.3.0 --from-tag v1.2.0 --output notes.md
```

需要模型生成时通过环境提供 `MINIMAX_API_KEY`，不把真实密钥写入命令文档或提交。

## 4. Import 检查 — `check_imports.py`

检查 Backend / Runner 的 future annotations、TYPE_CHECKING 名字泄漏和包级 facade。项目使用 Python 3.13，具体规则以脚本为准。

```bash
uv run --no-project --python 3.13 python scripts/check_imports.py --strict-imports
uv run --no-project --python 3.13 python scripts/check_imports.py --fix
```

不带 `--strict-imports` 只输出诊断且退出码为 0；`--fix` 修改文件，不能作为完整门禁。检查后查看差异并重跑严格模式。

## 5. Backend 分层架构检查 — `check_services_architecture.py`

```bash
uv run --no-project --python 3.13 python scripts/check_services_architecture.py
```

覆盖导入解析、包级环、层间约束、跨域和应用流程依赖。允许边以脚本中的声明为准，调整时同步 [Backend](../backend/README.md#32-services-五层) 的理由，不能通过放宽白名单掩盖不合理依赖。

## 6. Onboarding 引导词音频生成与校验 — `onboarding-audio/`

预制音频的文案、tag、生成和静态校验见 [专项说明](onboarding-audio/README.md)。合成会调用供应商并覆盖产物；`--check` 不合成，也不能代替试听。

## 7. 提示词调试与检查 — `debug_prompt.py`

```bash
uv run --project backend python scripts/debug_prompt.py
uv run --project backend python scripts/debug_prompt.py -m "你好" --persona-name "星奈" --json
uv run --project backend python scripts/debug_prompt.py --db --user-id 1 --preset companion
```

展示基础预设、画像、工具与输入，不执行完整回合，也不包含按音色动态追加的全部语音协议。数据库模式需要有效配置；真实发送内容通过 [Backend 调试日志](../backend/README.md#llm-调试日志)核对。

## 8. 按改动选择验证

先按[源码启动说明](../README.md#从源码启动)准备依赖，再选择相关检查：

| 改动 | 检查 |
|---|---|
| 文档 | 内容、相对路径、锚点与章节号；`git diff --check` 只检查空白 |
| Python | 提交前 hook、严格导入检查；Backend 依赖变化另跑分层检查 |
| Client | `pnpm --dir client lint`、`pnpm --dir client typecheck` |
| preload、构建入口或资源 | `pnpm --dir client build` 并核对实际产物 |
| wheel 或安装载荷 | 导入面门禁、精确版本与对应平台构建 |
| 音频 | 专项静态检查，必要时合成并试听 |

提交前执行：

```bash
uv tool run --python 3.13 pre-commit run -a
```

自动修正后检查差异并重跑。局部修改可先用 `--files`，不替代提交前全量入口；构建可能修改本机构建信息。只报告实际执行结果，静态、构建、真实供应商、安装和桌面验证分别表述。

## 9. 共享图标资源

[client/assets/icon.png](../client/assets/icon.png)为母图，保留真实 alpha。修改时同步 ICO、ICNS 和 [Installer 图标](../installer/src-tauri/icons/)，不铺底色；检查两端打包配置和最终产物，不能仅确认源 PNG 已替换。
