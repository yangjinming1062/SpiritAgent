# macOS 桌面原生模块

`desktop-macos.mm` 是 Client 主进程加载的 C Node-API 扩展，不修改 Finder、Dock 或其他进程的系统配置。入口和返回字段以源码与主进程 [desktop-macos.ts](../main/shared/desktop-macos.ts) 为准；呈现状态归 [desktop-presentation.ts](../main/lifecycle/desktop-presentation.ts)。Windows helper 独立维护。

## 线程与生命周期

表面层级、Space 监听和 `NSWorkspace` 图标绘制在主线程执行。应用 bundle 校验与元数据读盘使用后台任务；图标请求每批最多 8 项，调用方继续按可见条目分批。所有任务有总预算，AppKit 单次调用无法被 JavaScript 超时中止，因此不在一次主线程回调里绘制完整目录。

AX 枚举与窗口操作使用 Node 后台任务和独立串行锁，每个 AX 元素设置消息超时，队列等待计入总预算。`reset()` 立即作废宿主代次并清除窗口登记与监听，不等待 AX 工作线程；`cancelOperations()` 只取消后续动作，保留目录和窗口 ID。停止、Space 变化、用户继续操作及前台切到其他应用都会阻止后续窗口调整和应用激活。已经进入系统的单次 AX 调用无法撤回；超时保留结果未知的原因，调用方不能把它显示为明确拒绝或成功。

## 窗口身份与坐标

表面句柄先在本进程的 `NSView` 树中比对，不解引用任意外来地址。背景采用运行时查询的桌面图标层级减一，交互窗口保持普通层，Electron 继续管理交互层鼠标穿透。两表面使用 `Stationary` 留在桌面，透明空白点击穿透到 Finder 后，系统“显示桌面”仍保留底栏。表面参加普通桌面 Space，不声明全屏辅助窗口；原生全屏和前台资格仍由主进程轮询状态裁决。

传入范围、AX 窗口位置和 Quartz 屏幕范围采用主屏左上原点的逻辑坐标；渲染器工作区由主进程转换为交互屏本地 DIP，不把 `NSScreen` 左下坐标直接传给页面。

运行窗口按进程、位置、大小及可取得的标题与当前 Space 的 Quartz 窗口唯一配对，再分配不透明 ID。执行时重新核对进程创建时间、AX 元素、交互屏与前台；不将可复用的 PID 或窗口序号直接当作授权。Space 变化清除登记，避免把旧 Space 的窗口迁入。

公开 API 不能可靠确定首次发现的最小化窗口属于哪个 Space，因此仅保留本宿主会话中曾在当前 Space 可见、随后最小化的窗口；首次即最小化的窗口不进入运行投影。多个窗口的几何与标题都无法唯一匹配时也不猜测。跨屏迁移保留能容纳的原尺寸，过大时缩入目标工作区；全屏和未决模态框的跨屏迁移明确拒绝，本屏激活允许程序显示自己的模态框。关闭只提交普通关闭按钮操作，不绕过保存提示，也不把提交回执视为已关闭。

## 构建与验证

运行 `pnpm --dir client build:native`，或使用 [build-desktop-macos.cjs](../scripts/build-desktop-macos.cjs) 指定目标架构。构建使用 Xcode Command Line Tools 与 Node-API 头文件，产物是 `build/<arch>/desktop_macos.node`；无需新增运行时包。打包、签名与完整验证入口归 [Scripts](../../scripts/README.md)。

加载、辅助功能状态查询、应用元数据和图标可在未授权环境只读验证。窗口枚举、激活、关闭和迁移须由实际授权环境验收；只读探针不得调用授权请求或操作外部应用。临时探针不提交仓库。
