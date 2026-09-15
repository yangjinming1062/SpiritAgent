# 更新签名密钥

本目录只维护随客户端分发的更新验签公钥。签名与两阶段更新契约见 [PROTOCOL §5.5](../../docs/PROTOCOL.md#55-自更新签名client--backend--installer--backend)，发布操作见 [scripts README](../README.md)。

## 公钥与私钥职责

[`update.pub`](update.pub) 由 [client/package.json](../../client/package.json) 的 `extraResources` 打包，[客户端主进程更新器](../../client/main/runner/updater.ts) 用它验证更新清单。公钥进入仓库，私钥只用于构建签名，必须保存在仓库外或 CI Secret 中。

替换公钥需要同时检查已安装客户端的信任关系与发布签名方；仅更换签名私钥会导致持有旧公钥的客户端拒绝更新。不要将私钥加入安装包、更新包或构建日志。

## 本地与 CI 配置

[签名助手](../lib/UpdateManifest.ps1) 的 `Resolve-UpdateSigningKey` 接受环境变量 `SPIRITAGENT_UPDATE_SIGNING_KEY` 指定的 **PEM 文件路径**；未设置时查找用户目录下的 `.spiritagent/update.key`。缺少文件时构建签名步骤失败。

[发布工作流](../../.github/workflows/release.yml) 中同名 Secret 保存的是 **PEM 内容**。Windows 构建先将其写入临时文件，再把文件路径传给签名助手；不能将 Secret 内容直接当作本地环境变量的路径使用。
