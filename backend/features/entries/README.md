# 伙伴内容

拥有 `companion_entries`、`entry_links`、`entry_assets` 和 `entry_comments`。动态和日记使用同一内容实体，发布策略分别归 posts 与 journal；消费模块仅使用 `contracts.py` 的快照及 `queries.py`、`service.py` 的公开操作。

内容、日记来源和评论回复的关系约束见 [字段字典](../../../docs/DATABASE_FIELDS.md#动态与日记)。业务通过公开入口维护关联，不能在模型正文中推断关系。

列表只读取内容、附件引用及评论数量，不自动加载评论正文；评论使用独立游标分页。制作、评论生成与记忆索引不属于内容读取操作。事务由调用方提交，内容与 outbox、发布结果在同一事务生效。

恢复后通过 `ports.py` 登记的投影回调重建日记索引；已发布正文是事实来源。评论在途状态恢复为可重试失败；包级校验及选择范围见 [备份契约](../../../docs/PROTOCOL.md#备份校验与覆盖恢复)。
