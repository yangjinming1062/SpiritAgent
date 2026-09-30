# 仓库构建与验证

命令默认在仓库根目录执行。首次安装阶段脚本归 [Installer](../installer/README.md)，协作与验证要求归 [RULES](../RULES.md#代码验证规范)。

## 按改动选择验证

先按[源码启动说明](../README.md#从源码启动)准备依赖，再选择相关检查：

| 改动 | 检查 |
|---|---|
| 文档 | 事实、相对路径、锚点、图表分支与渲染；`git diff --check` 只检查空白 |
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

## 导入检查

检查 Backend / Runner 的 future annotations、TYPE_CHECKING 名字泄漏和包级 facade。项目使用 Python 3.13，具体规则以脚本为准。

```bash
uv run --no-project --python 3.13 python scripts/check_imports.py --strict-imports
uv run --no-project --python 3.13 python scripts/check_imports.py --fix
```

不带 `--strict-imports` 只输出诊断且退出码为 0；`--fix` 修改文件，不能作为完整门禁。检查后查看差异并重跑严格模式。

## Backend 分层检查

```bash
uv run --no-project --python 3.13 python scripts/check_services_architecture.py
```

覆盖导入解析、包级环、层间约束、跨域和应用流程依赖。允许边以脚本中的声明为准，调整时同步 [Backend](../backend/README.md#services-依赖边界) 的理由，不能通过放宽白名单掩盖不合理依赖。

## 提示词调试

```bash
uv run --project backend python scripts/debug_prompt.py
uv run --project backend python scripts/debug_prompt.py -m "你好" --persona-name "星奈" --json
uv run --project backend python scripts/debug_prompt.py --db --user-id 1 --preset companion
```

展示基础预设、画像、工具与输入，不执行完整回合，也不包含按音色动态追加的全部语音协议。数据库模式需要有效配置；真实发送内容通过 [Backend 调试日志](../backend/README.md#llm-调试日志)核对。

## 引导音频

预制音频的文案、tag、生成和静态校验见 [专项说明](onboarding-audio/README.md)。合成会调用供应商并覆盖产物；`--check` 不合成，也不能代替试听。

## 构建安装器

构建入口 [build.py](build.py)依次构建 Runner wheel、Client、暂存 payload 和 Tauri Installer；正式暂存执行精确版本与导入面门禁。`--target` 指定 mac / win（默认按宿主推断），`--skip-runner` / `--skip-desktop` 跳过对应构建但仍要求已有同版本产物，`--output` 指定输出目录。共享步骤在 [build_helpers.py](lib/build_helpers.py)，其中 `set_version` 把版本写入 Client、Installer（含 Tauri 配置与 Cargo.toml）和 Runner 的清单。

Backend Docker 独立部署。`build.py` 执行会同步修改版本清单，下面版本号仅为示例：

```bash
uv run python scripts/build.py --version 0.16.0
```

暂存只接受与目标版本精确匹配的 wheel，并对实际入包的 wheel 与 `server.py` 执行导入面检查，不能混入历史 wheel。单独诊断可运行：

```bash
uv run --no-project --python 3.13 python scripts/check_runner_facade.py
```

单独检查默认选最新 wheel，不等同于正式构建的精确版本门禁。构建期间临时加入实际桌面资源，完成后恢复 Tauri 配置。

Windows 产出单个 `SpiritAgent-Setup-<version>.exe`（内嵌桌面端 NSIS 包，由 `install.ps1` 静默安装）和 update ZIP；update ZIP 由 [UpdateManifest.ps1](lib/UpdateManifest.ps1) 的 `Build-UpdateZip` 打包（需要 openssl），包含当前版本 NSIS 包（内置技能在其 resources 内，不另行打包）与 blockmap、`runner/` 下的 wheel 与 `server.py`、签名的 `latest-runner.yml`、`manifest.json`，以及缺签名时补签的 `latest*.yml`（Backend 上传时丢弃后者，按库存重新生成），仅 Windows 构建生成。macOS 产出 DMG。macOS 产物在 macOS 构建，Windows 在 Windows 构建，不支持跨宿主替代验证。`--sign-identity`（配合 `--notary-profile` 公证）与 `--cert-thumbprint` 就地签名 `client/release` 中的桌面端产物，再复制进 payload，Windows update ZIP 也取该文件；安装器本身不由 `build.py` 签名；electron-builder 另有经环境变量驱动的 [notarize.cjs](../client/scripts/notarize.cjs)。Windows 更新包始终要求更新签名密钥。

## 发布

推送 `vX.Y.Z`（仅三段数字）tag 触发 [release.yml](../.github/workflows/release.yml)，tag 是发布版本来源；其他 `v*` tag 也会触发，但构建的版本校验会失败，不生成 release。双平台分别构建，汇总安装器、更新包和 notes 为草稿 release，核对后发布；已存在 release 时仅补产物和 notes，不改变发布状态。

客户端自动更新经 Backend 分发：在管理端“版本管理”上传 update ZIP 后，客户端从当前后端获取更新清单，见 [update.py](../backend/api/v1/update.py) 与[自更新签名](../docs/PROTOCOL.md#自更新签名)。

`SPIRITAGENT_UPDATE_SIGNING_KEY` 缺失会使更新包签名失败并中止构建，配置方式见 [密钥说明](release-keys/README.md)。notes 的 `MINIMAX_API_KEY` 缺失可降级，不是构建硬门槛。`release.yml` 不传平台签名参数，CI 产物未经平台证书签名，不能宣称完成公证。

## Release notes

根据 tag 间提交生成中文说明，模型不可用时回退为分组提交列表，不阻断发布。以下 tag 为示例，须使用实际存在的版本：

```bash
python scripts/gen_release_notes.py v1.3.0
python scripts/gen_release_notes.py v1.3.0 --from-tag v1.2.0 --output notes.md
```

需要模型生成时通过环境提供 `MINIMAX_API_KEY`，不把真实密钥写入命令文档或提交。

## 共享图标资源

[client/assets/icon.png](../client/assets/icon.png)为母图，保留真实 alpha。修改时同步 ICO、ICNS 和 [Installer 图标](../installer/src-tauri/icons/)，不铺底色；检查两端打包配置和最终产物，不能仅确认源 PNG 已替换。
