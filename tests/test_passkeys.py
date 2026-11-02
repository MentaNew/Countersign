"""Passkey signatures: registration, approval, and independent verification."""

import copy

import pytest

from gateway.signing.passkeys import PasskeyError, Passkeys, new_statement, verify_user_signature
from tests.soft_passkey import SoftPasskey

RP, ORIGIN = "localhost", "http://localhost:5173"


def enrolled():
    pk = Passkeys(RP, ORIGIN)
    dev = SoftPasskey(RP, ORIGIN)
    stored = pk.verify_registration("demo-user", dev.register(pk.registration_options("demo-user", [])))
    return pk, dev, stored


def test_register_approve_and_verify_offline():
    pk, dev, stored = enrolled()
    st = new_statement("cart_mandate_approval", purchase_id="pur_1", cart_hash="abc", total="38.90")
    opts = pk.approval_options("pur_1", st, [stored["credential_id"]])
    record, count = pk.verify_approval("pur_1", dev.sign(opts["options"]), stored)
    assert count == 1 and record["statement"]["cart_hash"] == "abc"
    assert verify_user_signature(record) == (True, "signed on the user's device, user verified")
    assert verify_user_signature(record, {"cart_hash": "abc"})[0]
    assert verify_user_signature(record, {"cart_hash": "other"}) == (
        False, "signed statement is about a different cart_hash")


def test_tampered_statement_or_signature_fails():
    pk, dev, stored = enrolled()
    opts = pk.approval_options("int_1", new_statement("intent_mandate_approval", intent_id="int_1",
                                                      max_total="60.00"), [stored["credential_id"]])
    record, _ = pk.verify_approval("int_1", dev.sign(opts["options"]), stored)
    forged = copy.deepcopy(record)
    forged["statement"]["max_total"] = "6000.00"
    assert verify_user_signature(forged) == (False, "signature is not over this statement")
    other = SoftPasskey(RP, ORIGIN)  # a different device's key
    swapped = {**record, "public_key": enrolled()[2]["public_key"]}
    assert not verify_user_signature(swapped)[0]
    assert other  # silence unused


def test_wrong_origin_and_replay_are_refused():
    pk, dev, stored = enrolled()
    opts = pk.approval_options("pur_2", new_statement("cart_mandate_approval", purchase_id="pur_2"),
                               [stored["credential_id"]])
    with pytest.raises(PasskeyError):
        pk.verify_approval("pur_2", dev.sign(opts["options"], origin="https://evil.example"), stored)
    with pytest.raises(PasskeyError, match="no pending"):  # the challenge was consumed
        pk.verify_approval("pur_2", dev.sign(opts["options"]), stored)


# ------------------------------------------------------------ through the gateway

from gateway.catalog.client import DictCatalog
from gateway.db.session import make_sessionmaker
from gateway.domain import CartLineRequest as L
from gateway.service import Gateway, GatewayError
from gateway.signing.keys import Signer
from gateway.verifier.stub import StubInjectionDetector, StubVerifier
from tests.conftest import CATALOG, FakePayPal, paper_mandate

pytestmark_gw = pytest.mark.anyio


@pytest.fixture
def pgw(engine):
    return Gateway(make_sessionmaker(engine), Signer.ephemeral(), DictCatalog(CATALOG),
                   StubVerifier(), StubInjectionDetector(), FakePayPal(),
                   public_base_url="http://gw.test", web_base_url=ORIGIN,
                   passkeys=Passkeys(RP, ORIGIN), require_passkey="auto")


async def enroll(gw):
    dev = SoftPasskey(RP, ORIGIN)
    await gw.register_passkey("demo-user", dev.register(await gw.passkey_registration_options("demo-user")))
    return dev


async def signed_intent(gw, dev):
    r = await gw.confirm_intent("Buy 10 reams of A4 paper, max $60", paper_mandate())
    opts = await gw.intent_approval_options(r["intent_id"], "demo-user")
    assert opts["passkey"] and opts["statement"]["intent_id"] == r["intent_id"]
    view = await gw.decide_intent(r["intent_id"], "demo-user", True, dev.sign(opts["options"]))
    assert view["status"] == "active" and view["user_signed"]
    return r["intent_id"]


