# 更新签名密钥

本目录仅保存 Runner 更新验签公钥 [update.pub](update.pub)，随 Client `extraResources` 交付。[签名助手](../lib/UpdateManifest.ps1) 生成 `latest-runner.yml`，[Client 更新器](../../client/main/runner/updater.ts) 验签；这与桌面安装包的平台证书签名是不同机制。完整信任与安装语义见 [PROTOCOL](../../docs/PROTOCOL.md#自更新签名)。

## 公钥与私钥职责

私钥放仓库外或 CI Secret，禁止放入安装包、更新包或日志。轮换需设计已安装客户端的信任迁移，直接更换密钥会使旧客户端拒绝新签名。

## 本地与 CI 配置

当前 `build.py` 仅在 Windows 生成 update ZIP。签名需要 openssl；助手先查 PATH，再查常见 Git for Windows 安装位置。私钥输入区分运行环境：

| 环境 | `SPIRITAGENT_UPDATE_SIGNING_KEY` |
|---|---|
| 本地 PowerShell | PEM **文件路径**；未设置时读用户目录 `.spiritagent/update.key` |
| GitHub Actions Secret | PEM **内容**；[工作流](../../.github/workflows/release.yml) 写入临时文件后传路径给助手 |

文件缺失或签名失败中止 Windows 更新包构建。发布操作统一见 [Scripts](../README.md#发布)。
