# 更新签名密钥

本目录只保存客户端验签公钥 [update.pub](update.pub)，经 `client/package.json` 的 extraResources 打包。签名与安装语义见 [PROTOCOL](../../docs/PROTOCOL.md#55-自更新签名client--backend--installer--backend)。

## 公钥与私钥职责

公钥进入仓库，私钥只放仓库外或 CI Secret，不进入安装包、更新包或日志。轮换须处理已安装客户端的信任迁移；只更换私钥会使旧客户端拒绝更新。

## 本地与 CI 配置

本地 [签名助手](../lib/UpdateManifest.ps1)读取 `SPIRITAGENT_UPDATE_SIGNING_KEY`，值是 PEM **文件路径**；未设置时读取用户目录 `.spiritagent/update.key`，文件缺失则签名失败。

CI 同名 Secret 存 PEM **内容**，工作流先写临时文件再传路径。两者不可混用，发布流程见 [Scripts](../README.md#2-发布--tag-触发-github-actions)。
