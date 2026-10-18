import pytest
from sqlalchemy import update

from gateway.db.models import LogEntry
from gateway.db.session import make_sessionmaker
from gateway.ledger import chain
from gateway.signing.keys import Signer, verify_with_public_key

pytestmark = pytest.mark.anyio


def test_sign_verify_and_tamper(tmp_path):
    s = Signer.from_file(tmp_path / "k.pem")
    body = {"b": 2, "a": [1, "x"]}
    sig = s.sign(body)
    assert s.verify({"a": [1, "x"], "b": 2}, sig)  # key order does not matter
    assert not s.verify({"a": [1, "x"], "b": 3}, sig)
    assert verify_with_public_key(s.public_key_b64, body, sig)
    assert Signer.from_file(tmp_path / "k.pem").key_id == s.key_id  # reloads the same key
    assert (tmp_path / "k.pem").stat().st_mode & 0o077 == 0


async def test_chain_appends_and_detects_tampering(engine):
    sessions = make_sessionmaker(engine)
    async with sessions.begin() as s:
        for i in range(5):
            await chain.append(s, "e", {"i": i, "p": 0.1234, "nested": {"z": 1, "a": [1, 2]}})
    async with sessions() as s:
        assert await chain.verify_chain(s) == (True, None, 5)
    async with sessions.begin() as s:
        await s.execute(update(LogEntry).where(LogEntry.seq == 3).values(payload={"i": 99}))
    async with sessions() as s:
        ok, bad, _ = await chain.verify_chain(s)
    assert not ok and bad == 3
