"""Hosted-deployment behaviour (P1.6): keys from secrets, database URLs, rate limits,
placeholder secrets refused on a public address."""

import base64

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gateway.catalog.client import DictCatalog
from gateway.config import Settings
from gateway.hosting import db_url, pem_bytes, refuse_placeholders
from gateway.main import create_app
from gateway.signing.keys import Signer
from gateway.verifier.stub import StubInjectionDetector, StubVerifier
from tests.conftest import CATALOG, FakePayPal


def pem(key) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def test_database_urls_from_hosts_get_our_driver():
    assert db_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert db_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert db_url("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"


def test_keys_from_secret_settings_in_any_paste_format():
    key = Ed25519PrivateKey.generate()
    raw = pem(key)
    want = Signer(key).key_id
    for value in (raw.decode(), raw.decode().replace("\n", "\\n"), base64.b64encode(raw).decode()):
        assert Signer.from_pem(pem_bytes(value)).key_id == want


def test_placeholder_secrets_refused_only_on_a_public_address():
    refuse_placeholders("http://localhost:8000", user_password="change-me-user")  # fine locally
    with pytest.raises(RuntimeError, match="USER_PASSWORD"):
        refuse_placeholders("https://gw.example.com", user_password="change-me-user",
                            agent_api_key="x" * 40)
    with pytest.raises(RuntimeError, match="AGENT_API_KEY"):
        refuse_placeholders("https://gw.example.com", agent_api_key="short",
                            user_password="y" * 20)
    refuse_placeholders("https://gw.example.com", agent_api_key="x" * 40, user_password="y" * 20)


@pytest.mark.anyio
async def test_hosted_gateway_uses_secret_keys_and_limits_login_guesses(engine):
    ed, es = Ed25519PrivateKey.generate(), ec.generate_private_key(ec.SECP256R1())
    s = Settings(_env_file=None, agent_api_key="a" * 32, user_password="u" * 20,
                 verifier="stub", anchor_interval_seconds=0,
                 public_base_url="https://gw.example.com", web_base_url="https://app.example.com",
                 signing_key_pem=base64.b64encode(pem(ed)).decode(),
                 ap2_signing_key_pem=pem(es).decode(), signing_key_path="/nonexistent/k.pem")
    app = create_app(s, engine=engine, catalog=DictCatalog(CATALOG), paypal=FakePayPal(),
                     verifier=StubVerifier(), injection=StubInjectionDetector())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
            pk = (await c.get("/api/public-key")).json()
            assert pk["key_id"] == Signer(ed).key_id
            codes = [(await c.post("/api/login", json={"password": "guess"})).status_code
                     for _ in range(12)]
            assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]


@pytest.mark.anyio
async def test_hosted_gateway_refuses_example_secrets(engine):
    s = Settings(_env_file=None, public_base_url="https://gw.example.com")
    with pytest.raises(RuntimeError, match="refusing to start"):
        create_app(s, engine=engine)
