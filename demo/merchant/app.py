"""Fictional merchants for the demo. Serves a JSON catalog (what the gateway trusts for prices)
and plain HTML product pages (what the shopping agent reads, including a planted injection).

Run: uv run uvicorn demo.merchant.app:app --port 8100
"""

import html
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

CATALOG: dict = json.loads((Path(__file__).parent / "catalog.json").read_text())

app = FastAPI(title="Demo merchants")


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
