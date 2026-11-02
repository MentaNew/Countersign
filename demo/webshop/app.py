"""Greenleaf Paper Co.: a fictional *ordinary* web shop, the kind a browser agent meets on the
real internet. It does not sign carts and knows nothing about the gateway. It has a
Shopify-style cart (`/cart.js`), a checkout with shipping and tax, and takes payment with its own
PayPal order (sandbox), the way a real shop does.

Run: uv run uvicorn demo.webshop.app:app --port 8300
Open it as http://greenleaf.localhost:8300 (browsers send *.localhost to this machine), so the
shop has its own name: the gateway knows it as merchant "greenleaf.localhost".
"""

import html
import secrets
from decimal import ROUND_HALF_UP, Decimal

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic_settings import BaseSettings, SettingsConfigDict

from gateway.config import ROOT


class ShopSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    webshop_base_url: str = "http://greenleaf.localhost:8300"


S = ShopSettings()
CURRENCY = "USD"
SHIPPING = Decimal("4.99")
TAX_RATE = Decimal("0.08")
PRODUCTS = {
    "a4-copy-paper": {"title": "A4 Copy Paper, 500 sheets", "price": Decimal("3.50"),
                      "variants": ["White", "Ivory"],
                      "description": "80gsm everyday copy paper for laser and inkjet printers."},
    "letter-copy-paper": {"title": "US Letter Copy Paper, 500 sheets", "price": Decimal("3.40"),
                          "variants": ["White"], "description": "8.5 x 11 in, 20 lb."},
    "premium-pens": {"title": "Gel Pens, pack of 12", "price": Decimal("8.90"),
                     "variants": ["Black", "Blue"], "description": "Smooth 0.7 mm gel ink."},
    "gift-card-50": {"title": "Greenleaf Gift Card", "price": Decimal("50.00"),
                     "variants": ["50 USD"], "description": "Digital gift card, emailed."},
}
CARTS: dict[str, list[dict]] = {}  # session id -> lines {handle, variant, qty}
ORDERS: dict[str, dict] = {}  # PayPal order id -> what was paid

app = FastAPI(title="Greenleaf Paper Co. (demo web shop)")


def money(d: Decimal) -> str:
    return f"${d.quantize(Decimal('0.01'))}"


def session(request: Request) -> str:
    return request.cookies.get("cart_session") or ""


def cart_of(request: Request) -> list[dict]:
    return CARTS.get(session(request), [])


