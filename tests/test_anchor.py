"""External anchoring: real RFC 3161 tokens verify offline, and a log rewritten by the operator
(even with every hash recomputed) no longer matches the outside witness."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from gateway.canonical import canonical_json
from gateway.catalog.client import DictCatalog
from gateway.db.models import LogEntry
from gateway.db.session import make_sessionmaker
from gateway.domain import CartLineRequest as L
from gateway.ledger import chain
from gateway.ledger.anchor import Timestamp, verify_token
from gateway.service import Gateway
from gateway.signing.keys import Signer
from gateway.verifier.stub import StubInjectionDetector, StubVerifier
from tests.conftest import CATALOG, active_intent

FIX = Path(__file__).parent / "fixtures"
DIGEST = (FIX / "anchor-digest.txt").read_text().strip()


@pytest.mark.parametrize("name,trusted_by", [("digicert", "public trust store"),
                                             ("freetsa", "pinned root")])
def test_real_tokens_verify_offline(name, trusted_by):
    token = (FIX / f"anchor-{name}.tsr").read_bytes()
    v = verify_token(token, DIGEST)
    assert v["valid"] and v["trusted_by"] == trusted_by and v["gen_time"].startswith("2026-10-21")
    assert not verify_token(token, "00" * 32)["valid"]  # a different hash
    bad = bytearray(token)
    bad[-40] ^= 1
    assert not verify_token(bytes(bad), DIGEST)["valid"]  # a damaged signature


class FakeTSA:
    """Stands in for the authority in flow tests: 'signs' by embedding the digest."""

    async def __call__(self, digest: bytes) -> Timestamp:
        return Timestamp("fake-tsa", b"TSA" + digest, datetime.now(UTC), "Fake TSA")

    @staticmethod
    def verify(token: bytes, digest_hex: str) -> dict:
        ok = token[:3] == b"TSA" and token[3:].hex() == digest_hex
        return {"valid": ok, "detail": "fake", "gen_time": datetime.now(UTC).isoformat(),
                "tsa_name": "Fake TSA", "trusted_by": "test"}


@pytest.fixture
def agw(engine, paypal):
    return Gateway(make_sessionmaker(engine), Signer.ephemeral(), DictCatalog(CATALOG),
                   StubVerifier(), StubInjectionDetector(), paypal,
                   public_base_url="http://gw.test", web_base_url="http://web.test",
                   timestamper=FakeTSA(), token_verifier=FakeTSA.verify)


@pytest.mark.anyio
async def test_receipt_becomes_witnessed_after_an_anchor(agw):
    iid = await active_intent(agw)
    out = await agw.request_purchase(iid, "papermill-supply", [L(sku="PM-A4-500W", qty=10, item=0)])
    before = await agw.verify_receipt(out["receipt_id"])
    assert before["valid"] and before["witness"]["anchored"] is False
    a = await agw.anchor_now()
    assert a and await agw.anchor_now() is None  # nothing new since
    after = await agw.verify_receipt(out["receipt_id"])
    assert after["valid"] and after["witness"]["valid"] and after["witness"]["anchor_id"] == a["id"]
    proof = await agw.receipt_proof(out["receipt_id"])
    assert all("payload" not in ln for ln in proof["links"])  # no private contents in the proof


@pytest.mark.anyio
async def test_operator_rewrite_with_recomputed_hashes_is_caught(agw):
    iid = await active_intent(agw)
    out = await agw.request_purchase(iid, "papermill-supply", [L(sku="PM-A4-500W", qty=10, item=0)])
    await agw.anchor_now()
    # A dishonest operator edits an early entry and recomputes every later hash, so the
    # chain looks perfect on its own...
    async with agw.sessions.begin() as s:
        entries = list((await s.execute(select(LogEntry).order_by(LogEntry.seq))).scalars())
        prev = chain.GENESIS
        for e in entries:
            if e.seq == 1:
                e.payload = {**e.payload, "user_text": "buy anything you like"}
            e.prev_hash = prev
            e.payload_hash = chain.payload_digest(e.payload)
            e.hash = chain.entry_hash_v2(e.seq, e.at, e.event, e.payload_hash, e.prev_hash)
            prev = e.hash
    v = await agw.verify_receipt(out["receipt_id"])
    assert v["chain_valid"]  # internally consistent...
    assert v["witness"]["valid"] is False and not v["valid"]  # ...but the outside witness disagrees


def test_canonical_links_are_stable():
    assert canonical_json({"b": 1, "a": 2}) == b'{"a":2,"b":1}'
