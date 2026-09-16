# Onboarding 引导音频

本目录维护预渲染的引导词及其合成脚本。音频由云端 TTS 生成后随安装包分发，客户端引导阶段读本机文件播放；音色试听仍走运行时 TTS，不属于这套预制音频。

## 来源与交付

- [manifest.json](manifest.json)维护文案、音色、语言与 tag；[生成脚本](generate_onboarding_audio.py)读取它，输出到 [installer/payload/onboarding-audio/zh/](../../installer/payload/onboarding-audio/zh/)。请求结构与后端 MiMo TTS 适配一致，不保证两次云端合成的音频字节相同。
- MP3 与 manifest 一并提交；[Tauri 资源配置](../../installer/src-tauri/tauri.conf.json)将音频嵌入安装包，释放位置见 [Installer](../../installer/README.md#3-架构地图)。
- tag 与文案绑定，不与题号绑定；[引导流程](../../client/renderer/app/onboarding/onboarding-flow.tsx)使用对应的音频标识。调整题序无需重合成，修改文案、音色或合成行为需更新受影响的音频；增加或改名 tag 时同步调用方。

## 修改与验证

以下命令在仓库根目录执行，使用后端项目依赖；静态检查也需安装 OpenAI SDK，因为脚本在加载时导入它。

1. 修改 manifest，并核对引导流程中的 tag 引用。
2. 需要合成时配置环境变量 `MIMO_API_KEY`（或 `TTS_API_KEY`），执行：

   ```bash
   uv run --project backend python scripts/onboarding-audio/generate_onboarding_audio.py
   ```

   当前脚本每次重新合成整个 manifest，会调用供应商并覆盖对应 MP3；不是按变更条目增量生成。删除 tag 后还需移除已不使用的音频文件。
3. 执行静态检查，并试听受影响音频后提交 manifest 与 MP3：

   ```bash
   uv run --project backend python scripts/onboarding-audio/generate_onboarding_audio.py --check
   ```

`--check` 不调用供应商，只核对 MP3 文件名与 manifest 一致、文件头符合脚本接受的帧同步字节；不验证朗读内容、音色、时长或完整解码，不能代替试听。
