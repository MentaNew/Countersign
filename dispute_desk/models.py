"""Seller-side tables, in their own `desk` schema. Nothing here references the gateway's tables."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import JSON, DateTime, ForeignKey, MetaData, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from gateway.db.models import SessionColumns

SCHEMA = "desk"
JsonCol = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)
    type_annotation_map: ClassVar = {dict[str, Any]: JsonCol, list[Any]: JsonCol}


class Dispute(Base):
    __tablename__ = "disputes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # PayPal dispute id
    source: Mapped[str] = mapped_column(String(20))  # import | webhook | simulated
    source_verified: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str | None] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(String(60))
    stage: Mapped[str | None] = mapped_column(String(30))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capture_id: Mapped[str | None] = mapped_column(String(64))
    receipt_id: Mapped[str | None] = mapped_column(String(64))
    receipt_check: Mapped[dict[str, Any] | None]
    fulfillment: Mapped[dict[str, Any] | None]  # carrier + tracking entered by the seller
    packet: Mapped[dict[str, Any]]
    raw: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Action(Base):
    """What the seller sent to PayPal, and what PayPal answered."""

    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    dispute_id: Mapped[str] = mapped_column(ForeignKey("disputes.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # provide_evidence | make_offer | accept_claim
    by: Mapped[str] = mapped_column(String(100))
    request: Mapped[dict[str, Any]]
    response: Mapped[dict[str, Any]]
    ok: Mapped[bool]
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # PayPal event id
    event_type: Mapped[str] = mapped_column(String(80))
    verified: Mapped[bool]
    dispute_id: Mapped[str | None] = mapped_column(String(64))
    body: Mapped[dict[str, Any]]
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


async def create_desk_schema(engine: AsyncEngine, drop: bool = False) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        if drop:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


class SellerSession(SessionColumns, Base):
    """Seller staff sign-ins (same rules as the buyer's: hashed token, idle and maximum age)."""

    __tablename__ = "seller_sessions"
