# Onboarding 引导音频

维护随安装包交付的引导问题预制语音；预制语音仅用于提问（见 [DESIGN](../../docs/DESIGN.md#引导与后台准备)），运行时音色试听不属于本目录。

## 来源与交付

[manifest.json](manifest.json)定义文案、音色、语言和 tag，[脚本](generate_onboarding_audio.py)生成 MP3 到 `installer/payload/onboarding-audio/zh/`，由安装器释放到本机。manifest 与 MP3 一并提交。

tag 绑定文案而非题号；改题序不需重合成，改文案、声音或合成行为须更新音频，改 tag 同步[引导调用方](../../client/renderer/app/onboarding/onboarding-flow.tsx)。云端两次合成不保证字节相同。

客户端经主进程 [onboarding-audio.ts](../../client/main/ipc/onboarding-audio.ts) 只读取 `zh` 目录（开发时回落到仓库 payload），tag 须匹配 `onboarding.` 加小写字母、数字与点，单文件上限 256 KiB；渲染侧 tag 类型为 `onboarding.q<数字>`（[onboarding-audio.ts](../../client/renderer/app/onboarding/onboarding-audio.ts)），新 tag 须同时满足两处约束。脚本默认输出到 `zh`（`--output-dir` 可改，但客户端只读 `zh`），不按 manifest 的语言字段选目录。

## 修改与验证

在仓库根目录准备后端依赖，以环境变量提供 `MIMO_API_KEY` 或 `TTS_API_KEY` 后合成：

```bash
uv run --project backend python scripts/onboarding-audio/generate_onboarding_audio.py
```

当前命令默认重新合成整个 manifest 并覆盖 MP3；`--tag onboarding.q8`（可重复）只合成指定条目。任一条合成失败或响应未正常结束（`finish_reason` 不是 `stop`）即报错退出，该条不写入，已完成的条目保留，失败条目用 `--tag` 重跑。删 tag 后手工清理不再使用的文件。静态检查：

```bash
uv run --project backend python scripts/onboarding-audio/generate_onboarding_audio.py --check
```

`--check` 不调用供应商，但脚本加载仍需 OpenAI SDK；只核对文件名（缺失或多余均失败）与开头两字节是否为允许的帧同步头 `FF FB/FA/F3/F2`（带 ID3v2 头判失败；有缺失时只报缺失，不再报同步字），不检查正文、音色、时长或完整解码。提交前试听受影响音频，不能用静态通过代替。
