# macOS 桌面原生模块

[desktop-macos.mm](desktop-macos.mm) 是主进程加载的 C Node-API 扩展，管理自有表面层级、应用元数据与 AX 窗口操作。接口由源码和 [desktop-macos.ts](../main/shared/desktop-macos.ts) 定义，模式状态仍归[主进程宿主](../main/lifecycle/desktop-macos-host.ts)；不修改 Finder、Dock 或其他进程的系统配置。

## 线程与生命周期

AppKit 表面操作、Space 监听与图标绘制在主线程执行；bundle 校验及读盘在后台执行。图标按可见项分批，每批最多 8 项，不能在一次主线程回调里绘制完整目录。任务有总预算，但 JavaScript 超时不能中止已进入 AppKit 的调用。

AX 枚举与操作使用后台任务、独立串行锁及逐元素消息超时，排队计入总预算。`reset()` 立即推进宿主代次并清空登记和监听，不等待 AX 线程；`cancelOperations()` 只阻止后续动作，保留目录与窗口 ID。停止、Space 变化、新用户操作或切到其他前台应用使后续校正失效。已进入系统的 AX 调用无法撤回，超时须保留结果未知，不能宣称明确失败或成功。

## 窗口身份与坐标

| 边界 | 必须保持的约束 |
|---|---|
| 表面句柄 | 先在本进程 `NSView` 树中比对，不解引用任意外来地址 |
| 图层与 Space | 背景使用运行时桌面图标层减一，交互窗为普通层；两者使用 `Stationary` 并参加普通桌面 Space，不声明全屏辅助窗口；鼠标穿透由 Electron 管理 |
| 坐标 | 输入、AX 与 Quartz 均用主屏左上原点逻辑坐标，主进程转为交互屏本地 DIP；不能直接向页面传 `NSScreen` 左下坐标 |
| 窗口身份 | 进程、几何与可用标题唯一匹配当前 Space 的 Quartz 窗口，再分配不透明 ID；执行重新核对进程创建时间、AX 元素、屏幕与前台 |
| Space 切换 | 清除窗口登记，旧 ID 不能用于迁移 |

公开 API 无法可靠识别首次发现的最小化窗口所属 Space，因此仅保留本会话已在当前 Space 观察到、随后最小化的窗口；几何与标题无法唯一匹配也不猜测。迁移保留可容纳尺寸，过大才缩入目标工作区；全屏或未决模态跨屏迁移明确拒绝，本屏激活可交由程序处理模态框。关闭仅请求普通关闭按钮，不绕过保存提示，回执不证明窗口已消失。

## 构建与验证

支持 macOS 12 及以上；arm64 与 x64 分别构建，不支持 universal 模块。

在 macOS 执行 `pnpm --dir client build:native`；指定架构使用 [build-desktop-macos.cjs](../scripts/build-desktop-macos.cjs)。构建依赖 Xcode CLT 与 Node-API 头，输出 `build/<arch>/desktop_macos.node`。打包和签名归 [Scripts](../../scripts/README.md#client-开发与打包脚本)。

加载、权限状态、元数据与图标可以只读验证；窗口枚举、激活、关闭和迁移须在已授权环境执行[平台验收](../../scripts/README.md#macos-桌面验收)。只读探针不得请求授权或操作外部应用，临时探针不入库。
