# 产品展示素材

根 [README](../../README.md#实机演示) 使用本目录展示产品。概览图说明方向；视频来自实机录屏；PNG 使用真实组件与虚构数据，只说明采集版本的布局。

| 文件 | 来源与覆盖范围 |
|---|---|
| [overview.svg](overview.svg) | 2026-10-26 更新的手工产品概览；非运行截图，无外部图片、字体或脚本依赖 |
| [demo.mp4](demo.mp4) | 2026-10-20 Windows 录屏剪辑，3 分 45 秒、1920×1080、30 fps；覆盖创建、功能页、桌面模式与动作预览，原录屏无可核实构建号 |
| [demo-cover.jpg](demo-cover.jpg) | 视频片头抽帧，1280×720 |
| [chat.png](chat.png)、[work.png](work.png)、[settings.png](settings.png) | 2026-10-15，提交 `fe06115e`；日色陪伴、夜色文案秘书和当时的互动设置 |
| [desktop.png](desktop.png) | 2026-10-22 的桌面组件，1600×900；日色透明主题、无媒体降级背景、专业对话与当时底栏 |

视频的创建段主要为 2.5 倍速，功能页按讲解停帧，桌面动作保留实机画面；口播由 MiMo 合成并配字幕。PNG 通过仓库外临时 Vite 入口渲染 [MainRoot](../../client/renderer/app/windows/main/main-root.tsx)、[DesktopRoot](../../client/renderer/app/windows/desktop/desktop-root.tsx) 等真实组件，用本地桩替换 IPC 与后端，账户和对话均为虚构。没有读取真实账户、调用供应商或启用 Windows Explorer 接管。

现有演示尚未覆盖当前生活区、持续事项与成果链路；PNG 也未验证原生桌面行为、生成后形象、视频播放或手机实机界面。不能据此宣称长期生活、模型质量或平台验收通过。

更新时从当前组件或实机重新采集，不手工重画未实现界面；注明平台、版本、时间、真实／虚构数据、人工介入和失败范围，并同步根 README。跨日生活展示应可追溯实际记录和成果。清除账户标识、密钥、激活／配对码和私人内容，检查可读性与链接；除已配置豁免的 `demo.mp4` 外，文件须满足仓库 1000 KB 检查。
