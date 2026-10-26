# Windows 桌面宿主

Rust helper 管理 Explorer 挂载、系统工作区、运行窗口和异常恢复；Electron 会话归[主进程](../main/README.md#桌面承载与恢复)。[main.rs](src/main.rs) 定义命令，[windows.rs](src/windows.rs) 组织接管事务；窗口、托盘、工作区分别归 `src/windows/` 下对应模块。

| 进程模式 | 职责 |
|---|---|
| `host` | 读取受限 JSON，只操作本次登记的表面与核验过的目标 |
| `guardian` | 独立监视父进程、host 和十秒续租，异常或紧急键时恢复 |
| `recover` | 处理持久化 journal |
| `check-runtime` | 只检查可执行文件加载 |
| `display-bounds` | 只读取得匹配显示器的真实像素矩形 |
| `tray-probe` | 在独立进程预算内查询托盘 UI Automation 边界 |
| `allow-foreground` | 核验前台父进程和同源 host，转授一次前台权限 |

## 挂载与输入

根据实际 Explorer 结构识别 `SHELLDLL_DefView`、图标层和壁纸 WorkerW，不能只按 Windows 版本选层级。分层结构须确认同父层下已分离可见壁纸，再把背景排在图标与壁纸之间；传统结构用图标层后方 WorkerW。缺层时在 guardian 就绪后有界请求创建并重新识别，不能隐藏共享图标或修改图标偏好。交互界面保持普通顶层，一次接管只接受同屏的一份背景和界面。

挂载前核对父进程、窗口创建身份、完整 DPI context 与显示器几何，物理位置和尺寸各项误差不超过两像素；挂载后复验外框、客户区及图层顺序。Electron 去掉 frame／thickFrame，helper 按实际结构设置样式；父层、样式、几何与置顶信息必须先记 journal 再修改。背景不接收输入，透明界面只命中登记区域。

`display-bounds` 在 PMv2 context 下按近似物理矩形匹配显示器并调用 `GetMonitorInfoW`，不匹配返回空值；取整 DIP 不能反推实际像素。该查询不访问 Explorer、journal 或挂载状态。

`focus` 只接受仍匹配身份、父层和可见性的登记界面，系统须处于桌面前台；先置前，待修饰键和鼠标释放后临时附着输入线程并复验前台。线程解除失败退出 host 交 guardian 恢复，不能保留附着继续运行。

## 运行窗口

[applications.rs](src/windows/applications.rs) 枚举当前虚拟桌面各屏普通／最小化窗口，排除壳、隐藏、工具浮窗和产品自身。会话共用 WinEvent，保留移动与销毁顺序，周期补扫；事件溢出清登记后全量校准。未变不广播，快照按 revision 分批，主进程收齐才采纳，换代丢弃半批。行大小及接收预算同时受 [explorer-desktop-host.ts](../main/lifecycle/explorer-desktop-host.ts) 限制。

| 操作 | 必须保留的边界 |
|---|---|
| 激活／迁移 | 重核会话、窗口身份、虚拟桌面与前台；只有固定项／目录异屏回退允许迁移，其余目标须仍在交互屏。本屏仅还原最小化，跨屏适配 DPI 并保留最大化及还原位置 |
| 模态窗口 | 准备根窗口后解析当前模态分支，不能用 `GA_ROOTOWNER` 替代用户所选窗口；隐藏 owner 下各主窗保持隔离 |
| 关闭 | 整批先核归属和未决模态，再逐个重核并发 `WM_CLOSE`；回执只证明投递 |
| 普通启动／Jump List | 共用原生启动定位，异步等待不阻塞心跳；Jump List 只解析有界 Shell Link 的 Task，执行时重读任务 ID 和目标，原参数不交给 renderer |

前台授权在目标就绪、执行名额预留后请求，绑定操作 ID、单调代次和当前模态目标。主进程保留原始输入及账户资格，短命子进程核验前台再转授。授权回执和工作线程执行前都重核前台、身份及屏幕，模态或几何改变则重规划、重授权。前台暂空仅暂缓，真正切到其他程序取消后续动作，不能复用旧授权抢回前台。

启动后在有界窗口内关联启动前集合、进程身份及新增窗口。单实例复用还要求没有后续输入、程序窗口唯一、前台跨入该分支，并由返回进程或已退出启动进程证明交接；歧义和到期如实报告未定位。成功关联只消费一次，不持续吸附同程序窗口。仅 `--user-data-dir` 不同的重复 Jump List 任务合并，损坏链接跳过并保留诊断。

迁移、工作区校正及前台请求使用受管理的 PMv2 线程。同一 HWND 单飞，线程与焦点预留合计最多 16 个；取消但尚未返回的调用继续占名额，不能另开线程绕过。名额满时保留去重意图，释放后按新鲜几何重试；只读等待不占名额。原生回执之后仍核实际外框、屏幕、DPI 和最大化状态，耗时或几何静止不能证明拒绝。

新按键、点击、滚动、拖动、前台切换、工作区变化及会话结束会取消后续校正和焦点步骤；悬停、原手势释放与短暂空前台不算新操作。已进入 Windows 的调用无法撤回。等待外部程序不持恢复锁，停止先取消任务并恢复系统，不等待阻塞线程，线程随 host 退出结束。

## 原生系统托盘

[tray.rs](src/windows/tray.rs) 探测刷新，[tray_placement.rs](src/windows/tray_placement.rs) 恢复位置，[regions.rs](src/windows/regions.rs) 保存区域。接管只平移交互屏原任务栏到 Dock 槽位，保留原尺寸、父子关系及托盘子树，裁剪为托盘与滚动视口交集。优先用 `TrayNotifyWnd`，缺失时只读 UI Automation 并复核 Explorer、任务栏、屏幕与坐标。探测独立限时，不能阻塞心跳与恢复；不能可靠定位或应用区域时结束接管。

固有尺寸与平移／滚动位置分开，WinEvent 和巡检刷新 DPI、显隐、图标及 Explorer 重建；不跨屏复制托盘，不纳入第三方任务栏面板，也不用固定宽度。槽位变化先发布会话物理目标，再改原生位置和裁剪。Explorer 继续处理图标、菜单及系统面板，弹窗不参加任务栏裁剪或工作区校正。

### 位置与透明样式

[tray-style.cpp](tray-style/tray-style.cpp) 在 Explorer 内应用可逆子类和 SDK-only C++/WinRT 样式。子类在 `WM_WINDOWPOSCHANGING` 原处理后约束位置，保留尺寸、层序及标志，不代理输入；目标通过独立映射和有界 seqlock 读取，绑定 host 创建身份、任务栏及续租。空布局、全屏和失租先停约束，所属线程解除；绕过该消息的系统变动由巡检及失败恢复处理。

XAML 修改只作用登记任务栏：清背景与边线，按应用主题调整系统前景，第三方位图保留原 `Image.Source` 并加独立浅色底片。目标值变化才写属性，原值留在所属 XAML 线程；新树不能沿用旧对象原值，系统面板和其他任务栏不改样。窗口合成与 XAML 透明均确认后才显示原生行，失败回窗口模式。

样式状态核验同一 Windows 会话、host、Explorer 和任务栏身份，正常停止、host 退出或失租都恢复原属性及观察。诊断订阅单飞并配对初始化／释放 COM。`E_UNEXPECTED` 仅在已收到本次回调且目标样式验证成功时容忍，其余失败中止接管。停用时先确认原值恢复，再解除自有订阅；两阶段分别上报，解除确认前保留诊断连接。失败清理不解除其他组件订阅，迟到错误归原会话；解除失败允许换新诊断连接，但持续不可用时需重启 Explorer 后再启用。

DLL 以内容 hash 命名，启用前复制并逐字节核对到当前用户 `LocalAppData/SpiritAgent/native/tray-style/`，不让 Explorer 映射安装／开发目录阻塞更新。已缓存或已载入 DLL 不覆盖、不删除，下次按 manifest 选当前模块。

Electron 保留 Dock 玻璃绘制，原生行在上方接受输入。缺布局、布局清空或界面隐藏时收起托盘；主进程只将固有尺寸和实际可见性投影给 renderer。跨层状态语义归 [呈现契约](../../docs/PROTOCOL.md#桌面呈现与本机启动器)。

## 工作区与层级

[workspace.rs](src/windows/workspace.rs) 仅为交互屏注册底部 AppBar，计入系统已有占用，保留托盘不重复扣高度；菜单和功能窗口不扩大预留。helper 以真实像素协商并上报实际工作区，注册、定位及恢复共用锁。共享 Explorer 前台按指针屏幕判断，其他屏桌面不取得交互屏焦点资格。

普通窗口校正先平移、过大再缩小，不激活；排除系统菜单、输入法、隐藏、最小化和真正全屏。Explorer、WorkerW 与系统弹窗宿主不能误判为全屏，隐藏托盘菜单不得触发界面收起或任务栏复位。

`showInactive()` 后仍须读取原生矩形，不能信 Electron 缓存。校正使用 `SWP_NOZORDER` 保层级，图标／背景顺序或两像素几何误差持续不收敛时恢复；Explorer 重启重新识别宿主与 AppBar，失败退回窗口。全屏、最小化和退出时清复验计时。

## 恢复

guardian 持有原值并成功注册 Ctrl+Alt+Shift+D 后才允许接管。恢复信号绑定会话和 host 创建身份，在锁内置位后阻止新写入；信号失败只能停止已核验的自有 host，诊断失败不能延误恢复。

journal 必须包含完整阶段、窗口区域、托盘位置和有效 DPI；仅尚未挂载的记录可直接清理。所有系统变更先落原值，恢复时再核 HWND 创建身份，防止把旧状态写入复用句柄。

恢复依次停止位置约束与样式、注销 AppBar、还原任务栏几何／置顶／区域／显隐；原屏幕或 DPI 改变时让 Explorer 重新协商，不写回旧缩放尺寸。辅助映射失败仍尽力恢复可核验状态并保留 journal。透明交互面的区域保持原样，桌面图标坐标不保存或回写，摘除后只重绘释放屏幕，不重设壁纸。

异常写 `.interrupted`，正常停止不写；host 不覆盖 guardian 原因。诊断有界写 `.guardian.log` 和 stderr，失败保留记录继续重试。所有恢复进程均被强杀时只能由下次启动处理待恢复记录。

## 构建

需要 Rust 1.85+、Visual Studio 2022+ 目标架构 C++ 工具和含 C++/WinRT 的 Windows SDK：

```sh
pnpm --dir client build:native
```

[build-desktop-host.cjs](../scripts/build-desktop-host.cjs) 按 Electron 架构生成 helper，并由[样式构建](../scripts/build-desktop-tray-style.cjs)输出 hash DLL 与 `desktop-tray-style.json`。复用须核源码／构建输入 hash、manifest、架构及普通和延迟导入；样式使用 `/MT`，不接受额外 VC／UCRT 运行库。开发保留旧 hash 文件，打包暂存仅含当前 manifest 所选 DLL，最终包再验架构。`cargo check --locked` 不覆盖 C++、链接和打包。

开发 launcher 使用独立 Electron 副本，开发和 Windows 打包写 PMv2 manifest 并保留原元数据；准备失败阻止新接管，已有 helper 仍可恢复。补工具链后重启客户端，main / preload 重编译先恢复桌面再重启；结束开发须等 Electron 退出，超时只停自有主进程并交 guardian 恢复。

### 安全运行检查

`check-runtime` 输出 `runtime_ready` 后成功退出，不设 DPI、不访问 Explorer／journal、不注册热键。`display-bounds` 和 `tray-probe` 同样不执行接管。

## 原生验收

`SPIRITAGENT_DESKTOP_PROBE=1` 会实际挂载背景，保留系统任务栏且不预留工作区；它不是无副作用加载检查。完整 [Windows 验收](../../scripts/README.md#windows-桌面验收)另覆盖图层、输入、托盘、迁移和异常恢复。
