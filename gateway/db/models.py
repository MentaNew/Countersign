from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import JSON, DateTime, ForeignKey, MetaData, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonCol = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


SCHEMA = "gateway"  # buyer side; the seller-side dispute desk has its own "desk" schema


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)
    type_annotation_map: ClassVar = {dict[str, Any]: JsonCol, list[Any]: JsonCol}


class Intent(Base):
    __tablename__ = "intents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_text: Mapped[str] = mapped_column(Text)
    mandate: Mapped[dict[str, Any]]
    # pending_user -> active | rejected; active -> revoked
    status: Mapped[str] = mapped_column(String(20), default="pending_user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(100))
    signature: Mapped[str | None] = mapped_column(Text)
    key_id: Mapped[str | None] = mapped_column(String(32))


class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("intents.id"), index=True)
    merchant_id: Mapped[str] = mapped_column(String(100))
    lines: Mapped[list[Any]]
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    cart_hash: Mapped[str] = mapped_column(String(64))
    context_texts: Mapped[list[Any]]
    verdict: Mapped[dict[str, Any]]
    decision: Mapped[str] = mapped_column(String(10))
    # blocked | pending_approval | rejected | approval_expired | authorized | ordered | captured | failed
    status: Mapped[str] = mapped_column(String(20))
    approval: Mapped[dict[str, Any] | None]
    approval_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_id: Mapped[str | None] = mapped_column(String(32))
    paypal_order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    paypal_status: Mapped[str | None] = mapped_column(String(40))
    payer_action_url: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id"), unique=True)
    body: Mapped[dict[str, Any]]
    signature: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LogEntry(Base):
    __tablename__ = "log_entries"

    seq: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    event: Mapped[str] = mapped_column(String(40), index=True)
    intent_id: Mapped[str | None] = mapped_column(String(32), index=True)
    purchase_id: Mapped[str | None] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, Any]]
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
