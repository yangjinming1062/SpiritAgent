"""消息批次的派生统计 DDL；迁移冻结生成结果，元数据测试安装同一套触发器。"""

from sqlalchemy import BigInteger, MetaData, column, event, func, select, table, tuple_
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection
from sqlalchemy.orm import aliased
from sqlalchemy.sql import Select

from .formatting import visible_bubble_count
from .models import Conversation, Message

_COUNTERS = ("visible_message_count", "input_token_count", "output_token_count", "tool_call_count")
_MESSAGE_FIELDS = (
    "conversation_id",
    "role",
    "subtype",
    "content",
    "content_type",
    "prompt_tokens",
    "completion_tokens",
    "tool_calls",
    "media_json",
    "reply_json",
)


def _sql(statement: Select) -> str:
    dialect = postgresql.dialect(paramstyle="named")
    # 离线编译采用 PostgreSQL 的 standard_conforming_strings 默认值，不重复转义正则反斜杠。
    dialect._backslash_escapes = False
    return str(statement.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))


def _aggregate(source: str, *, sign: int = 1, compare: str | None = None, changed_only: bool = False) -> str:
    rows = table(source, *(column(field.name, field.type) for field in Message.__table__.c)).alias("messages")
    message = aliased(Message, rows, adapt_on_names=True)
    statement = (
        select(
            message.conversation_id,
            (func.sum(visible_bubble_count(message)) * sign).label(_COUNTERS[0]),
            (func.coalesce(func.sum(message.prompt_tokens), 0) * sign).label(_COUNTERS[1]),
            (func.coalesce(func.sum(message.completion_tokens), 0) * sign).label(_COUNTERS[2]),
            (func.count(message.id).filter(message.tool_calls.is_not(None)) * sign).label(_COUNTERS[3]),
        )
        .select_from(rows)
        .join(Conversation, Conversation.id == message.conversation_id)
    )
    if compare:
        previous = table(compare, *(column(field.name, field.type) for field in Message.__table__.c)).alias("previous")
        statement = statement.outerjoin(previous, previous.c.id == rows.c.id).where(
            tuple_(*(rows.c[field] for field in _MESSAGE_FIELDS)).is_distinct_from(
                tuple_(*(previous.c[field] for field in _MESSAGE_FIELDS))
            )
        )
    if changed_only:
        changed = table("changed", column("id", BigInteger()))
        statement = statement.where(message.conversation_id.in_(select(changed.c.id)))
    return _sql(statement.group_by(message.conversation_id))


