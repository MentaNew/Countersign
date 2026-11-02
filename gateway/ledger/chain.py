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


def entry_hash_v2(seq: int, at: datetime, event: str, payload_hash: str, prev_hash: str) -> str:
    body = {
        "v": 2,
        "seq": seq,
        "at": at.astimezone(UTC).isoformat(),
        "event": event,
        "payload_hash": payload_hash,
        "prev_hash": prev_hash,
    }
    return sha256_hex(canonical_json(body))


def payload_digest(payload: dict[str, Any]) -> str:
    return sha256_hex(canonical_json(payload))


def recompute(e: LogEntry) -> str:
    """The hash an entry should have, from its stored contents."""
    if e.hash_version == 2:
        return entry_hash_v2(e.seq, e.at, e.event, payload_digest(e.payload), e.prev_hash)
    return entry_hash(e.seq, e.at, e.event, e.payload, e.prev_hash)


def link(e: LogEntry) -> dict[str, Any]:
    """Public view of an entry: enough to check the chain, nothing private (v2 only)."""
    return {"seq": e.seq, "at": e.at.astimezone(UTC).isoformat(), "event": e.event,
            "payload_hash": e.payload_hash, "prev_hash": e.prev_hash, "hash": e.hash}


def verify_links(links: list[dict[str, Any]]) -> bool:
    """Check consecutive public links (as returned by `link`) re-hash and connect."""
    prev = None
    for ln in links:
        at = datetime.fromisoformat(ln["at"])
        if ln["hash"] != entry_hash_v2(ln["seq"], at, ln["event"], ln["payload_hash"],
                                       ln["prev_hash"]):
            return False
        if prev is not None and (ln["prev_hash"] != prev["hash"] or ln["seq"] != prev["seq"] + 1):
            return False
        prev = ln
    return True


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
    digest = payload_digest(payload)
    entry = LogEntry(
        seq=seq,
        at=at,
        event=event,
        intent_id=intent_id,
        purchase_id=purchase_id,
        payload=payload,
        prev_hash=prev,
        hash=entry_hash_v2(seq, at, event, digest, prev),
        hash_version=2,
        payload_hash=digest,
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
            or e.hash != recompute(e)
            or (e.hash_version == 2 and e.payload_hash != payload_digest(e.payload))
        ):
            return False, e.seq, count
        prev = e.hash
        expected_seq += 1
    return True, None, count


async def count(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(LogEntry))).scalar_one()


def _json_roundtrip(obj: Any) -> Any:
    return json.loads(canonical_json(obj))
