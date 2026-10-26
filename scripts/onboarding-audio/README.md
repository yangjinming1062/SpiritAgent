# 引导音频

本目录维护随安装包交付的预制问题语音；使用范围见 [DESIGN](../../docs/DESIGN.md#引导与后台准备)，运行时音色试听另走后端服务。

## 来源与交付

[manifest.json](manifest.json) 定义文案、音色和 tag，[生成脚本](generate_onboarding_audio.py) 调用 MiMo TTS，默认输出 `installer/payload/onboarding-audio/zh/`，安装到 `$SPIRITAGENT_HOME/audio/onboarding/zh/`。manifest 与音频一起提交。

tag 绑定文案而非题序；文案、音色或合成行为变化须重生成，改 tag 同步[引导调用方](../../client/renderer/app/onboarding/onboarding-flow.tsx)，删除时清理旧 MP3。[主进程](../../client/main/ipc/onboarding-audio.ts)只读 `zh`，缺失时尝试仓库 payload，并校验 tag 与 256 KiB 上限；[渲染类型](../../client/renderer/app/onboarding/onboarding-audio.ts)须同步。语言字段与 `--output-dir` 不会自动改变安装或读取路径。

## 修改与验证

仓库根执行，生成需 Backend 依赖及环境变量 `MIMO_API_KEY` 或 `TTS_API_KEY`：

```bash
uv run --project backend python scripts/onboarding-audio/generate_onboarding_audio.py --tag onboarding.q8
uv run --project backend python scripts/onboarding-audio/generate_onboarding_audio.py --check
```

`--tag` 可重复，省略会重生成全部并覆盖音频。请求失败或响应不完整时不写该条目，已完成文件保留；检查后重跑缺失或失败条目。

`--check` 不调用供应商，但仍需 OpenAI SDK；只核对文件集合与 MPEG 帧头，缺失、多余或 ID3v2 头会失败，不能验证文案、音色、时长或完整解码。提交前试听受影响音频。
