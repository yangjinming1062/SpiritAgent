# Main 主进程

可信主进程持有凭据、窗口、Runner、配置镜像、磁盘缓存和更新。渲染层只经 `window.spiritagent` 使用受控能力；跨窗口协作见 [Client](../README.md)，通道类型见 [shared/ipc](../shared/ipc/)。

## 包边界

`entry.ts` 是唯一组合根，负责显式装配，不展开业务逻辑。`backend` 管会话与 HTTP，`runner` 管进程和本地 RPC，`lifecycle` 管窗口、托盘、媒体协议与更新，`ipc` 按能力注册通道，`security` 管准入，`shared` 为叶子层。

`shared` 不导入 backend / runner 实现，通过结构端口由装配层注入。主进程输出 ESM `entry.js`，沙盒 preload 输出 CJS `preload.cjs`；preload 混入 ESM import 会使桥接失效。

## 启动与装配

Home 支持 `SPIRITAGENT_DESKTOP_USER_DATA_DIR` 覆盖并统一设置 Electron userData，配置、日志与缓存不另找目录。默认单实例；`SPIRITAGENT_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK=1` 只用于并行验证，第二实例事件在转发器就绪前折叠保存，之后兑现一次。

远程显示可禁用 GPU。Chromium 后台节流全局关闭，渲染功耗由引擎管理。关窗不退出，真正退出先 flush 配置，再有界等待 Runner 停止；超时可能残留进程，不能宣称已保证全部清理。

## 关键设计决策

### 凭据与渲染面隔离

激活凭据加密落盘，JWT 仅内存；preload 不暴露持久凭据读取。`api()` 只允许相对路径和规定前缀，拒绝绝对 URL、协议相对地址和路径穿越，避免携凭据访问任意地址。

文件读取仅接受选择器或拖拽登记的路径，渲染层不能自行授予白名单；敏感路径另行阻断。白名单仅进程内且有容量限制，重启须重新选择，历史附件路径不自动恢复权限。

`spiritagent-media:` 只读取 Home 下允许的 cache / audio 资源并校验路径与扩展名，不能成为任意文件读取接口。

### 网关宿主—代理

仅宿主窗口可上报网关状态、注入事件和答复代理 RPC，其他表面只能发请求。所有入口核对 sender，不能只依赖 TypeScript 类型。

代理等待设超时，网关 closed / error 时统一 reject。连接缓存 reset 递增代次，迟到连接不得写回；会话凭据通过实时 getter 读取，不捕获过期 token。

### 表面互斥与几何

并发 open / close 通过串行链裁决，生活空间与工作台最多一个可见。工作台移动时由主进程跟随显示器，渲染状态不传递几何。未认证时唤起激活界面须更新渲染状态，不能只 raise 窗口。

快捷键由主进程注册并返回冲突或失败状态。Windows 关闭隐藏到托盘，macOS 隐藏但保留 Dock；多屏和透明命中规则归 [Client](../README.md#窗口与主题)。

### 配置镜像与云同步

镜像写锁串行执行原子落盘、Runner 推送和防抖上云。应用云端镜像期间抑制变更回环；换号先清理不可信旧同步节，机密与设备配置不上云。

Runner 配置补丁只接受工作台 sender。离线编辑与云端冲突不提供版本化合并，精确恢复语义见 [PROTOCOL](../../docs/PROTOCOL.md#24-配置所有权与云端同步)。

### Runner 生命周期

Client 创建 IPC 端点并鉴权 Runner。断连、退出和开始停止立即作废缓存；新握手先推配置，再取工具，迟到查询不能恢复旧资格。

会话懒创建与 token 重接由 `backend/session-runtime` 承担；Runner 桥持有、自动启停与 IPC 由 `ipc/runner` 的 host 承担。登录恢复经回调接回 host，握手关联查询的失效控制不等于已经完整消费 Runner `run_generation`，当前接入限制见协议文档。

更新使用 Home 下的 uv，在原 venv 安装 wheel 并替换 `server.py`，不承诺安装原子切换或自动回滚。导入面一致性在构建期检查，损坏环境交安装器修复。

### 网络与缓存

字节缓存优先使用内容哈希，否则规范化签名 URL 后生成键；历史与账号清理见 [Client](../README.md#资产与历史缓存)。下载、写盘和回调均须遵守取消与用户代次。

[hardening.ts](security/hardening.ts)按路径和方法配置请求等待，新增同步生成入口须核对超时规则。超时不代表后端任务已停；401 按结构化状态处理，不匹配错误文案。

STT / TTS 经 [媒体入口](ipc/media.ts)调用云端，使用有界队列、并发和速率控制；TTS 合并在途请求，缓存命中不排队、不耗调用额度。窗口不复制这套限制。

### 构建产物

tsup 构建 main / preload，共享 IPC 通过 alias 解析；开发监听只覆盖 main 与 shared。修改导出或路径须核对实际文件名与 `package.json` 的 main。

开发 CSP 允许 Vite 所需能力，生产保持严格策略，不能为修复开发白屏放宽生产 CSP。

## 与外部的契约

跨进程变化同步共享类型、preload、主进程 handler 和调用方；权限由 sender 与路径准入落实。后端、Runner、配置及更新见 [PROTOCOL](../../docs/PROTOCOL.md)。

## 已知限制

路径授权不跨进程重启保留，多端配置不做冲突合并，退出清理有超时边界。远程显示可能关闭硬件加速，排查透明与模糊前先确认实际运行模式。

## 验证入口

命令见 [Scripts](../../scripts/README.md#8-按改动选择验证)。覆盖错误 sender、未授权路径、换号、连接 reset 后迟到结果、退出超时和更新失败；preload 与入口变更另查实际构建产物。
