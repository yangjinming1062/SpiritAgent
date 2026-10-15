# 产品展示素材

根 [README](../../README.md#界面预览) 使用本目录展示产品概览与当前界面。概览图是能力示意，截图展示真实组件布局，不作为原生桌面行为或模型质量的验收证据。

| 文件 | 来源与范围 |
|---|---|
| [overview.svg](overview.svg) | 手工绘制的产品能力导览；不模拟应用界面，无外部图片、字体或脚本依赖 |
| [chat.png](chat.png) | 当前 `MainRoot` 与公共对话页，日色主题、虚构陪伴会话 |
| [desktop.png](desktop.png) | 当前 `DesktopRoot`、背景入口与公共对话页，日色透明主题；浮动专业对话、右侧轻语及 Dock，视频未就绪 |
| [work.png](work.png) | 同一客户端入口，夜色主题、虚构文案秘书会话 |
| [settings.png](settings.png) | 同一客户端入口，“设置 → 互动”，展示现有选项 |

截图采集于 2026-10-15，基于提交 `fe06115e` 的客户端代码。通过临时 Vite 入口在 Chromium 中渲染 [MainRoot](../../client/renderer/app/windows/main/main-root.tsx)、[ConversationPage](../../client/renderer/app/features/conversation/conversation-page.tsx) 与 [AppSettings](../../client/renderer/app/features/settings/app-settings.tsx)，替换 IPC / 后端响应为本地演示数据；保留产品组件与样式。采集入口不进入运行版代码。对话、名称与账户均为虚构，以项目图标作为演示头像，没有调用供应商或读取真实账户数据。

桌面图使用 [DesktopRoot](../../client/renderer/app/windows/desktop/desktop-root.tsx) 与 [背景入口](../../client/renderer/app/windows/desktop/desktop-background-entry.tsx) 在浏览器中分层渲染，保留真实顶栏、浮动面板、轻语、Dock 与无媒体时的背景降级。图中专业对话与陪伴对话独立，设置面板已最小化；没有采集本机已安装应用，也没有启用 Windows Explorer 挂载。

未展示生成后的透明精灵、场景图片、视频播放、Windows Explorer 接管或手机实机界面；这些效果仍需对应平台验收。后续补充实机素材时，在本页标明平台、版本与所展示的状态，并同步根 README 的来源说明。

更新截图应从当前组件重新采集，不手工重画控件或把未实现功能加进画面。移除账户标识、密钥、激活码、手机配对码与私人内容，检查文字可读性、截取范围和 GitHub 展示效果；单个文件保持在仓库 1 MB 检查上限以内。
