# Onboarding audio assets

预渲染的 onboarding 引导词音频。云端 TTS 一次合成、随安装包分发，client 在 onboarding 阶段只读盘播放——零网络、零本地合成。

## 文件清单

- `manifest.json` — 文案与 voice 元信息（tracked source of truth）。`generate_onboarding_audio.py` 据此生成 mp3。
- `generate_onboarding_audio.py` — 合成脚本，需要 `MIMO_API_KEY`（或 `TTS_API_KEY`）环境变量；请求体结构对齐 backend 的 MiMo TTS 运行时，保证同一音色下与 `/api/media/tts` 产出一致。
- 生成的 mp3 落到 `installer/payload/onboarding-audio/zh/<tag>.mp3`，与 manifest、脚本一并提交（`.gitignore` 对 `installer/payload/onboarding-audio/` 显式重新包含）；[tauri.conf.json](../../installer/src-tauri/tauri.conf.json) 的 `bundle.resources` 打包该目录，`scripts/build.py` 的 Tauri 阶段将其嵌入安装包。tag 与 [onboarding 引导流程](../../client/renderer/app/onboarding/onboarding-flow.tsx)里 `playOnboardingAudio(tag)` 的 `OnboardingAudioTag` 一一对应。

**tag 与文案绑定，不与题号绑定**：问题表里每题自带 `audioTag`，指向录了这句话的那条 manifest 条目。因此调整引导题序不触发任何重新合成；只有**改文案**才需要重生成对应 mp3。

## 添加 / 修改流程

1. 改 `manifest.json`（新增条目或调整文案）
2. 设 `MIMO_API_KEY`，跑 `python scripts/onboarding-audio/generate_onboarding_audio.py` 重新生成 mp3
3. 提交 `manifest.json` 与重新生成的 mp3
4. `--check` 校验 mp3 同步字节合法、目录内容与 manifest 条目一致（无外部调用，可随时本地运行）

## voice 选择

`冰糖`（mimo TTS 默认中文女声）。整段 onboarding 听感一致；voice preview sample 不在此列，仍走运行时 TTS 让用户试听不同声线。
