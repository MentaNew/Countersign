"""Typed domain objects shared by the MCP tools, the REST API, the policy and the verifier."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Decision = Literal["approve", "block", "escalate"]


class Money(BaseModel):
    amount: Decimal = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    def __str__(self) -> str:
        return f"{self.amount:.2f} {self.currency}"


class MandateItem(BaseModel):
    description: str = Field(min_length=2, max_length=300)
    qty: int = Field(gt=0, le=10_000)
    max_unit_price: Money | None = None


class Mandate(BaseModel):
    """What the user approved. Signed by the gateway; the agent only ever refers to it by id."""

    items: list[MandateItem] = Field(min_length=1, max_length=20)
    max_total: Money
    merchants: list[str] = Field(min_length=1)
    approval_above: Money | None = None
    expires_at: datetime
    max_purchases: int = Field(default=5, gt=0, le=100)

    @field_validator("expires_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return v

    def summary(self) -> str:
        """Plain-language summary shown to the user. Deterministic on purpose: no LLM in the loop."""
        lines = []
        for it in self.items:
            cap = f" (at most {it.max_unit_price} each)" if it.max_unit_price else ""
            lines.append(f"- {it.qty} x {it.description}{cap}")
        ask = (
            f"Ask me before any single purchase above {self.approval_above}."
            if self.approval_above
            else "Ask me only when something looks wrong."
        )
        return "\n".join(
            [
                "Buy only:",
                *lines,
                (
                    f"Spend no more than {self.max_total} in total, over at most "
                    f"{self.max_purchases} purchase(s)."
                ),
                f"Only from: {', '.join(self.merchants)}.",
                ask,
                f"This permission expires {self.expires_at:%Y-%m-%d %H:%M %Z}.",
            ]
        )


class CartLineRequest(BaseModel):
    """A line as the agent requests it. Prices and titles are never taken from the agent."""
    #what the agent requests to buy - deliberately small, because the agent is untrusted
    sku: str = Field(min_length=1, max_length=100)
    qty: int = Field(gt=0, le=10_000)
    item: int | None = Field(
        default=None, description="Index of the mandate item this line fulfils (0-based)"
    )


class ResolvedLine(BaseModel):
    """A cart line after the gateway looked it up in the merchant catalog."""

    sku: str
    title: str
    unit_price: Money
    qty: int
    item: int | None

    @property
    def line_total(self) -> Decimal:
        return self.unit_price.amount * self.qty


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class SemanticVerdict(BaseModel):
    match: dict[Literal["matches", "partial", "mismatch"], float]
    extra_items: float = Field(ge=0, le=1)
    model: str
    rationale: str = ""


class InjectionVerdict(BaseModel):
    score: float = Field(ge=0, le=1)
    per_text: list[float] = []
    model: str


class Verdict(BaseModel):
    decision: Decision
    hard_checks: list[CheckResult]
    semantic: SemanticVerdict | None = None
    injection: InjectionVerdict | None = None
    thresholds: dict[str, float] = {}
    reasons: list[str] = []
