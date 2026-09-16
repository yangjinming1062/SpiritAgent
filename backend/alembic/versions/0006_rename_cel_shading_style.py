"""2D 立绘画风键 cel_shading 更名 refined_anime_cg（存量数据与列默认值同步转换）。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "cel_shading"
_NEW = "refined_anime_cg"
# prompt_json 是任意 JSON；replace 精确匹配带引号的值片段，键名与其他字段不受影响
_JSON_OLD = f'"{_OLD}"'
_JSON_NEW = f'"{_NEW}"'


def upgrade() -> None:
    for table in ("companion_outfits", "companion_2d_models"):
        op.execute(
            sa.text(
                f"UPDATE {table} SET style = '{_NEW}' WHERE style = '{_OLD}'",
            ),
        )
        op.alter_column(
            table,
            "style",
            server_default=sa.text(f"'{_NEW}'"),
        )
    op.execute(
        sa.text(
            "UPDATE avatar_assets SET prompt_json = replace(prompt_json, "
            f"'{_JSON_OLD}', '{_JSON_NEW}') WHERE prompt_json LIKE '%{_JSON_OLD}%'",
        ),
    )


def downgrade() -> None:
    for table in ("companion_outfits", "companion_2d_models"):
        op.execute(
            sa.text(
                f"UPDATE {table} SET style = '{_OLD}' WHERE style = '{_NEW}'",
            ),
        )
        op.alter_column(
            table,
            "style",
            server_default=sa.text(f"'{_OLD}'"),
        )
    op.execute(
        sa.text(
            "UPDATE avatar_assets SET prompt_json = replace(prompt_json, "
            f"'{_JSON_NEW}', '{_JSON_OLD}') WHERE prompt_json LIKE '%{_JSON_NEW}%'",
        ),
    )
