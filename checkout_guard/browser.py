"""The checkout guard's browser. The agent drives it through tools, never directly: every payment
step is blocked until the gateway has issued a permit for the exact cart the user approved with
their passkey. The guard reads the cart itself from the shop, and identifies itself as an AI
agent in its user agent."""

import asyncio
import base64
import hashlib
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Request,
    Route,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError

from checkout_guard.gateway_client import GatewayClient
from checkout_guard.rules import (
    host_of,
    is_payment_url,
    is_sensitive_field,
    lines_from_shopify_cart,
    parse_checkout_totals,
    same_lines,
)
from gateway.signing.keys import verify_with_public_key

log = logging.getLogger("checkout_guard")
AGENT_TAG = "SpendGuardrail-CheckoutGuard/0.1 (AI shopping agent acting for a person)"
CART_MUTATIONS = ("/cart/add", "/cart/change", "/cart/update", "/cart/clear")

SNAPSHOT_JS = """() => {
  let n = 0; const out = [];
  const sel = 'a[href], button, input, select, textarea, [role=button]';
  for (const el of document.querySelectorAll(sel)) {
    const st = getComputedStyle(el);
    if (el.type === 'hidden' || st.display === 'none' || st.visibility === 'hidden') continue;
    const ref = 'e' + (++n); el.setAttribute('data-guard-ref', ref);
    const label = el.labels && el.labels[0] ? el.labels[0].innerText.trim() : '';
    out.push({ref, tag: el.tagName.toLowerCase(), type: el.type || '',
      text: (el.tagName === 'SELECT' ? '' : (el.innerText || el.value || '')).trim().slice(0, 80),
      name: el.name || '', id: el.id || '', label: label.slice(0, 80),
      placeholder: el.placeholder || '', href: el.getAttribute('href') || '',
      autocomplete: el.getAttribute('autocomplete') || '',
      'aria-label': el.getAttribute('aria-label') || '',
      options: el.tagName === 'SELECT' ? [...el.options].map(o => o.text) : undefined});
  }
  return {url: location.href, title: document.title,
          text: document.body ? document.body.innerText.slice(0, 8000) : '', elements: out};
}"""

FETCH_JSON_JS = """async (u) => {
  const r = await fetch(u, {credentials: 'same-origin', headers: {Accept: 'application/json'}});
  if (!r.ok) return null;
  try { return await r.json(); } catch (e) { return null; }
}"""


class GuardError(Exception):
    """Something the agent should be told plainly (blocked, unreadable cart...)."""


def site(url: str) -> str:
    return host_of(url).removeprefix("www.")


def on_shop(url: str, shop: str) -> bool:
    """The shop's own site or one of its subdomains (e.g. checkout.shop.com)."""
    s = site(url)
    return s == shop or s.endswith("." + shop)


