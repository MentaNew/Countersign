"""Append-only, hash-chained event log. Each entry commits to the previous entry's hash."""

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.canonical import canonical_json, sha256_hex
from gateway.db.models import LogEntry

GENESIS = "0" * 64
_LOCK_KEY = 7_340_001  # pg advisory lock id serialising appends


def entry_hash(seq: int, at: datetime, event: str, payload: dict[str, Any], prev_hash: str) -> str:
    body = {
        "seq": seq,
        "at": at.astimezone(UTC).isoformat(),
        "event": event,
        "payload": payload,
        "prev_hash": prev_hash,
    }
    return sha256_hex(canonical_json(body))


async def append(
    session: AsyncSession,
    event: str,
    payload: dict[str, Any],
    *,
    intent_id: str | None = None,
    purchase_id: str | None = None,
) -> LogEntry:
    """Append within the caller's transaction; the advisory lock is held until it commits."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
    last = (
        await session.execute(select(LogEntry).order_by(LogEntry.seq.desc()).limit(1))
    ).scalar_one_or_none()
    seq = (last.seq + 1) if last else 1
    prev = last.hash if last else GENESIS
    # Microsecond precision survives the Postgres round-trip, so the hash can be recomputed.
    at = datetime.now(UTC)
    # Normalise through JSON so what we hash is exactly what JSONB will give back.
    payload = _json_roundtrip(payload)
    entry = LogEntry(
        seq=seq,
        at=at,
        event=event,
        intent_id=intent_id,
        purchase_id=purchase_id,
        payload=payload,
        prev_hash=prev,
        hash=entry_hash(seq, at, event, payload, prev),
    )
    session.add(entry)
    await session.flush()
    return entry


async def verify_chain(session: AsyncSession) -> tuple[bool, int | None, int]:
    """Return (ok, first_bad_seq, entries_checked)."""
    rows = (await session.execute(select(LogEntry).order_by(LogEntry.seq))).scalars()
    prev = GENESIS
    expected_seq = 1
    count = 0
    for e in rows:
        count += 1
        if (
            e.seq != expected_seq
            or e.prev_hash != prev
            or e.hash != entry_hash(e.seq, e.at, e.event, e.payload, e.prev_hash)
        ):
            return False, e.seq, count
        prev = e.hash
        expected_seq += 1
    return True, None, count


async def count(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(LogEntry))).scalar_one()


def _json_roundtrip(obj: Any) -> Any:
    return json.loads(canonical_json(obj))
