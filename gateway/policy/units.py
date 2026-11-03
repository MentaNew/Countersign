"""Pack sizes, counted in code. "12 Pack" of notebooks is 12 notebooks; "48 Count (Pack of 2)"
is 96 batteries. Each cart line is converted into "how many of what the person asked for", so
the quantity rule counts the right thing and the AI check only has to judge the product itself.

When a title or request has no recognisable pack size, nothing is assumed: the line counts as
one requested item per unit bought, exactly as before (and the AI still sees the title)."""

import re
from dataclasses import dataclass
from fractions import Fraction

# Things counted per pack. Never sheets, ounces, pounds, watts, millimetres: those describe the
# item, not how many items there are.
# Plural only: "735 Charger" is a model number, "3 Chargers" is a count.
_NOUNS = (r"(?:capsules|pods|k-cups|batteries|cartridges|bottles|cans|pens|pencils"
          r"|notebooks|chargers|cables|pads|bars|tablets|filters|rolls)")
_COUNT = re.compile(r"(\d{1,4})\s*-?\s*(?:count|ct)\b", re.IGNORECASE)
_NOUN = re.compile(r"(\d{1,4})\s+(?:(?:double|mega|family|big|giant|super)\s+)?" + _NOUNS + r"\b",
                   re.IGNORECASE)
_PACK_OF = re.compile(r"\bpack\s+of\s+(\d{1,4})\b", re.IGNORECASE)
_N_PACK = re.compile(r"(\d{1,4})\s*-?\s*(?:pack|pk)\b", re.IGNORECASE)
_CASE_OF = re.compile(r"\b(?:case|box|set|carton)\s+of\s+(\d{1,4})\b", re.IGNORECASE)
_TWIN = re.compile(r"\btwin\s*-?\s*pack\b", re.IGNORECASE)


def pack_size(text: str) -> int | None:
    """Items per pack named in a title or request, or None if not stated or unclear."""
    counts = {int(m.group(1)) for m in _COUNT.finditer(text)}
    nouns = {int(m.group(1)) for m in _NOUN.finditer(text)}
    base_set = counts | nouns
    packs_of = {int(m.group(1)) for m in _PACK_OF.finditer(text)}
    if len(base_set) == 2 and len(packs_of) == 1:
        # "48 Count (Pack of 2) - 96 Batteries Total": the larger number is the total
        small, big = sorted(base_set)
        if big == small * next(iter(packs_of)):
            return big
    if len(base_set) > 1:
        return None  # e.g. "24 count ... 20 bottles": unclear, let the person/AI judge
    base = next(iter(base_set), None)
    if base is None:
        boxed = {int(m.group(1)) for m in _CASE_OF.finditer(text)}
        npack = {int(m.group(1)) for m in _N_PACK.finditer(text)}
        cands = boxed | npack | ({2} if _TWIN.search(text) else set())
        if len(cands) > 1:
            return None
        base = next(iter(cands), None)
    if len(packs_of) > 1:
        return None
    if packs_of:
        n = next(iter(packs_of))
        # "48 Count (Pack of 2)" = 2 packs of 48; "pack of 6 rolls" = 6 (same number twice).
        return n if base in (None, n) else base * n
    return base if base and base > 0 else None


@dataclass(frozen=True)
class LineUnits:
    requested: Fraction  # how many of the requested items this line amounts to
    known: bool  # True when the title's pack size was read; False = counted as before
    note: str  # plain-language explanation, shown to the AI check and the person


def line_units(request_description: str, title: str, qty: int) -> LineUnits:
    per_request = pack_size(request_description) or 1
    per_line = pack_size(title)
    if per_line is None:
        return LineUnits(Fraction(qty), False,
                         "pack size not stated in the title; counted as bought")
    req = Fraction(qty * per_line, per_request)
    unit = "" if per_request == 1 else f" (packs of {per_request})"
    shown = int(req) if req.denominator == 1 else float(round(req, 2))
    return LineUnits(req, True,
                     f"{qty} x {per_line} per pack = {qty * per_line} units = {shown} "
                     f"of the requested item{unit}; quantity checked in code")
