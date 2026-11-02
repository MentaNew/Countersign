"""The checkout guard's rules, as plain functions (tested without a browser): which addresses are
payment steps, which form fields the agent may never type into, and how a cart and its totals
are read from a normal shop."""

import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin, urlsplit

# Payment steps: the agent cannot reach them until the user has approved this exact cart.
PAYMENT_HOSTS = (
    "paypal.com", "venmo.com", "pay.shopify.com", "shop.app", "checkout.stripe.com",
    "js.stripe.com", "pay.google.com", "apple.com", "klarna.com", "afterpay.com",
    "affirm.com", "shopifycs.com", "checkout.pci.shopifyinc.com", "adyen.com",
    "braintreegateway.com", "braintree-api.com",
)
# Static images and fonts from these hosts are harmless (logos on the shop's page).
STATIC_OK = ("paypalobjects.com",)

SENSITIVE_FIELD = re.compile(
    r"card|cc-?num|cc-?csc|cc-?exp|cvv|cvc|security.?code|expir|iban|routing|account.?num"
    r"|password|passcode|one-time-code|otp", re.IGNORECASE)


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def is_payment_url(url: str) -> bool:
    host = host_of(url)
    if any(host == h or host.endswith("." + h) for h in STATIC_OK):
        return False
    return any(host == h or host.endswith("." + h) for h in PAYMENT_HOSTS)


def is_sensitive_field(attrs: dict[str, Any]) -> bool:
    """Card numbers, security codes, bank details and passwords are for the person only."""
    if (attrs.get("type") or "").lower() == "password":
        return True
    if (attrs.get("autocomplete") or "").lower().startswith("cc-"):
        return True
    text = " ".join(str(attrs.get(k) or "") for k in
                    ("name", "id", "autocomplete", "placeholder", "label", "aria-label"))
    return bool(SENSITIVE_FIELD.search(text))


_AMOUNT = re.compile(r"(-)?\s*(?:[A-Z]{3}\s*)?[$€£]?\s*(-)?(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,2})?")


def parse_money(text: str) -> Decimal | None:
    """The last amount on a line: "$4.99", "USD $42.79", "-$5.00", "1,234.50". "Free" is 0."""
    if re.search(r"\bfree\b", text, re.IGNORECASE):
        return Decimal(0)
    found = [m for m in _AMOUNT.finditer(text) if m.group(3)]
    if not found:
        return None
    m = found[-1]
    try:
        value = Decimal(m.group(3).replace(",", "") + (m.group(4) or ""))
    except InvalidOperation:
        return None
    return -value if (m.group(1) or m.group(2)) else value


_LABELS = {
    "subtotal": re.compile(r"^\s*sub-?total\b", re.IGNORECASE),
    "shipping": re.compile(r"^\s*(shipping|delivery)\b", re.IGNORECASE),
    "tax": re.compile(r"^\s*(estimated\s+)?(sales\s+)?tax(es)?\b", re.IGNORECASE),
    "discount": re.compile(r"^\s*(discount|promo|coupon)\b", re.IGNORECASE),
    "total": re.compile(r"^\s*(order\s+)?total\b(?!\s*savings)", re.IGNORECASE),
}


def parse_checkout_totals(page_text: str) -> dict[str, Decimal | None]:
    """Read the order summary from a checkout page's visible text. A label's amount is on the
    same line or the next one. Unknown (e.g. "calculated at next step") stays None."""
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    out: dict[str, Decimal | None] = dict.fromkeys(_LABELS)
    for i, line in enumerate(lines):
        for key, rx in _LABELS.items():
            if out[key] is not None or not rx.search(line):
                continue
            amount = parse_money(rx.sub("", line))
            if amount is None and i + 1 < len(lines):
                nxt = lines[i + 1]
                if not any(r.search(nxt) for r in _LABELS.values()):
                    amount = parse_money(nxt)
            out[key] = abs(amount) if amount is not None else None
            break
    return out


def lines_from_shopify_cart(cart: dict[str, Any], shop_url: str) -> list[dict[str, Any]]:
    """Shopify's public `/cart.js` (prices in cents) -> PageLine dicts."""
    cur = (cart.get("currency") or "USD").upper()
    out = []
    for it in cart.get("items") or []:
        variant = it.get("variant_title") or None
        out.append({
            "title": it.get("product_title") or it.get("title") or "?",
            "variant": None if variant in (None, "Default Title") else variant,
            "qty": int(it["quantity"]),
            "unit_price": {"amount": str(Decimal(int(it["price"])) / 100), "currency": cur},
            "product_url": urljoin(shop_url, it.get("url") or ""),
        })
    return out


def same_lines(read: list[dict[str, Any]], approved: list[dict[str, Any]]) -> bool:
    """Is the cart in the browser still the cart the user approved (titles, qty, prices)?"""
    def key(lines, title_key):
        return sorted((str(ln[title_key]), int(ln["qty"]),
                       Decimal(str(ln["unit_price"]["amount"]))) for ln in lines)
    titled = [{**ln, "t": ln["title"] + (f" ({ln['variant']})" if ln.get("variant") else "")}
              for ln in read]
    return key(titled, "t") == key(approved, "title")
