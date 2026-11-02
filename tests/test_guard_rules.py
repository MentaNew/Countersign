"""Checkout guard rules (P2.1), without a browser."""

from decimal import Decimal

from checkout_guard.rules import (
    is_payment_url,
    is_sensitive_field,
    lines_from_shopify_cart,
    parse_checkout_totals,
    parse_money,
    same_lines,
)


def test_payment_steps_are_recognised():
    for url in ("https://www.sandbox.paypal.com/checkoutnow?token=X",
                "https://www.paypal.com/sdk/js?client-id=abc", "https://checkout.stripe.com/c/pay",
                "https://pay.shopify.com/x", "https://deposit.shopifycs.com/sessions"):
        assert is_payment_url(url), url
    for url in ("https://greenleaf.localhost:8300/checkout", "https://www.paypalobjects.com/logo.png",
                "https://notpaypal.com/", "https://shop.example/cart"):
        assert not is_payment_url(url), url


def test_agent_may_not_type_card_bank_or_password_fields():
    assert is_sensitive_field({"autocomplete": "cc-number"})
    assert is_sensitive_field({"name": "card_number"})
    assert is_sensitive_field({"label": "Security code"})
    assert is_sensitive_field({"type": "password", "name": "pw"})
    assert is_sensitive_field({"placeholder": "IBAN"})
    assert not is_sensitive_field({"autocomplete": "email", "name": "email"})
    assert not is_sensitive_field({"autocomplete": "street-address", "label": "Address"})


def test_money_and_checkout_totals_from_page_text():
    assert parse_money("USD $42.79") == Decimal("42.79")
    assert parse_money("-$5.00") == Decimal("-5.00")
    assert parse_money("$1,234.50") == Decimal("1234.50")
    assert parse_money("Free") == 0
    assert parse_money("Calculated at next step") is None
    text = """Order summary
10 × A4 Copy Paper, 500 sheets (White)\t$35.00
Subtotal\t$35.00
Shipping
$4.99
Estimated taxes\t$2.80
Discount  -$1.00
Total\tUSD $41.79"""
    t = parse_checkout_totals(text)
    assert t == {"subtotal": Decimal("35.00"), "shipping": Decimal("4.99"),
                 "tax": Decimal("2.80"), "discount": Decimal("1.00"), "total": Decimal("41.79")}
    assert parse_checkout_totals("Shipping\nCalculated at next step\nTotal $10")["shipping"] is None


def test_shopify_cart_and_comparison_with_the_approved_cart():
    cart = {"currency": "USD", "items": [
        {"product_title": "A4 Copy Paper, 500 sheets", "variant_title": "White", "quantity": 10,
         "price": 350, "url": "/products/a4-copy-paper"}]}
    lines = lines_from_shopify_cart(cart, "http://greenleaf.localhost:8300")
    assert lines == [{"title": "A4 Copy Paper, 500 sheets", "variant": "White", "qty": 10,
                      "unit_price": {"amount": "3.5", "currency": "USD"},
                      "product_url": "http://greenleaf.localhost:8300/products/a4-copy-paper"}]
    approved = [{"title": "A4 Copy Paper, 500 sheets (White)", "qty": 10,
                 "unit_price": {"amount": "3.50", "currency": "USD"}}]
    assert same_lines(lines, approved)
    more = [{**lines[0], "qty": 11}]
    assert not same_lines(more, approved)
