# 主动资讯与话题储备

负责每日采集批次、来源快照、消息使用记录与反馈。夜间工作流规划和精选，基础设施执行采集；本模块通过 DTO 与事务内操作协作，不向外暴露 ORM。来源资格、时间窗口及分享政策归 [主动信息与来源分享](../../../docs/PROTOCOL.md#主动信息与来源分享)。

## 任务入口

| 修改任务 | 入口 |
|---|---|
| 批次、来源、预算与使用记录 | [models.py](models.py)、[store.py](store.py) |
| 检索计划与检查点 | [topic_discovery.py](../../workflows/nightly/topic_discovery.py) |
| 聊天资料与引用绑定 | [system_prompt.py](../../workflows/chat/system_prompt.py)、[reply_links.py](../../workflows/chat/reply_links.py) |
| 反馈与通知 | [use_cases.py](use_cases.py)、[companion_topics.py](../../entrypoints/http/routes/companion_topics.py) |
| 平台定义、凭证与内容校验 | [social_sources.py](../../infrastructure/web/social_sources.py)、[social_credentials.py](../../infrastructure/web/social_credentials.py)、[social_content.py](../../infrastructure/web/social_content.py) |
| 备份合并 | [backup.py](backup.py) |

## 采集与消费

每次请求前锁定批次并预占预算，失败或取消不返还；流程提交事务后才访问平台。采集中保存检查点，精选完成时原子清除完整候选和上下文，留下来源与统计。来源必须有可核验的原文和发布时间，模型兴趣判断不能补齐缺失事实。

陪伴每次模型调用按资料资格及当前话题补充来源，正文和字幕以带范围标记的摘录进入模型。提交使用服务端冻结的授权时刻，消息与来源使用记录共同提交。生活阅读通过 `read_saved_topics` 读取已有来源，不增加分享记录，也不受主动分享时窗限制。

已分享事实不因跨日、凭证清除或原消息删除而消失；未分享资料仍按原可用期使用。未反馈和撤销保持未知，后续兴趣判断结合明确反馈与真实交流，不能从沉默推导偏好。

## 平台与网络

平台规则由 [social_platforms.py](../../infrastructure/web/social_platforms.py) 定义。适配器使用账户凭证快照及冻结修订，每次请求复核修订和夜间预算；单个平台失效不阻断其他来源及已取得内容的精选。X 只复用固定版本 SDK 的操作定义、解析器和网页签名，不建立账户池；小红书在搜索/详情的同一浏览器内确认登录，搜索卡片缺少可靠时间时必须读取详情。

X 签名初始化同样消耗接口预算，仅访问规定的 HTTPS 源，Cookie 不发给静态资源域；跳转逐次验证，适配器不自行重试。自托管来源只豁免固定运维 origin，不能扩散到原文或模型给出的地址。Backend 按来源接口请求计数，不把远端浏览器的页面资源当作独立接口调用。

字幕只采纳响应中已有文字，字幕 URL 和视频链接不代表读过字幕或看过视频。部署及登录见 [社交资讯来源](../../README.md#免费社交资讯来源)，浏览器隔离与扫码生命周期见 [小红书服务](../../infrastructure/web/xiaohongshu_service/README.md)。

## 恢复与验证

合并在目标账户核对同日批次的窗口、状态和来源上限。重复来源的原文、字幕、时间及链接必须一致，保留目标已有的模型摘要；衍生描述不同不否定幂等。使用记录按来源和消息去重，消息已清理时按实际使用时间保留独立证据。

恢复清空采集检查点，把采集中批次标为失败，保留原可用时间；过期资料不变成新候选。分组与原子失败规则见 [备份契约](../../../docs/PROTOCOL.md#备份校验与覆盖恢复)。按 [Scripts](../../../scripts/README.md) 核对预算、修订失效、原文范围、引用和重复导入；真实平台字段及链接须用有效个人登录验证。