@pytest.mark.anyio
async def test_password_alone_refused_once_a_passkey_exists(pgw):
    r = await pgw.confirm_intent("paper", paper_mandate())
    assert (await pgw.passkey_status("demo-user"))["required"] is False
    await enroll(pgw)
    assert (await pgw.passkey_status("demo-user"))["required"] is True
    with pytest.raises(GatewayError, match="signed with your passkey"):
        await pgw.decide_intent(r["intent_id"], "demo-user", True)
    await pgw.decide_intent(r["intent_id"], "demo-user", False)  # rejecting needs no signature


@pytest.mark.anyio
async def test_user_signatures_reach_the_receipt_and_verify(pgw):
    dev = await enroll(pgw)
    iid = await signed_intent(pgw, dev)
    held = await pgw.request_purchase(iid, "northside-office", [L(sku="NO-A4-500W-PREM", qty=10, item=0)])
    assert held["status"] == "pending_approval"
    opts = await pgw.purchase_approval_options(held["purchase_id"], "demo-user")
    assert opts["statement"]["cart_hash"] == held["cart_hash"]
    done = await pgw.decide_purchase(held["purchase_id"], "demo-user", True, held["cart_hash"],
                                     dev.sign(opts["options"]))
    assert done["status"] == "ordered" and done["user_signed"]
    v = await pgw.verify_receipt(done["receipt_id"])
    assert v["valid"] and set(v["user_signatures"]) == {"intent_mandate", "cart_mandate"}
    assert all(x["valid"] for x in v["user_signatures"].values())
    r = v["receipt"]
    assert verify_user_signature(r["cart_mandate"]["user_signature"], {"cart_hash": r["cart_hash"]})[0]


@pytest.mark.anyio
async def test_signature_for_one_cart_cannot_approve_another(pgw):
    dev = await enroll(pgw)
    iid = await signed_intent(pgw, dev)
    a = await pgw.request_purchase(iid, "northside-office", [L(sku="NO-A4-500W-PREM", qty=9, item=0)])
    opts_a = await pgw.purchase_approval_options(a["purchase_id"], "demo-user")
    signed_for_a = dev.sign(opts_a["options"])
    b = await pgw.request_purchase(iid, "northside-office", [L(sku="NO-A4-500W-PREM", qty=10, item=0)])
    await pgw.purchase_approval_options(b["purchase_id"], "demo-user")
    with pytest.raises(GatewayError):
        await pgw.decide_purchase(b["purchase_id"], "demo-user", True, b["cart_hash"], signed_for_a)


@pytest.mark.anyio
async def test_never_mode_allows_scripts_and_receipt_says_unsigned(engine):
    gw = Gateway(make_sessionmaker(engine), Signer.ephemeral(), DictCatalog(CATALOG),
                 StubVerifier(), StubInjectionDetector(), FakePayPal(),
                 public_base_url="http://gw.test", web_base_url=ORIGIN,
                 passkeys=Passkeys(RP, ORIGIN), require_passkey="never")
    await enroll(gw)
    r = await gw.confirm_intent("paper", paper_mandate())
    view = await gw.decide_intent(r["intent_id"], "demo-user", True)
    assert view["status"] == "active" and not view["user_signed"]


def test_abandoned_ceremonies_are_pruned():
    import time as _t

    pk, _, stored = enrolled()
    for i in range(150):
        pk.approval_options(f"signin:{i}", new_statement("sign_in", n=i), [stored["credential_id"]])
    for k in pk._pending:
        pk._pending[k] = (_t.time() - 1, pk._pending[k][1])  # all expired
    pk.approval_options("signin:new", new_statement("sign_in", n=0), [stored["credential_id"]])
    assert list(pk._pending) == ["approve:signin:new"]
