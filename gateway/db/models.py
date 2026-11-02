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
    # Passkey signature by the user's own device over the approval statement (or None).
    user_signature: Mapped[dict[str, Any] | None]
    agent_id: Mapped[str | None] = mapped_column(String(32), index=True)  # who proposed it


class Agent(Base):
    """One shopping agent and its key. Only a hash of the key is stored; the key is shown once."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    key_hint: Mapped[str] = mapped_column(String(16))  # first characters, to recognise a key
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | revoked
    # "mcp": talks to the gateway directly; "browser": shops on normal websites through the
    # checkout guard, which holds this key and is the only one allowed to submit page carts.
    kind: Mapped[str | None] = mapped_column(String(20))
    spend_cap: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # per rolling 30 days
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    created_by: Mapped[str | None] = mapped_column(String(100))
    user_signature: Mapped[dict[str, Any] | None]  # passkey approval of its creation
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Control(Base):
    """Gateway-wide switches, e.g. "agents_paused" (the stop-all-agents button)."""

    __tablename__ = "controls"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[dict[str, Any]]
    updated_by: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Passkey(Base):
    """A user's registered WebAuthn credential. The private key never leaves their device."""

    __tablename__ = "passkeys"

    credential_id: Mapped[str] = mapped_column(String(512), primary_key=True)  # base64url
    user: Mapped[str] = mapped_column(String(100), index=True)
    public_key: Mapped[str] = mapped_column(Text)  # COSE key, base64url
    sign_count: Mapped[int] = mapped_column(default=0)
    aaguid: Mapped[str | None] = mapped_column(String(64))
    label: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("intents.id"), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(32), index=True)
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
    merchant_offer: Mapped[dict[str, Any] | None]  # the merchant's signed cart offer
    # "page": read from a normal shop's checkout by the checkout guard (not signed by the shop)
    cart_source: Mapped[str | None] = mapped_column(String(20))
    page_cart: Mapped[dict[str, Any] | None]  # what the guard read (without the screenshot)
    page_screenshot: Mapped[str | None] = mapped_column(Text)  # base64 PNG shown to the user
    checkout_permit: Mapped[dict[str, Any] | None]  # signed go-ahead the guard needs to pay
    # Exact exchanges with the model checks behind this decision (request + raw response text)
    model_exchanges: Mapped[list[Any] | None]
    # Signed record of the payment made at the shop's own checkout (page carts)
    payment_confirmation: Mapped[dict[str, Any] | None]
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


class MerchantKey(Base):
    """A merchant's signing key, pinned the first time the gateway sees it."""

    __tablename__ = "merchant_keys"

    merchant_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    public_key: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(32))
    source: Mapped[str | None] = mapped_column(Text)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    # v2 entries hash the payload's digest instead of the payload, so an outsider can follow
    # the chain without seeing private contents. None = v1 (entries before Oct 21).
    hash_version: Mapped[int | None]
    payload_hash: Mapped[str | None] = mapped_column(String(64))


class Anchor(Base):
    """An RFC 3161 timestamp over the chain head: outside proof the log existed at that time."""

    __tablename__ = "anchors"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    seq: Mapped[int] = mapped_column(index=True)  # log entry whose hash was timestamped
    head_hash: Mapped[str] = mapped_column(String(64))
    tsa_url: Mapped[str] = mapped_column(String(200))
    tsa_name: Mapped[str | None] = mapped_column(String(200))
    gen_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    token: Mapped[str] = mapped_column(Text)  # base64 DER TimeStampToken
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SessionColumns:
    """Columns of a sign-in session (shared by the buyer's gateway and the seller's desk)."""

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # public id, shown in the UI
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user: Mapped[str] = mapped_column(String(100), index=True)
    method: Mapped[str] = mapped_column(String(20))  # password | passkey
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    ip: Mapped[str | None] = mapped_column(String(64))


class UserSession(SessionColumns, Base):
    """A signed-in browser or script. Only a hash of the session token is stored; sessions end
    after idle time or a fixed maximum, on sign-out, or when the user ends them."""

    __tablename__ = "user_sessions"
