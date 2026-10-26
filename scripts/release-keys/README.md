# 更新签名密钥

[update.pub](update.pub) 是 Runner 更新验签公钥，随 Client `extraResources` 交付。[签名助手](../lib/UpdateManifest.ps1)生成 `latest-runner.yml`，[Client](../../client/main/runner/updater.ts)验证；它独立于桌面平台证书签名，信任与安装契约见 [PROTOCOL](../../docs/PROTOCOL.md#自更新签名)。

## 公钥与私钥职责

私钥仅存仓库外或 CI Secret，不进入源码、安装包、更新包或日志。轮换须安排已安装客户端的信任迁移，直接换公钥会使旧客户端拒绝新签名。

## 本地与 CI 配置

Windows update ZIP 需要 openssl；助手按 PATH 和常见 Git for Windows 位置查找。

| 运行环境 | `SPIRITAGENT_UPDATE_SIGNING_KEY` 的含义 |
|---|---|
| 本地 PowerShell | PEM 文件路径；未设时使用用户目录 `.spiritagent/update.key` |
| GitHub Actions | Secret 保存 PEM 内容，[工作流](../../.github/workflows/release.yml)落地临时文件后将路径传给助手 |

私钥缺失或签名失败中止构建。当前只生成 Windows 更新 ZIP，发布入口见 [Scripts](../README.md#发布)。
