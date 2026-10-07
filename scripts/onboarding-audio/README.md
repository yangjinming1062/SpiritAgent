# Onboarding 引导音频

本目录维护随安装包交付的引导问题语音，产品中的使用范围见 [DESIGN](../../docs/DESIGN.md#引导与后台准备)。运行时音色试听走后端语音服务。

## 来源与交付

[manifest.json](manifest.json) 定义文案、音色和 tag，[generate_onboarding_audio.py](generate_onboarding_audio.py) 调用 MiMo TTS，默认输出到 `installer/payload/onboarding-audio/zh/`；安装器释放到 `$SPIRITAGENT_HOME/audio/onboarding/zh/`。manifest 与 MP3 一并提交。

tag 绑定文案，调整题序无需重新合成；修改文案、音色或合成行为须更新音频，改 tag 同步[引导调用方](../../client/renderer/app/onboarding/onboarding-flow.tsx)，删除 tag 后清理旧 MP3。

[主进程读取入口](../../client/main/ipc/onboarding-audio.ts) 仅使用 `zh` 目录，文件缺失时尝试仓库 payload，单文件上限 256 KiB；tag 校验与[渲染侧类型](../../client/renderer/app/onboarding/onboarding-audio.ts) 均须满足。`--output-dir` 只改脚本输出位置，manifest 的语言字段不会自动切换生成或读取目录。

## 修改与验证

在仓库根准备后端依赖，以环境变量提供 `MIMO_API_KEY` 或 `TTS_API_KEY`：

```bash
uv run --project backend python scripts/onboarding-audio/generate_onboarding_audio.py
```

默认重新合成整个 manifest 并覆盖 MP3；`--tag onboarding.q8`（可重复）限制条目。任一请求失败或响应未正常完成即报错，失败条目不写入，已写入文件保留；检查产物后用 `--tag` 重跑缺失或失败条目。静态检查：

```bash
uv run --project backend python scripts/onboarding-audio/generate_onboarding_audio.py --check
```

`--check` 不调用供应商，仍需 OpenAI SDK；仅验证 MP3 文件集合和允许的帧头，缺失、多余或 ID3v2 头会失败。它不验证内容、音色、时长或完整解码，提交前须试听受影响音频。
