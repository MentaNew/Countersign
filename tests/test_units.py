"""Pack sizes counted in code (Nov 3)."""

from datetime import UTC, datetime

import pytest

from gateway.domain import Mandate, Money, ResolvedLine
from gateway.policy.hard_checks import SpendState, run_hard_checks, unit_findings
from gateway.policy.units import pack_size

TITLES = {
    "Five Star Spiral Notebook, College Ruled, 70 Sheets, 11\" x 8-1/2\", 12 Pack": 12,
    "Energizer MAX AAA Batteries, 48 Count (Pack of 2)": 96,
    "Post-it Notes, 3 in x 3 in, 100 Sheets/Pad, 12 Pads/Pack": 12,
    "65W USB C Wall Charger, 3-Pack": 3,
    "Bounty Select-A-Size Paper Towels - 6 Double Rolls": 6,
    "Poland Spring Water, 16.9 fl oz, 24 Count": 24,
    "Gourmesso Ristretto - 200 Capsules (2 x 100)": 200,
    "Peet's Espresso Capsules, 100 Count (10 Boxes of 10)": 100,
    "Epson T212XL Black Ink, Twin Pack (2 Cartridges)": 2,
    "Wet Dog Food, 13-oz can, case of 12": 12,
    "Pilot G2 Gel Pens, Value Pack 20 + 4 Bonus": None,
    "A4 Copy Paper 80gsm, 500 sheets, white": None,
    "Dry Dog Food, 35-lb bag": None,
    "Bottled spring water, case of 24 x 16.9 fl oz bottles": 24,
    "Paper towels, pack of 6 rolls": 6,
}


@pytest.mark.parametrize(("title", "expected"), TITLES.items())
def test_pack_size_from_titles(title, expected):
    assert pack_size(title) == expected


def mandate(desc, qty):
    return Mandate.model_validate({
        "items": [{"description": desc, "qty": qty}], "max_total": {"amount": "500", "currency": "USD"},
        "merchants": ["shop"], "expires_at": "2099-01-01T00:00:00Z"})


def line(title, qty):
    return ResolvedLine(sku="x", title=title, unit_price=Money(amount="1", currency="USD"),
                        qty=qty, item=0)


def quantity_ok(m, lines, state=SpendState()):
    return next(c for c in run_hard_checks(m, "shop", lines, state, datetime.now(UTC))
                if c.name == "quantity").passed


def test_two_six_roll_packs_fill_a_twelve_roll_request():
    m = mandate("Paper towels, pack of 12 rolls", 1)
    ln = [line("Bounty Paper Towels - 6 Double Rolls", 2)]
    assert quantity_ok(m, ln) and unit_findings(m, ln, SpendState()) == []


def test_one_smaller_pack_is_held():
    m = mandate("Paper towels, pack of 12 rolls", 1)
    ln = [line("Bounty Paper Towels - 6 Double Rolls", 1)]
    assert quantity_ok(m, ln) and "smaller packs" in unit_findings(m, ln, SpendState())[0]


def test_too_many_units_is_held_and_too_many_packages_blocked():
    m = mandate("AAA alkaline batteries, pack of 24", 2)
    double = [line("Energizer AAA Batteries, 48 Count (Pack of 2)", 1)]
    assert quantity_ok(m, double) and "more than you asked" in unit_findings(m, double, SpendState())[0]
    assert not quantity_ok(m, [line("Duracell AAA, 24 Count", 3)])  # 3 packs of 24 for 2


def test_a_twelve_pack_counts_as_twelve_and_prior_purchases_count_in_units():
    m = mandate("Spiral notebook, college ruled", 12)
    assert quantity_ok(m, [line("Spiral Notebooks, 12 Pack", 1)])
    bought = SpendState(qty_by_item={0: 12}, packs_by_item={0: 1})
    assert not quantity_ok(m, [line("Spiral Notebook", 1)], bought)


def test_model_numbers_and_stated_totals():
    assert pack_size("Anker USB C Charger 65W, 735 Charger (Nano II 65W) 3-Port") is None
    assert pack_size("Energizer MAX AA Batteries, 48 Count (Pack of 2) - 96 Batteries Total") == 96
    assert pack_size("Anker Nano Chargers, 3 Chargers") == 3
