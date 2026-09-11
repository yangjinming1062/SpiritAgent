# Onboarding audio assets

预渲染的 onboarding 引导词音频。云端 TTS 一次合成、随安装包分发，client 在 onboarding 阶段只读盘播放——零网络、零本地合成。

## 文件清单

- `manifest.json` — 文案与 voice 元信息（tracked source of truth）。`generate_onboarding_audio.py` 据此生成 mp3。
- `generate_onboarding_audio.py` — 一次性合成脚本。需要 `MIMO_API_KEY`（或 `TTS_API_KEY`）环境变量。
- 生成的 mp3 落到 `installer/payload/onboarding-audio/zh/<tag>.mp3`（gitignored，Tauri 构建产物）。tag 与 `client/renderer/companion/onboarding/onboarding-flow.tsx` 里 `playOnboardingAudio(tag)` 调用的字符串一一对应。

**tag 与文案绑定，不与题号绑定**：问题表里每题自带 `audioTag`，指向录了这句话的那条 manifest 条目。因此调整引导题序不触发任何重新合成；只有**改文案**才需要重生成对应 mp3。

## 添加 / 修改流程

1. 改 `manifest.json`（新增条目或调整文案）
2. 设 `MIMO_API_KEY`，跑 `python scripts/onboarding-audio/generate_onboarding_audio.py` 重生成 mp3
3. `scripts/build_client.sh` 之后的 Tauri 阶段会把它们嵌入到 `SpiritAgent-Setup` 安装包
4. 提交 `manifest.json` 和 `generate_onboarding_audio.py`（mp3 不进 git，是构建产物）

CI 用 `python scripts/onboarding-audio/generate_onboarding_audio.py --check` 校验 mp3 同步字节合法、与 manifest 条目一致。

## voice 选择

`冰糖`（mimo TTS 默认中文女声）。整段 onboarding 听感一致；voice preview sample 不在此列，仍走运行时 TTS 让用户试听不同声线。
