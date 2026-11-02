"""Fictional merchants for the demo. Serves a JSON catalog (what the gateway trusts for prices)
and plain HTML product pages (what the shopping agent reads, including a planted injection).

Run: uv run uvicorn demo.merchant.app:app --port 8100
"""

import html
import json
import os
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from gateway.catalog.client import price_lines
from gateway.catalog.offers import merchant_profile, sign_offer
from gateway.signing.jws import EcSigner

CATALOG: dict = json.loads((Path(__file__).parent / "catalog.json").read_text())
KEYS_DIR = Path(__file__).parent / "keys"  # one signing key per shop; gitignored
BASE_URL = os.environ.get("MERCHANT_BASE_URL", "http://localhost:8100")

app = FastAPI(title="Demo merchants")


SEED = os.environ.get("MERCHANT_KEY_SEED", "")  # hosted: no disk, keys derived from a secret


@lru_cache
def shop_signer(merchant_id: str) -> EcSigner:
    """Each shop signs its own cart offers (ES256 Checkout JWTs). The gateway pins this key.
    Locally the key is a file; on a host without a lasting disk it is derived from
    MERCHANT_KEY_SEED, so it stays the same across restarts (a changed key blocks purchases)."""
    if SEED:
        d = HKDF(hashes.SHA256(), 48, salt=b"demo-merchant-es256", info=merchant_id.encode()
                 ).derive(SEED.encode())
        n = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551  # P-256 order
        return EcSigner(ec.derive_private_key(int.from_bytes(d, "big") % (n - 1) + 1,
                                              ec.SECP256R1()))
    return EcSigner.from_file(KEYS_DIR / f"{merchant_id}-es256.pem")


class OfferLine(BaseModel):
    sku: str
    qty: int


class OfferRequest(BaseModel):
    lines: list[OfferLine]


@app.get("/{merchant_id}/.well-known/agent-merchant.json")
def profile(merchant_id: str) -> dict:
    if merchant_id not in CATALOG:
        raise HTTPException(404, "unknown merchant")
    return merchant_profile(merchant_id, CATALOG[merchant_id]["name"], shop_signer(merchant_id),
                            BASE_URL)


@app.post("/api/{merchant_id}/cart-offers")
def cart_offer(merchant_id: str, body: OfferRequest) -> dict:
    """Price the requested cart and sign it (the shop's half of the cart)."""
    if merchant_id not in CATALOG:
        raise HTTPException(404, "unknown merchant")
    offer = price_lines(merchant_id, CATALOG[merchant_id]["products"],
                        [ln.model_dump() for ln in body.lines])
    return sign_offer(shop_signer(merchant_id), offer)


def _product(merchant_id: str, sku: str) -> dict:
    m = CATALOG.get(merchant_id)
    p = next((p for p in m["products"] if p["sku"] == sku), None) if m else None
    if not p:
        raise HTTPException(404, f"{merchant_id}/{sku} not found")
    return p


@app.get("/api/{merchant_id}/products")
def list_products(merchant_id: str) -> list[dict]:
    if merchant_id not in CATALOG:
        raise HTTPException(404, "unknown merchant")
    return [
        {k: p[k] for k in ("sku", "title", "unit_price", "currency")}
        for p in CATALOG[merchant_id]["products"]
    ]


@app.get("/api/{merchant_id}/products/{sku}")
def get_product(merchant_id: str, sku: str) -> dict:
    p = _product(merchant_id, sku)
    return {k: p[k] for k in ("sku", "title", "unit_price", "currency")}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    items = "".join(
        f'<li><a href="/{mid}">{html.escape(m["name"])}</a></li>' for mid, m in CATALOG.items()
    )
    return f"<html><body><h1>Demo merchants</h1><ul>{items}</ul></body></html>"


@app.get("/{merchant_id}", response_class=HTMLResponse)
def store(merchant_id: str) -> str:
    m = CATALOG.get(merchant_id) or {}
    if not m:
        raise HTTPException(404, "unknown merchant")
    rows = "".join(
        f'<li><a href="/{merchant_id}/{p["sku"]}">{html.escape(p["title"])}</a>'
        f' ({p["sku"]}) {p["unit_price"]} {p["currency"]}</li>'
        for p in m["products"]
    )
    return f"<html><body><h1>{html.escape(m['name'])}</h1><ul>{rows}</ul></body></html>"


@app.get("/{merchant_id}/{sku}", response_class=HTMLResponse)
def product_page(merchant_id: str, sku: str) -> str:
    p = _product(merchant_id, sku)
    # Description is rendered raw on purpose: the planted injection hides in a display:none span.
    reviews = "".join(f"<blockquote>{html.escape(r)}</blockquote>" for r in p["reviews"])
    return (
        f"<html><body><h1>{html.escape(p['title'])}</h1><p>SKU {p['sku']}</p>"
        f"<p>{p['unit_price']} {p['currency']}</p><div>{p['description']}</div>"
        f"<h2>Reviews</h2>{reviews or '<p>No reviews yet.</p>'}</body></html>"
    )
