# see-through 拆分模块

2D 形象的唯一拆分路径：调 see-through 的在线 Space 把单张立绘拆成 22 个语义层 PSD（含遮挡区域补全），产物供客户端 Puppet 渲染层消费。主用 Hugging Face Space（[shitagaki-lab/see-through](https://github.com/shitagaki-lab/see-through)，Apache-2.0），魔搭社区 ModelScope 创空间为备用。

## 模块边界与备用配置

本模块只做供应商传输，失败统一为限额、传输或空间错误；切换、冷却与共享预算见 [PIPELINE §6.1](../../../../docs/PIPELINE.md)。预算剩余不足一分钟时跳过备用，避免越过整包清扫期限。

- 主用地址配置为 `seethrough_space_base`；备用为 `seethrough_fallback_base`，空串禁用备用。
- 魔搭备用使用 API-Inference 专用域与 `seethrough_fallback_token` 的 Bearer 鉴权；www 代理域会重定向且要求 token，不能作为匿名接口。
- 限额冷却只记录主用，备用限额不抑制主用。两个 base 均可换为自托管实例，空 token 仅适用于允许匿名的实例。

## 关键约束

- **无 SLA、社区免费算力**：失败/超时统一抛 `SeeThroughError`，由 mesh2d pipeline 落失败态（无 CPU 兜底链，客户端落 3D/蛋 + 设置页重试）
- **协议坑**：参数必须传 Gradio FileData 对象（`{"path": ..., "meta": {"_type": "gradio.FileData"}}`），裸路径字符串被静默拒收（`event: error` + `data: null`）
- **产物边界**：本模块只返回分层 PSD 字节；完整资产包生成、存储与发布由 [2D 编排模块](../mesh2d/README.md) 负责，产物约束见 [能力链说明](../../../../docs/PIPELINE.md#62-扶边姿态包)。后端不解析 PSD，也不依赖 psd-tools。
- **魔搭冷启动**：ModelScope 创空间休眠后首调含唤醒时间，900s 单次超时覆盖
