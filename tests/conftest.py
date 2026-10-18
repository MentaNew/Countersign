"""Tests run against a separate Postgres database (guardrail_test), with an offline verifier and
a fake PayPal: no network calls to Groq or PayPal."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from gateway.catalog.client import DictCatalog
from gateway.config import get_settings
from gateway.db.session import create_schema, make_engine, make_sessionmaker
from gateway.domain import Mandate
from gateway.service import Gateway
from gateway.signing.keys import Signer
from gateway.verifier.stub import StubInjectionDetector, StubVerifier

CATALOG = json.loads(
    (Path(__file__).resolve().parent.parent / "demo" / "merchant" / "catalog.json").read_text()
)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def test_db_url() -> str:
    url = get_settings().test_database_url
    dbname = url.rsplit("/", 1)[1]
    admin = url.rsplit("/", 1)[0].replace("postgresql+psycopg", "postgresql") + "/postgres"
    try:
        with psycopg.connect(admin, autocommit=True) as c:
            if not c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (dbname,)).fetchone():
                c.execute(f'CREATE DATABASE "{dbname}"')
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not reachable ({e}); run `docker compose up -d`")
    return url


class FakePayPal:
    def __init__(self, status: str = "PAYER_ACTION_REQUIRED", fail: bool = False):
        self.status = status
        self.fail = fail
        self.orders: list[dict] = []
        self.captured: list[str] = []

    async def create_order(self, *, lines, currency, merchant_name, custom_id, invoice_id,
                           return_url, cancel_url, vault_id=""):
        from gateway.paypal.client import PayPalError

        if self.fail:
            raise PayPalError(422, {"name": "UNPROCESSABLE_ENTITY", "message": "test failure"})
        order = {
            "id": f"PP{len(self.orders) + 1:05d}",
            "status": self.status,
            "custom_id": custom_id,
            "invoice_id": invoice_id,
            "lines": [ln.sku for ln in lines],
            "links": [{"rel": "payer-action", "href": "https://sandbox.paypal.test/approve"}],
        }
        self.orders.append(order)
        return order

    async def capture_order(self, order_id: str):
        self.captured.append(order_id)
        return {"id": order_id, "status": "COMPLETED"}

    async def get_order(self, order_id: str):
        return {"id": order_id}


@pytest.fixture
async def engine(test_db_url):
    eng = make_engine(test_db_url)
    await create_schema(eng, drop=True)
    yield eng
    await eng.dispose()


@pytest.fixture
def paypal():
    return FakePayPal()


@pytest.fixture
def verifier():
    return StubVerifier()


@pytest.fixture
def gw(engine, paypal, verifier) -> Gateway:
    return Gateway(
        make_sessionmaker(engine),
        Signer.ephemeral(),
        DictCatalog(CATALOG),
        verifier,
        StubInjectionDetector(),
        paypal,
        public_base_url="http://gw.test",
        web_base_url="http://web.test",
    )


def paper_mandate(**kw) -> Mandate:
    base = dict(
        items=[{"description": "A4 copy paper, 500 sheets, white", "qty": 10}],
        max_total={"amount": "60.00", "currency": "USD"},
        merchants=["papermill-supply", "northside-office"],
        approval_above={"amount": "40.00", "currency": "USD"},
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    base.update(kw)
    return Mandate.model_validate(base)


async def active_intent(gw: Gateway, **kw) -> str:
    r = await gw.confirm_intent("Buy 10 reams of A4 paper, max $60", paper_mandate(**kw))
    await gw.decide_intent(r["intent_id"], "demo-user", approve=True)
    return r["intent_id"]