def totals(lines: list[dict]) -> dict[str, Decimal]:
    sub = sum((PRODUCTS[ln["handle"]]["price"] * ln["qty"] for ln in lines), Decimal(0))
    tax = (sub * TAX_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    ship = SHIPPING if lines else Decimal(0)
    return {"subtotal": sub, "shipping": ship, "tax": tax, "total": sub + ship + tax}


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<title>{html.escape(title)} · Greenleaf Paper Co.</title>
<style>body{{font-family:system-ui;max-width:760px;margin:24px auto;padding:0 16px;color:#1d2a1f}}
a{{color:#2f6b3a}} .box{{border:1px solid #cfd8cf;border-radius:8px;padding:14px;margin:12px 0}}
button{{padding:8px 14px}} table{{width:100%;border-collapse:collapse}} td{{padding:4px 0}}
.paypal{{background:#ffc439;border:0;border-radius:20px;padding:10px 24px;font-weight:600}}</style>
</head><body><header><a href="/"><strong>Greenleaf Paper Co.</strong></a> ·
<a href="/cart">Cart</a></header>{body}</body></html>""")


@app.get("/", response_class=HTMLResponse)
def home():
    items = "".join(
        f'<div class="box"><a href="/products/{h}">{html.escape(p["title"])}</a> '
        f'— {money(p["price"])}</div>' for h, p in PRODUCTS.items())
    return page("Shop", f"<h1>Office paper and supplies</h1>{items}")


@app.get("/products/{handle}", response_class=HTMLResponse)
def product(handle: str):
    p = PRODUCTS.get(handle) or {}
    if not p:
        raise HTTPException(404)
    options = "".join(f"<option>{html.escape(v)}</option>" for v in p["variants"])
    return page(p["title"], f"""<h1>{html.escape(p["title"])}</h1><p>{money(p["price"])}</p>
<p>{html.escape(p["description"])}</p>
<form method="post" action="/cart/add"><input type="hidden" name="handle" value="{handle}">
<label>Variant <select name="variant">{options}</select></label>
<label>Quantity <input name="qty" type="number" value="1" min="1"></label>
<button type="submit">Add to cart</button></form>""")


@app.post("/cart/add")
def cart_add(request: Request, handle: str = Form(...), variant: str = Form(""),
             qty: int = Form(1)):
    if handle not in PRODUCTS or qty < 1:
        raise HTTPException(400, "unknown product")
    sid = session(request) or secrets.token_hex(8)
    lines = CARTS.setdefault(sid, [])
    for ln in lines:
        if ln["handle"] == handle and ln["variant"] == variant:
            ln["qty"] += qty
            break
    else:
        lines.append({"handle": handle, "variant": variant, "qty": qty})
    r = RedirectResponse("/cart", status_code=303)
    r.set_cookie("cart_session", sid, httponly=True, samesite="lax")
    return r


@app.post("/cart/clear")
def cart_clear(request: Request):
    CARTS.pop(session(request), None)
    return RedirectResponse("/cart", status_code=303)


@app.get("/cart.js")
def cart_js(request: Request):
    """Shopify's public cart format (prices in cents), read by the checkout guard."""
    lines = cart_of(request)
    items = [{"title": f'{PRODUCTS[ln["handle"]]["title"]} - {ln["variant"]}',
              "product_title": PRODUCTS[ln["handle"]]["title"], "variant_title": ln["variant"],
              "quantity": ln["qty"], "price": int(PRODUCTS[ln["handle"]]["price"] * 100),
              "line_price": int(PRODUCTS[ln["handle"]]["price"] * ln["qty"] * 100),
              "url": f'/products/{ln["handle"]}', "handle": ln["handle"]} for ln in lines]
    return JSONResponse({"token": session(request), "currency": CURRENCY,
                         "item_count": sum(ln["qty"] for ln in lines), "items": items,
                         "total_price": sum(i["line_price"] for i in items)})


@app.get("/cart", response_class=HTMLResponse)
def cart_page(request: Request):
    lines = cart_of(request)
    rows = "".join(f'<tr><td>{ln["qty"]} × {html.escape(PRODUCTS[ln["handle"]]["title"])} '
                   f'({html.escape(ln["variant"])})</td>'
                   f'<td>{money(PRODUCTS[ln["handle"]]["price"] * ln["qty"])}</td></tr>'
                   for ln in lines)
    if not lines:
        return page("Cart", '<h1>Your cart</h1><p>Your cart is empty.</p>')
    return page("Cart", f"""<h1>Your cart</h1><table>{rows}</table>
<form method="get" action="/checkout"><button type="submit">Check out</button></form>
<form method="post" action="/cart/clear"><button type="submit">Empty cart</button></form>""")


@app.get("/checkout", response_class=HTMLResponse)
def checkout(request: Request):
    lines = cart_of(request)
    if not lines:
        return RedirectResponse("/cart", status_code=303)
    t = totals(lines)
    rows = "".join(f'<tr><td>{ln["qty"]} × {html.escape(PRODUCTS[ln["handle"]]["title"])} '
                   f'({html.escape(ln["variant"])})</td>'
                   f'<td>{money(PRODUCTS[ln["handle"]]["price"] * ln["qty"])}</td></tr>'
                   for ln in lines)
    return page("Checkout", f"""<h1>Checkout</h1>
<div class="box"><h2>Order summary</h2><table>{rows}
<tr><td>Subtotal</td><td>{money(t["subtotal"])}</td></tr>
<tr><td>Shipping</td><td>{money(t["shipping"])}</td></tr>
<tr><td>Estimated taxes</td><td>{money(t["tax"])}</td></tr>
<tr><td><strong>Total</strong></td><td><strong>{CURRENCY} {money(t["total"])}</strong></td></tr>
</table></div>
<form method="post" action="/checkout/pay"><div class="box"><h2>Delivery</h2>
<label>Email <input name="email" type="email" autocomplete="email"></label><br>
<label>Name <input name="name" autocomplete="name"></label><br>
<label>Address <input name="address" autocomplete="street-address"></label></div>
<button class="paypal" type="submit">Pay with PayPal</button></form>""")


async def paypal_token(http: httpx.AsyncClient) -> str:
    r = await http.post("/v1/oauth2/token", auth=(S.paypal_client_id, S.paypal_client_secret),
                        data={"grant_type": "client_credentials"})
    r.raise_for_status()
    return r.json()["access_token"]


@app.post("/checkout/pay")
async def pay(request: Request, email: str = Form(""), name: str = Form(""),
              address: str = Form("")):
    """The shop creates its own PayPal order and sends the buyer to PayPal, like a real shop."""
    lines = cart_of(request)
    if not lines:
        return RedirectResponse("/cart", status_code=303)
    t = totals(lines)
    if not (S.paypal_client_id and S.paypal_client_secret):
        # No PayPal app configured: send the buyer to PayPal's sandbox site anyway, so the
        # checkout guard's payment lock can still be seen working.
        return RedirectResponse("https://www.sandbox.paypal.com/checkoutnow?token=DEMO-NO-APP",
                                status_code=303)
    amount = lambda d: {"currency_code": CURRENCY, "value": f"{d:.2f}"}
    body = {"intent": "CAPTURE", "purchase_units": [{
        "description": "Greenleaf Paper Co. order",
        "amount": {**amount(t["total"]), "breakdown": {
            "item_total": amount(t["subtotal"]), "shipping": amount(t["shipping"]),
            "tax_total": amount(t["tax"])}},
        "items": [{"name": f'{PRODUCTS[ln["handle"]]["title"]} ({ln["variant"]})'[:127],
                   "quantity": str(ln["qty"]),
                   "unit_amount": amount(PRODUCTS[ln["handle"]]["price"])} for ln in lines]}],
        "payment_source": {"paypal": {"experience_context": {
            "brand_name": "Greenleaf Paper Co.", "user_action": "PAY_NOW",
            "shipping_preference": "NO_SHIPPING",
            "return_url": f"{S.webshop_base_url}/checkout/return",
            "cancel_url": f"{S.webshop_base_url}/checkout"}}}}
    async with httpx.AsyncClient(base_url=S.paypal_base_url, timeout=30) as http:
        r = await http.post("/v2/checkout/orders", json=body, headers={
            "Authorization": f"Bearer {await paypal_token(http)}",
            "PayPal-Request-Id": secrets.token_hex(12)})
    if r.status_code >= 400:
        raise HTTPException(502, f"PayPal: {r.text[:300]}")
    order = r.json()
    ORDERS[order["id"]] = {"session": session(request), "total": f'{t["total"]:.2f}',
                           "email": email, "name": name, "address": address}
    link = next(lk["href"] for lk in order["links"] if lk["rel"] in ("payer-action", "approve"))
    return RedirectResponse(link, status_code=303)


@app.get("/checkout/return", response_class=HTMLResponse)
async def paypal_return(token: str):
    """PayPal sends the buyer back here after approving; the shop captures the payment."""
    if token not in ORDERS:
        raise HTTPException(404, "unknown order")
    async with httpx.AsyncClient(base_url=S.paypal_base_url, timeout=30) as http:
        r = await http.post(f"/v2/checkout/orders/{token}/capture", json={}, headers={
            "Authorization": f"Bearer {await paypal_token(http)}",
            "PayPal-Request-Id": f"capture-{token}"})
    data = r.json()
    captures = [c for pu in data.get("purchase_units", [])
                for c in (pu.get("payments") or {}).get("captures", [])]
    o = ORDERS[token]
    if data.get("status") == "COMPLETED":
        CARTS.pop(o["session"], None)
    return page("Thank you", f"""<h1>Thank you for your order</h1>
<div class="box">Order <strong id="order-id">{html.escape(token)}</strong><br>
PayPal status: <span id="paypal-status">{html.escape(str(data.get("status")))}</span><br>
Transaction: <span id="capture-id">{html.escape(captures[0]["id"] if captures else "-")}</span><br>
Total: USD {o["total"]}</div>""")