def conversation_statistics_ddl() -> tuple[str, ...]:
    """完整、可独立执行的 PostgreSQL 函数及触发器，禁止迁移运行时导入模型。"""
    statements = []
    for operation in ("insert", "update", "delete"):
        names = (
            ("new_rows", "old_rows")
            if operation == "update"
            else ("new_rows",)
            if operation == "insert"
            else ("old_rows",)
        )
        identifiers = " UNION ".join(f"SELECT conversation_id FROM {name}" for name in names)
        aggregates = " UNION ALL ".join(
            _aggregate(
                name,
                sign=-1 if name == "old_rows" else 1,
                compare=("old_rows" if name == "new_rows" else "new_rows") if operation == "update" else None,
            )
            for name in names
        )
        assignments = ", ".join(f"{field} = target.{field} + delta.{field}" for field in _COUNTERS)
        sums = ", ".join(f"sum({field}) AS {field}" for field in _COUNTERS)
        changed = " OR ".join(f"delta.{field} != 0" for field in _COUNTERS)
        statements.append(f"""CREATE OR REPLACE FUNCTION conversation_statistics_{operation}() RETURNS trigger
LANGUAGE plpgsql AS $statistics$
BEGIN
    -- 先按会话顺序取锁，再用最新预设/父会话语义计算增量；与元数据重算串行。
    PERFORM id FROM conversations WHERE id IN ({identifiers}) ORDER BY id FOR NO KEY UPDATE;
    WITH batches AS ({aggregates}), delta AS (
        SELECT conversation_id, {sums} FROM batches GROUP BY conversation_id
    )
    UPDATE conversations AS target SET {assignments}
    FROM delta WHERE target.id = delta.conversation_id AND ({changed});
    RETURN NULL;
END
$statistics$""")
        transitions = " ".join(f"{'OLD' if name == 'old_rows' else 'NEW'} TABLE AS {name}" for name in names)
        statements.append(
            f"CREATE TRIGGER messages_statistics_{operation} AFTER {operation.upper()} ON messages REFERENCING {transitions} FOR EACH STATEMENT EXECUTE FUNCTION conversation_statistics_{operation}()"
        )
    zero = ", ".join(f"{field} = 0" for field in _COUNTERS)
    nonzero = " OR ".join(f"{field} != 0" for field in _COUNTERS)
    statements.extend(
        (
            f"""CREATE OR REPLACE FUNCTION conversation_statistics_truncate() RETURNS trigger
LANGUAGE plpgsql AS $statistics$
BEGIN
    UPDATE conversations SET {zero} WHERE {nonzero};
    RETURN NULL;
END
$statistics$""",
            "CREATE TRIGGER messages_statistics_truncate AFTER TRUNCATE ON messages FOR EACH STATEMENT EXECUTE FUNCTION conversation_statistics_truncate()",
        )
    )
    changed = """SELECT current.id FROM new_conversations AS current JOIN old_conversations AS previous USING (id)
        WHERE (current.parent_id, current.system_preset_id) IS DISTINCT FROM (previous.parent_id, previous.system_preset_id)"""
    assignments = ", ".join(f"{field} = coalesce(totals.{field}, 0)" for field in _COUNTERS)
    statements.extend(
        (
            f"""CREATE OR REPLACE FUNCTION conversation_statistics_context() RETURNS trigger
LANGUAGE plpgsql AS $statistics$
BEGIN
    IF NOT EXISTS ({changed}) THEN RETURN NULL; END IF;
    WITH changed AS ({changed}), totals AS ({_aggregate("messages", changed_only=True)})
    UPDATE conversations AS target SET {assignments}
    FROM changed LEFT JOIN totals ON totals.conversation_id = changed.id
    WHERE target.id = changed.id;
    RETURN NULL;
END
$statistics$""",
            "CREATE TRIGGER conversations_statistics_context AFTER UPDATE ON conversations REFERENCING OLD TABLE AS old_conversations NEW TABLE AS new_conversations FOR EACH STATEMENT EXECUTE FUNCTION conversation_statistics_context()",
        )
    )
    return tuple(statements)


def conversation_statistics_drop_ddl() -> tuple[str, ...]:
    operations = ("insert", "update", "delete", "truncate", "context")
    triggers = tuple(
        f"DROP TRIGGER IF EXISTS {'conversations' if operation == 'context' else 'messages'}_statistics_{operation} ON {'conversations' if operation == 'context' else 'messages'}"
        for operation in operations
    )
    return (*triggers, *(f"DROP FUNCTION IF EXISTS conversation_statistics_{operation}()" for operation in operations))


def _install(_metadata: MetaData, connection: Connection, **kwargs: object) -> None:
    names = {item.name for item in kwargs.get("tables", [])}
    if connection.dialect.name == "postgresql" and {"conversations", "messages"} <= names:
        for statement in conversation_statistics_ddl():
            connection.exec_driver_sql(statement)


def _remove(_metadata: MetaData, connection: Connection, **kwargs: object) -> None:
    names = {item.name for item in kwargs.get("tables", [])}
    if connection.dialect.name == "postgresql" and {"conversations", "messages"} <= names:
        for statement in conversation_statistics_drop_ddl():
            connection.exec_driver_sql(statement)


def register_conversation_statistics(metadata: MetaData) -> None:
    if metadata.info.get("conversation_statistics_registered"):
        return
    metadata.info["conversation_statistics_registered"] = True
    event.listen(metadata, "after_create", _install)
    event.listen(metadata, "before_drop", _remove)
