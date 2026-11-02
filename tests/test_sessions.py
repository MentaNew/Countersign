"""Sign-in sessions: HttpOnly cookie, CSRF header on changes, idle and absolute expiry, sign-out,
ending other sessions, and passwordless sign-in with a passkey."""

from datetime import timedelta

import httpx
import pytest

from gateway.catalog.client import DictCatalog
from gateway.config import Settings
from gateway.logins import COOKIE, CSRF_HEADER, now
from gateway.main import create_app
from gateway.verifier.stub import StubInjectionDetector, StubVerifier
from tests.conftest import CATALOG, FakePayPal
from tests.soft_passkey import SoftPasskey

pytestmark = pytest.mark.anyio
RP, ORIGIN = "localhost", "http://localhost:5173"
CSRF = {CSRF_HEADER: "1"}


@pytest.fixture
async def app_client(engine, tmp_path):
    s = Settings(_env_file=None, agent_api_key="agent-secret", user_password="user-secret",
                 verifier="stub", signing_key_path=str(tmp_path / "k.pem"),
                 anchor_interval_seconds=0)
    app = create_app(s, engine=engine, catalog=DictCatalog(CATALOG), paypal=FakePayPal(),
                     verifier=StubVerifier(), injection=StubInjectionDetector())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
            yield app, c


async def sign_in(c) -> httpx.Response:
    r = await c.post("/api/login", json={"password": "user-secret"})
    assert r.status_code == 200
    return r


async def test_cookie_is_httponly_strict_and_works(app_client):
    _, c = app_client
    r = await sign_in(c)
    cookie = r.headers["set-cookie"].lower()
    assert f"{COOKIE}=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert (await c.get("/api/session")).json()["user"] == "demo-user"
    # A change with the cookie alone is refused (another site could send the cookie)...
    assert (await c.post("/api/agents/stop")).status_code == 403
    # ...and works with the header this web app adds.
    assert (await c.post("/api/agents/stop", headers=CSRF)).status_code == 200


async def test_bearer_token_for_scripts_needs_no_csrf_header(app_client):
    _, c = app_client
    tok = (await sign_in(c)).json()["token"]
    c.cookies.clear()
    r = await c.post("/api/agents/stop", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200


async def test_sign_out_and_end_other_sessions(app_client):
    _, c = app_client
    other = (await sign_in(c)).json()
    c.cookies.clear()
    await sign_in(c)
    listed = (await c.get("/api/sessions")).json()
    assert len(listed) == 2 and sum(x["current"] for x in listed) == 1
    assert (await c.post("/api/sessions/end-others", headers=CSRF)).json()["ended"] == 1
    r = await c.get("/api/session", headers={"Authorization": f"Bearer {other['token']}"})
    assert r.status_code == 401
    assert (await c.post("/api/logout", headers=CSRF)).status_code == 200
    c.cookies.clear()
    assert (await c.get("/api/session")).status_code == 401


async def test_idle_and_absolute_expiry(app_client):
    app, _ = app_client
    logins = app.state.logins
    token, row = await logins.create("demo-user", "password")
    assert await logins.resolve(token)
    from sqlalchemy import update

    from gateway.db.models import UserSession
    async with logins.db.begin() as s:  # pretend it has been idle for 3 hours
        await s.execute(update(UserSession).where(UserSession.id == row.id)
                        .values(last_seen_at=now() - timedelta(hours=3)))
    assert await logins.resolve(token) is None
    token2, row2 = await logins.create("demo-user", "password")
    async with logins.db.begin() as s:  # past the absolute maximum
        await s.execute(update(UserSession).where(UserSession.id == row2.id)
                        .values(expires_at=now() - timedelta(seconds=1)))
    assert await logins.resolve(token2) is None


async def test_passwordless_sign_in_with_a_passkey(app_client):
    _, c = app_client
    await sign_in(c)
    dev = SoftPasskey(RP, ORIGIN)
    opts = (await c.post("/api/passkeys/register/options", headers=CSRF)).json()
    r = await c.post("/api/passkeys/register", headers=CSRF,
                     json={"credential": dev.register(opts), "label": "laptop"})
    assert r.status_code == 200, r.text
    await c.post("/api/logout", headers=CSRF)
    c.cookies.clear()

    o = (await c.post("/api/login/passkey/options")).json()
    assert o["statement"]["kind"] == "sign_in"
    r = await c.post("/api/login/passkey", json={"nonce": o["nonce"],
                                                 "credential": dev.sign(o["options"])})
    assert r.status_code == 200 and r.json()["method"] == "passkey", r.text
    assert (await c.get("/api/session")).status_code == 200
    # The same signature cannot be replayed for another sign-in.
    r = await c.post("/api/login/passkey", json={"nonce": o["nonce"],
                                                 "credential": dev.sign(o["options"])})
    assert r.status_code == 401


async def test_security_headers(app_client):
    _, c = app_client
    r = await c.get("/api/public-key")
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"


async def test_waiting_lists_what_needs_the_person(app_client):
    _, c = app_client
    await sign_in(c)
    assert (await c.get("/api/waiting")).json() == []
    r = await c.post("/mcp/", headers={
        "Authorization": "Bearer agent-secret", "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "confirm_intent", "arguments": {
                "user_text": "paper", "items": [{"description": "A4 paper", "qty": 10}],
                "max_total": {"amount": "60", "currency": "USD"},
                "merchants": ["papermill-supply"], "expires_at": "2099-01-01T00:00:00Z"}}})
    assert r.status_code == 200
    [w] = (await c.get("/api/waiting")).json()
    assert w["kind"] == "intent" and w["path"].startswith("/intents/")
