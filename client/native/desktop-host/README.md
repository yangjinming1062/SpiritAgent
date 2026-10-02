# Windows 桌面宿主

独立 Rust 可执行文件，不依赖 Electron ABI。`host` 通过标准输入的受限 JSON 命令接收主进程登记的 HWND；`guardian` 独立监测主进程、host 与十秒续租，负责异常和 Ctrl+Alt+Shift+D 恢复；`recover` 处理遗留 journal。

WorkerW 属于 Explorer 兼容适配。附着前校验父进程、窗口身份及 DPI，附着后复验几何；guardian 持有原始状态并注册紧急键后才接管。只临时隐藏图标视图与任务栏，不修改系统持久偏好。恢复失败保留 journal 和守护进程。

journal 的 `prepared / attaching / active` 阶段区分尚未改动和已开始挂载；缺少阶段的旧记录按需要恢复处理。异常恢复在同一锁内先写 `<journal>.interrupted`，正常停止不写；主进程下次启动消费标记并停留窗口模式，防止 journal 已清理后再次自动接管。标记失败仍优先恢复，诊断有界写入 `<journal>.guardian.log` 和 stderr。

## 构建

使用 Rust 1.85 以上和 Windows MSVC 链接工具。Windows 打包的 beforePack 自动执行以下入口，helper 缺失或编译失败时中止。

```sh
pnpm --dir client build:native
```

脚本默认按本机架构选择 Windows 目标，也接受 `--target x86_64-pc-windows-msvc` 或 `--target aarch64-pc-windows-msvc`；构建预算十分钟。产物按架构保存在 `client/build/<arch>/desktop-host.exe`，打包放入 `resources/desktop-host.exe`；开发入口使用 `client/build/desktop-host.exe`。静态验证使用对应目标的 `cargo check --locked`，不能替代 Windows 链接工具与打包检查。

## 原生验收

`SPIRITAGENT_DESKTOP_PROBE=1` 下选择桌面模式会挂载 Explorer 子窗口，保留系统图标与任务栏；探测成功不代表交互与恢复已验收。完整矩阵见 [Windows 桌面验收](../../../scripts/README.md#windows-桌面验收)。所有进程同时被强制终止时不能保证即时恢复，下次启动处理遗留记录。
