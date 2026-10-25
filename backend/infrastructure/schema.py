"""装配时补齐账户内关系约束；迁移保存最终展开后的静态定义。"""

from sqlalchemy import BigInteger, ForeignKeyConstraint, MetaData, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB


def finalize_owned_relations(metadata: MetaData) -> None:
    """相同账户内的实体引用由联合外键保护，而非仅依赖应用过滤。"""
    for table in metadata.tables.values():
        for column in table.c:
            if isinstance(column.type, JSONB) and column.nullable:
                column.type.none_as_null = True
        owner = "user_id" if "user_id" in table.c else "owner_id"
        if (
            owner in table.c
            and "source_key" in table.c
            and not any(
                isinstance(constraint, UniqueConstraint)
                and {c.name for c in constraint.columns} == {owner, "source_key"}
                for constraint in table.constraints
            )
        ):
            table.append_constraint(UniqueConstraint(owner, "source_key", name=f"uq_{table.name}_source"))
        if (
            owner in table.c
            and "id" in table.c
            and not any(
                isinstance(constraint, UniqueConstraint) and tuple(c.name for c in constraint.columns) == (owner, "id")
                for constraint in table.constraints
            )
        ):
            table.append_constraint(UniqueConstraint(owner, "id", name=f"uq_{table.name}_owner_id"))
    for table in metadata.tables.values():
        owner = "user_id" if "user_id" in table.c else "owner_id"
        for constraint in tuple(table.foreign_key_constraints):
            for element in constraint.elements:
                if isinstance(element.column.type, BigInteger):
                    element.parent.type = BigInteger()
            if owner not in table.c or len(constraint.elements) != 1:
                continue
            element = constraint.elements[0]
            parent = element.column.table
            column = element.parent.name
            parent_owner = "user_id" if "user_id" in parent.c else "owner_id"
            if column == owner or element.column.name != "id" or parent_owner not in parent.c:
                continue
            name = f"fk_{table.name}_{column}_owner"
            if any(c.name == name for c in table.constraints):
                continue
            if any(
                set(c.column_keys) == {owner, column} and c.referred_table is parent
                for c in table.foreign_key_constraints
            ):
                continue
            # 可空归属的联合外键会被 MATCH SIMPLE 跳过；保留实体自身的存在性约束。
            if not table.c[owner].nullable:
                table.constraints.remove(constraint)
                table.foreign_keys.discard(element)
                element.parent.foreign_keys.discard(element)
            ondelete = f"SET NULL ({column})" if constraint.ondelete == "SET NULL" else constraint.ondelete
            table.append_constraint(
                ForeignKeyConstraint(
                    [owner, column],
                    [f"{parent.name}.{parent_owner}", element.target_fullname],
                    name=name,
                    ondelete=ondelete,
                    deferrable=constraint.deferrable,
                    initially=constraint.initially,
                    use_alter=constraint.use_alter,
                )
            )
