# 主动资讯与话题储备

负责每日采集批次、来源快照、消息使用记录与反馈。夜间工作流规划和精选，基础设施执行采集；本模块通过 DTO 与事务内操作协作，不向外暴露 ORM。

## 任务入口

| 修改任务 | 入口 |
|---|---|
| 批次、来源、预算与使用记录 | [models.py](models.py)、[store.py](store.py) |
| 检索计划与检查点 | [topic_discovery.py](../../workflows/nightly/topic_discovery.py) |
| 聊天资料与引用绑定 | [system_prompt.py](../../workflows/chat/system_prompt.py)、[reply_links.py](../../workflows/chat/reply_links.py) |
| 反馈与通知 | [use_cases.py](use_cases.py)、[companion_topics.py](../../entrypoints/http/routes/companion_topics.py) |
| 平台定义、凭证与内容校验 | [social_sources.py](../../infrastructure/web/social_sources.py)、[social_credentials.py](../../infrastructure/web/social_credentials.py)、[social_content.py](../../infrastructure/web/social_content.py) |

## 采集与消费

每次请求前锁定批次并预占预算，失败或取消不返还；流程提交事务后才访问平台。采集中保存检查点，精选完成时原子清除完整候选和上下文，留下来源与统计。来源必须有可核验的原文和发布时间，模型兴趣判断不能补齐缺失事实。

陪伴先在同一账户和预设中按话题匹配、反馈和原文时间选择摘要，再按当前模型可用窗口补入正文与字幕摘录。生活判断使用当前活动与事项，夜间规划结合当日交流与事项选择摘要。普通交谈和回忆首次引用往日收藏须附带显示发布日期的原文卡片。含这类候选的回合先核对完整回复，遗漏的卡片交格式修复。提交使用服务端冻结的授权时刻，消息与来源使用记录共同提交。生活阅读通过 `read_saved_topics` 读取已有来源，不增加分享记录。

已分享事实不因跨日、凭证清除或原消息删除而消失；未分享的往日资料可在话题相关时引用，保留原文日期；例行联系判断只察看当期尚未分享的发现。未反馈和撤销保持未知，后续兴趣判断结合明确反馈与真实交流，不能从沉默推导偏好。

## 平台与网络

平台规则由 [social_platforms.py](../../infrastructure/web/social_platforms.py) 定义。适配器使用账户凭证快照及冻结修订，每次请求复核修订和夜间预算；单个平台失效不阻断其他来源及已取得内容的精选。X 只复用固定版本 SDK 的操作定义、解析器和网页签名，不建立账户池；小红书在搜索/详情的同一浏览器内确认登录，搜索卡片缺少可靠时间时必须读取详情。

X 签名初始化同样消耗接口预算，仅访问规定的 HTTPS 源，Cookie 不发给静态资源域；跳转逐次验证，适配器不自行重试。自托管来源只豁免固定运维 origin，不能扩散到原文或模型给出的地址。Backend 按来源接口请求计数，不把远端浏览器的页面资源当作独立接口调用。

字幕只采纳响应中已有文字，字幕 URL 和视频链接不代表读过字幕或看过视频。

## 恢复与验证

使用记录按来源和消息去重，消息已清理时按实际使用时间保留独立证据。

批次采集检查点在失败后重建，采集窗口结束后不补旧批次；已正式保留的发现跨日可供查询。核对预算、修订失效、原文范围与引用；真实平台字段及链接须用有效个人登录验证。
