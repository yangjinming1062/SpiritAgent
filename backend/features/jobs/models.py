"""任务受理身份；业务详情使用基础设施的共享任务存储契约。"""

from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import BigInteger, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class JobRequest(ModelBase, TimestampMixin):
    __tablename__ = "job_requests"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_job_requests_user_request"),
        ForeignKeyConstraint(
            ["user_id", "job_id"], ["jobs.user_id", "jobs.id"], ondelete="CASCADE", name="fk_job_requests_owned_job"
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    job_id: Mapped[int] = mapped_column(BigInteger, index=True)
    request_id: Mapped[str] = mapped_column(String(256))
    fingerprint: Mapped[str] = mapped_column(String(64))


class JobAsset(ModelBase):
    __tablename__ = "job_assets"
    __table_args__ = (
        UniqueConstraint("job_id", "asset_id", "role", name="uq_job_assets_role"),
        ForeignKeyConstraint(
            ["user_id", "job_id"], ["jobs.user_id", "jobs.id"], ondelete="CASCADE", name="fk_job_assets_owned_job"
        ),
        ForeignKeyConstraint(
            ["user_id", "asset_id"],
            ["assets.user_id", "assets.id"],
            ondelete="RESTRICT",
            name="fk_job_assets_owned_asset",
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    job_id: Mapped[int] = mapped_column(BigInteger, index=True)
    asset_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(32))
