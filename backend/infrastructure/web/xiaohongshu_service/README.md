# 小红书登录扩展

此目录构建采集服务的登录扩展。上游 `v2.5.5` 标签对象 `badcc26ce23a617783f31847afb730f16594d347` 指向提交 `a5c8f7799980ba1fdd501999843eb2d17e4c9a9f`；构建下载该提交归档、校验 SHA256、对锁定依赖执行 `go mod vendor`，并用 `git apply --unidiff-zero --check` 核查零上下文补丁。运行层复用官方镜像中的浏览器，只替换服务二进制。原有搜索、详情和鉴权契约沿用上游。

构建默认沿用上游的 `GOPROXY=https://goproxy.cn,direct` 和 `GOSUMDB=sum.golang.google.cn`，保留 Go 模块签名校验；依赖整理独立于补丁层缓存。在 `backend/.env` 配置 `XHS_BUILD_GOPROXY`、`XHS_BUILD_GOSUMDB` 可覆盖构建值，留空使用默认值；直接 `docker build` 则使用同名 `GOPROXY`、`GOSUMDB` 构建参数。模块源列表的逗号只在 HTTP 404/410 时切换来源，网络超时仍失败，按服务器可达性配置；语义见 [Go 模块环境变量](https://go.dev/ref/mod#environment-variables)。镜像依赖拉取失败时修改模块源并重新构建采集服务，运行时的 `XHS_PROXY` 不控制 Go 依赖下载。

`login_admin.go` 拥有运营者当前扫码会话。二维码增加随机 `session_id`；`GET /api/v1/login/session?session_id=…` 读取该会话，`POST /api/v1/login/verification` 接收该编号和六位 ASCII 数字 `code`。检查和提交复用同一个浏览器页面，不主动导航或发送短信。可识别的短信验证显示 `sms_required`，验证控件加载、提交处理中或成功跳转显示 `verifying`，明确的滑块等其他验证显示 `verification_required`。验证码只向同一可见验证弹窗中唯一的输入框进行一次原生输入；平台自动提交时等待状态收敛，需要确认时重新定位当前弹窗，最多点击一次。单次提交受会话有效期与 18 秒操作期限约束，不自动重填或重交验证码。

扫码会话有效期四分钟。浏览器与页面事件使用独立生命周期，二维码请求结束不会取消会话；获取新二维码、清除登录或到期会释放旧浏览器。成功保存 Cookie 后释放浏览器并保留成功状态，直到新会话或清除；查询不存在或已被替换的编号返回 `409`，过期编号返回 `410`。清除接口的 `login_session_cancelled: true` 表示当前二维码会话也已结束。原有 `login_session.go` 保留在下载的上游源中供原测试引用，扩展不使用它。

新增登录接口沿用上游 `AUTH_TOKEN`，设置 `Cache-Control: no-store`；验证码请求最多 1 KiB，不记录请求正文，也不返回手机号、验证码、平台 Cookie 或平台原始错误。页面检查、验证码输入、确认按钮和 Cookie 保存失败分别使用固定错误码，服务日志只记录该阶段码，供后台定位。运营者登录状态仍保存在专用卷，用户备份不包含该卷。生产操作入口与部署说明见 [Backend](../../../README.md#免费社交资讯来源)。