class CheckoutGuard:
    def __init__(self, gateway: GatewayClient, headless: bool = False, channel: str = "chrome"):
        self.gateway = gateway
        self.headless = headless
        self.channel = channel
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._main: Page | None = None
        self._elements: dict[str, dict[str, Any]] = {}
        self.permit: dict[str, Any] | None = None  # the unlocked purchase's signed permit
        self.blocked: list[str] = []  # payment requests refused (shown to the agent and user)
        self.checkout_shop: str | None = None  # origin the cart was read from
        self._loading = 0  # page loads the guard is still inspecting
        # (PayPal order id, page url) when PayPal sent the browser back to the shop after paying
        self.paypal_return: tuple[str, str] | None = None

    # ------------------------------------------------------------------ browser

    async def page(self) -> Page:
        if self._page is None:
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(channel=self.channel,
                                                           headless=self.headless)
            probe = await self._browser.new_page()
            ua = await probe.evaluate("navigator.userAgent")
            await probe.close()
            self._context = await self._browser.new_context(
                user_agent=f"{ua.replace('HeadlessChrome', 'Chrome')} {AGENT_TAG}",
                viewport={"width": 1280, "height": 900})
            await self._context.route("**/*", self._route)
            self._page = await self._context.new_page()
            self._main = self._page
            self._context.on("page", self._on_popup)
        return self._page

    def _on_popup(self, page: Page) -> None:
        """Follow popups (PayPal often opens one); when it closes, go back to the shop's tab."""
        self._page = page

        def closed(_: Page) -> None:
            if self._page is page:
                self._page = self._main
        page.on("close", closed)

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._page = self._main = self._context = self._browser = self._pw = None

    def unlocked(self) -> bool:
        if not self.permit:
            return False
        expires = datetime.fromisoformat(self.permit["permit"]["expires_at"])
        if datetime.now(UTC) >= expires:
            self.permit = None
            return False
        return True

    async def _route(self, route: Route, request: Request) -> None:
        url = request.url
        if (self.permit and request.method != "GET"
                and on_shop(url, self.permit["permit"]["shop"])
                and urlsplit(url).path.startswith(CART_MUTATIONS)):
            self.permit = None  # the cart is changing: the approval no longer applies
            log.info("cart changed after approval; payment locked again")
        if is_payment_url(url) and not self.unlocked():
            await self._block(route, url)
            return
        if request.resource_type == "document":
            # A shop's "Pay" button often answers with a redirect to the payment site, and
            # redirect hops never reach this handler: fetch the page ourselves and look.
            self._loading += 1
            try:
                await self._document(route, url)
            finally:
                self._loading -= 1
            return
        await route.continue_()

    def _note_paypal_return(self, url: str) -> None:
        """PayPal returns the buyer to the shop with ?token=<order id>&PayerID=..."""
        if not self.permit or not on_shop(url, self.permit["permit"]["shop"]):
            return
        q = parse_qs(urlsplit(url).query)
        if q.get("token") and q.get("PayerID"):
            self.paypal_return = (q["token"][0], url)
            log.info("PayPal returned to the shop with order %s", q["token"][0])

    async def _document(self, route: Route, url: str) -> None:
        self._note_paypal_return(url)
        try:
            resp = await route.fetch(max_redirects=0)
        except PlaywrightError:
            await route.abort()
            return
        target = urljoin(url, resp.headers.get("location", ""))
        if 300 <= resp.status < 400 and is_payment_url(target) and not self.unlocked():
            await self._block(route, target)
            return
        await route.fulfill(response=resp)

    async def _block(self, route: Route, url: str) -> None:
        self.blocked.append(url)
        log.info("blocked payment request %s", url)
        await route.abort("blockedbyclient")

    # ------------------------------------------------------------------ agent actions

    async def snapshot(self) -> dict[str, Any]:
        page = await self.page()
        try:
            snap = await page.evaluate(SNAPSHOT_JS)
        except PlaywrightError as e:
            raise GuardError(f"could not read the page ({e.message.splitlines()[0]})") from e
        self._elements = {el["ref"]: el for el in snap["elements"]}
        snap["payment"] = ("unlocked for the approved cart" if self.unlocked() else
                           "locked: payment pages open only after the user approves the cart")
        if self.blocked:
            snap["blocked_payment_attempts"] = self.blocked[-3:]
        return snap

    async def open(self, url: str) -> dict[str, Any]:
        if is_payment_url(url) and not self.unlocked():
            self.blocked.append(url)
            raise GuardError("blocked: payment pages are locked until the user approves this "
                             "cart (call checkout_ready on the shop's checkout page)")
        page = await self.page()
        try:
            await page.goto(url, wait_until="load")
        except PlaywrightError as e:
            raise GuardError(f"could not open {url} ({e.message.splitlines()[0]})") from e
        return await self.snapshot()

    async def click(self, ref: str) -> dict[str, Any]:
        page = await self.page()
        if ref not in self._elements:
            raise GuardError(f"unknown element {ref}; take a new snapshot")
        before = len(self.blocked)
        try:
            await page.locator(f'[data-guard-ref="{ref}"]').click(timeout=15000)
            await self._settle()
        except PlaywrightError as e:
            if len(self.blocked) == before:
                raise GuardError(f"click failed ({e.message.splitlines()[0]})") from e
        page = await self.page()
        if len(self.blocked) > before:
            if page.url.startswith("chrome-error:"):
                try:
                    await page.go_back(wait_until="load")
                except PlaywrightError:
                    pass
            snap = await self.snapshot()
            snap["blocked"] = ("That leads to a payment step, which stays locked until the user "
                               "approves this exact cart. Call checkout_ready on the checkout "
                               "page first.")
            return snap
        return await self.snapshot()

    async def _settle(self) -> None:
        """Wait until a click's page load (if any) has been inspected and has loaded."""
        await asyncio.sleep(0.3)  # let a navigation start
        for _ in range(300):
            if not self._loading:
                break
            await asyncio.sleep(0.1)
        page = await self.page()
        await page.wait_for_load_state("load", timeout=30000)

    async def type_text(self, ref: str, text: str) -> dict[str, Any]:
        el = self._elements.get(ref)
        if el is None:
            raise GuardError(f"unknown element {ref}; take a new snapshot")
        if is_sensitive_field(el):
            raise GuardError("refused: card numbers, security codes, bank details and "
                             "passwords are entered by the person, never by the agent")
        page = await self.page()
        await page.locator(f'[data-guard-ref="{ref}"]').fill(text, timeout=15000)
        return {"ok": True, "ref": ref}

    async def choose(self, ref: str, option: str) -> dict[str, Any]:
        if ref not in self._elements:
            raise GuardError(f"unknown element {ref}; take a new snapshot")
        page = await self.page()
        await page.locator(f'[data-guard-ref="{ref}"]').select_option(label=option,
                                                                        timeout=15000)
        return {"ok": True, "ref": ref, "option": option}

    # ------------------------------------------------------------------ cart and permit

    async def read_cart(self) -> dict[str, Any]:
        """Read the cart from the shop itself: lines from its cart API, shipping, tax and total
        from the checkout page the browser is on. Never from the agent."""
        page = await self.page()
        parts = urlsplit(page.url)
        origin = f"{parts.scheme}://{parts.netloc}"
        cart = await page.evaluate(FETCH_JSON_JS, f"{origin}/cart.js")
        if not cart or not cart.get("items"):
            raise GuardError("could not read a cart from this shop (the guard reads "
                             "Shopify-style /cart.js carts); is the cart empty?")
        lines = lines_from_shopify_cart(cart, origin)
        text = await page.evaluate("document.body.innerText")
        totals = parse_checkout_totals(text)
        if totals["total"] is None:
            raise GuardError("no order total on this page: go to the shop's checkout page, "
                             "where shipping, tax and the total are shown")
        subtotal = sum((Decimal(ln["unit_price"]["amount"]) * ln["qty"] for ln in lines),
                       Decimal(0))
        if totals["subtotal"] is not None and totals["subtotal"] != subtotal:
            raise GuardError(f"the page's subtotal {totals['subtotal']} differs from the cart "
                             f"({subtotal}); reload the checkout page")
        cur = lines[0]["unit_price"]["currency"]
        shot = await page.screenshot(full_page=True)
        if len(shot) > 2_500_000:  # very long page: keep what is on screen (the gateway caps size)
            shot = await page.screenshot()

        def m(v: Decimal | None) -> dict[str, str] | None:
            return None if v is None else {"amount": f"{v:.2f}", "currency": cur}

        self.checkout_shop = origin
        return {
            "shop_url": origin, "page_url": page.url, "lines": lines,
            "shipping": m(totals["shipping"]), "tax": m(totals["tax"]),
            "discount": m(totals["discount"]), "total": m(totals["total"]),
            "reader": "shopify-cart-json+checkout-text",
            "screenshot_png_b64": base64.b64encode(shot).decode(),
            "page_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }

    async def unlock(self, purchase: dict[str, Any]) -> str:
        """Open the payment step for an approved purchase, after checking the gateway's signed
        permit and that the browser's cart is still the approved one."""
        permit = purchase.get("checkout_permit")
        if not permit:
            raise GuardError("no checkout permit on this purchase")
        if not verify_with_public_key(await self.gateway.public_key(), permit["permit"],
                                      permit["signature"]):
            raise GuardError("the checkout permit's signature does not verify")
        body = permit["permit"]
        if datetime.now(UTC) >= datetime.fromisoformat(body["expires_at"]):
            raise GuardError("the checkout permit expired; ask for the cart again")
        page = await self.page()
        if not on_shop(page.url, body["shop"]):
            raise GuardError(f"the browser is not on {body['shop']}; open its checkout page")
        parts = urlsplit(page.url)
        cart = await page.evaluate(FETCH_JSON_JS, f"{parts.scheme}://{parts.netloc}/cart.js")
        read = lines_from_shopify_cart(cart or {}, f"{parts.scheme}://{parts.netloc}")
        if not same_lines(read, purchase["lines"]):
            raise GuardError("the cart in the browser is no longer the approved cart; "
                             "call checkout_ready again")
        self.permit = permit
        return f"payment unlocked for {body['total']} {body['currency']} at {body['shop']}"

    async def payment_report(self) -> dict[str, Any]:
        """What the guard saw after paying: the PayPal order id and the shop's page."""
        if not self.paypal_return:
            raise GuardError("no PayPal payment seen yet: the person must finish paying in "
                             "PayPal, which then returns to the shop")
        order_id, url = self.paypal_return
        page = await self.page()
        text = await page.evaluate("document.body ? document.body.innerText : ''")
        return {"paypal_order_id": order_id, "page_url": url,
                "page_text_sha256": hashlib.sha256(text.encode()).hexdigest()}

    def lock(self) -> None:
        """After the payment is recorded, payment pages are locked again."""
        self.permit = None
        self.paypal_return = None
