"""Score the gateway's decisions on labelled carts (eval/cases/*.yaml).

    uv run python eval/run_eval.py                       # offline stub verifier, no network
    uv run python eval/run_eval.py --verifier llm        # GroqCloud; answers cached in eval/cache/
    uv run python eval/run_eval.py --verifier llm --write   # also write docs/eval.md

Every case goes through gateway.policy.evaluate.evaluate_cart, the same code path as a live
purchase. Model answers are cached by (model, prompt, input), so re-runs cost no calls and
anyone can reproduce the reported numbers without an API key.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gateway.canonical import canonical_json
from gateway.config import get_settings
from gateway.domain import InjectionVerdict, Mandate, Money, ResolvedLine, SemanticVerdict
from gateway.policy.decision import Thresholds
from gateway.policy.evaluate import evaluate_cart
from gateway.policy.hard_checks import SpendState
from gateway.verifier.base import verifier_state

CASES = ROOT / "eval" / "cases"  # development set: seen while building and tuning
HELDOUT = ROOT / "eval" / "heldout"  # never used for tuning (see the header of each file)
CACHE = ROOT / "eval" / "cache"
EVAL_NOW = datetime(2026, 10, 20, tzinfo=UTC)
DECISIONS = ("approve", "escalate", "block")


class Cached:
    """Caches verifier and injection answers on disk, keyed by model, prompt and input."""

    def __init__(self, verifier, injection, tag: str, prompts: str, use_cache: bool):
        self.verifier, self.injection = verifier, injection
        self.tag, self.prompts, self.use_cache = tag, prompts, use_cache
        self.calls = 0
        self.hits = 0
        self.used: set[Path] = set()
        self.failures: list[str] = []
        self.case_files: list[Path] = []  # answers used by the case being evaluated
        self.refresh = False  # ask the model again and overwrite the cached answer
        self.refreshed: set[Path] = set()

    def _path(self, kind: str, payload: Any) -> Path:
        prompt = self.prompts.get(kind, "") if isinstance(self.prompts, dict) else self.prompts
        key = hashlib.sha256(canonical_json([self.tag, prompt, kind, payload])).hexdigest()
        return CACHE / self.tag.replace("/", "_").replace(":", "_") / f"{kind}-{key[:24]}.json"

    async def _get(self, kind, payload, compute, model):
        path = self._path(kind, payload)
        self.used.add(path)
        self.case_files.append(path)
        fresh = not self.refresh or path in self.refreshed  # refresh asks once per input
        if self.use_cache and path.exists() and fresh:
            self.hits += 1
            data = json.loads(path.read_text())
            # Current format keeps the raw exchanges next to the answer; older files are bare.
            return model.model_validate(data.get("verdict", data))
        self.calls += 1
        from gateway.verifier.groq import recorder

        exchanges: list[dict] = []
        token = recorder.set(exchanges) if self.tag != "stub" else None
        try:
            out = await self._retry(compute, kind)
        finally:
            if token is not None:
                recorder.reset(token)
        if self.use_cache:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.refreshed.add(path)
            path.write_text(json.dumps({
                "kind": kind, "input": payload, "verdict": out.model_dump(mode="json"),
                "exchanges": exchanges}, indent=1, ensure_ascii=False))
        return out

    async def _retry(self, compute, kind: str):
        """Groq rate limits (429) and brief server errors (5xx) are retried with waits, so the
        reported numbers measure the model, not the network. A check that still fails is
        counted in `failures` and the run refuses to write results."""
        import httpx

        for attempt in range(6):
            try:
                return await compute()
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if code != 429 and code < 500 or attempt == 5:
                    self.failures.append(f"{kind}: {code}")
                    raise
                wait = float(e.response.headers.get("retry-after") or 2 ** (attempt + 1))
                print(f"  {kind}: Groq {code}, retrying in {wait:.0f}s", file=sys.stderr)
                await asyncio.sleep(min(wait, 60))

    async def evaluate(self, mandate, lines):
        return await self._get("semantic", verifier_state(mandate, lines),
                               lambda: self.verifier.evaluate(mandate, lines), SemanticVerdict)

    async def score(self, texts):
        return await self._get("injection", texts, lambda: self.injection.score(texts),
                               InjectionVerdict)


def build(verifier_name: str, use_cache: bool) -> Cached:
    s = get_settings()
    if verifier_name == "stub":
        from gateway.verifier.stub import StubInjectionDetector, StubVerifier

        return Cached(StubVerifier(), StubInjectionDetector(), "stub", "stub-v1", use_cache=False)
    from gateway.verifier import groq as g

    client = g.GroqClient(s.groq_api_key)
    def h(*parts):
        return hashlib.sha256(canonical_json(list(parts))).hexdigest()[:12]

    # One prompt fingerprint per check, so editing one prompt only invalidates its own answers.
    prompts = {"semantic": h(g._MATCH_PROMPT, g._MATCH_SCHEMA),
               "injection": h(g._INJECTION_PROMPT, g._INJECTION_SCHEMA)}
    tag = f"groq:{s.llm_verifier_model}+{s.injection_guard_model}"
    return Cached(g.GroqVerifier(client, s.llm_verifier_model),
                  g.GroqInjectionDetector(client, s.llm_verifier_model, s.injection_guard_model),
                  tag, prompts, use_cache)


def load_cases() -> list[dict]:
    cases = []
    for split, folder in (("development", CASES), ("held-out", HELDOUT)):
        for f in sorted(folder.glob("*.yaml")):
            cases += [{**c, "split": split} for c in yaml.safe_load(f.read_text())["cases"]]
    ids = [c["id"] for c in cases]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise SystemExit(f"duplicate case ids: {sorted(dupes)}")
    return cases


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson score interval for k successes out of n (good for small n and 0 or n)."""
    if n == 0:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def to_inputs(case: dict):
    mandate = Mandate.model_validate(case["mandate"])
    cur = case.get("currency", "USD")
    lines = [ResolvedLine(sku=ln["sku"], title=ln["title"],
                          unit_price=Money(amount=ln["unit_price"], currency=cur),
                          qty=ln["qty"], item=ln.get("item", 0)) for ln in case["cart"]]
    prior = case.get("prior") or {}
    state = SpendState(spent=Decimal(str(prior.get("spent", "0"))),
                       purchases=prior.get("purchases", 0),
                       qty_by_item={int(k): v for k, v in (prior.get("qty_by_item") or {}).items()})
    now = datetime.fromisoformat(case["now"]) if case.get("now") else EVAL_NOW
    return mandate, lines, state, now


async def run(verifier_name: str, use_cache: bool, split: str = "all",
              refresh: bool = False) -> dict:
    checker = build(verifier_name, use_cache)
    checker.refresh = refresh
    thresholds = Thresholds()
    results = []
    for case in load_cases():
        if split != "all" and case["split"] != split:
            continue
        mandate, lines, state, now = to_inputs(case)
        checker.case_files = []
        v = await evaluate_cart(mandate, case["merchant"], lines, state, now, checker, checker,
                                case.get("context_texts") or [], thresholds)
        results.append({"id": case["id"], "category": case["category"], "split": case["split"],
                        "expected": case["expected"], "got": v.decision, "reasons": v.reasons,
                        "notes": case.get("notes", ""),
                        "answers": [str(f.relative_to(ROOT)) for f in checker.case_files]})
    http_calls = 0
    if verifier_name == "llm":
        from gateway.verifier.groq import GroqClient

        http_calls = GroqClient.calls
    return {"verifier": checker.tag, "thresholds": thresholds.__dict__, "results": results,
            "model_calls": http_calls, "cache_hits": checker.hits, "_used": checker.used,
            "failures": checker.failures}


def metrics(results: list[dict]) -> dict:
    n = len(results) or 1
    confusion = {e: Counter() for e in DECISIONS}
    for r in results:
        confusion[r["expected"]][r["got"]] += 1
    per = {}
    for d in DECISIONS:
        tp = confusion[d][d]
        predicted = sum(confusion[e][d] for e in DECISIONS)
        actual = sum(confusion[d].values())
        per[d] = {"precision": tp / predicted if predicted else None,
                  "recall": tp / actual if actual else None, "support": actual}
    should_stop = [r for r in results if r["expected"] != "approve"]
    should_pass = [r for r in results if r["expected"] == "approve"]
    correct = sum(r["got"] == r["expected"] for r in results)
    unsafe = [r["id"] for r in should_stop if r["got"] == "approve"]
    alarms = [r["id"] for r in should_pass if r["got"] != "approve"]
    by_cat = defaultdict(lambda: [0, 0])
    for r in results:
        by_cat[r["category"]][0] += r["got"] == r["expected"]
        by_cat[r["category"]][1] += 1
    return {
        "n": n,
        "accuracy": correct / n,
        "accuracy_ci": wilson(correct, n),
        # Safety: a cart that should have been stopped but was auto-approved.
        "unsafe_approvals": unsafe,
        "should_stop": len(should_stop),
        "stop_recall": (len(should_stop) - len(unsafe)) / len(should_stop) if should_stop else None,
        "unsafe_rate_ci": wilson(len(unsafe), len(should_stop)),
        # Friction: a good cart that was held or blocked.
        "false_alarms": alarms,
        "should_pass": len(should_pass),
        "false_alarm_rate_ci": wilson(len(alarms), len(should_pass)),
        "per_decision": per,
        "confusion": {e: dict(confusion[e]) for e in DECISIONS},
        "by_category": {k: f"{a}/{b}" for k, (a, b) in sorted(by_cat.items())},
    }


def pct(x):
    return "n/a" if x is None else f"{100 * x:.0f}%"


def ci(interval) -> str:
    return "" if not interval else f" (95% CI {pct(interval[0])}–{pct(interval[1])})"


def summary_table(results: list[dict]) -> list[str]:
    """One row per split, with 95% confidence intervals: the headline numbers."""
    rows = ["| Split | Cases | Exact decision | Unsafe auto-approvals | False alarms |",
            "|---|---|---|---|---|"]
    for split in ("development", "held-out", "all"):
        rs = [r for r in results if split == "all" or r["split"] == split]
        if not rs:
            continue
        m = metrics(rs)
        rows.append(
            f"| {split} | {m['n']} | {pct(m['accuracy'])}{ci(m['accuracy_ci'])} | "
            f"{len(m['unsafe_approvals'])}/{m['should_stop']}{ci(m['unsafe_rate_ci'])} | "
            f"{len(m['false_alarms'])}/{m['should_pass']}{ci(m['false_alarm_rate_ci'])} |")
    return rows


def report(run_out: dict, m: dict) -> str:
    lines = [
        f"- Cases: **{m['n']}**  ·  verifier: `{run_out['verifier']}`  ·  thresholds: "
        + ", ".join(f"{k}={v}" for k, v in run_out["thresholds"].items()),
        "",
        *summary_table(run_out["results"]),
        "",
        ("Unsafe auto-approval: a cart that should have been stopped (blocked or held for the "
        "person) but was paid without asking. False alarm: a good cart that was held or blocked."),
        "",
        (f"Unsafe auto-approvals: {', '.join(f'`{i}`' for i in m['unsafe_approvals']) or 'none'}. "
        f"False alarms: {', '.join(f'`{i}`' for i in m['false_alarms']) or 'none'}."),
        "",
        "All cases:",
        "",
        "| Expected \\ Got | approve | escalate | block | precision | recall |",
        "|---|---|---|---|---|---|",
    ]
    for e in DECISIONS:
        c = m["confusion"][e]
        p = m["per_decision"][e]
        lines.append(f"| **{e}** ({p['support']}) | {c.get('approve', 0)} | {c.get('escalate', 0)} | "
                     f"{c.get('block', 0)} | {pct(p['precision'])} | {pct(p['recall'])} |")
    lines += ["", "| Category | Development | Held-out |", "|---|---|---|"]
    dev_rs = [r for r in run_out["results"] if r["split"] == "development"]
    dev = metrics(dev_rs)["by_category"] if dev_rs else {}
    held_rs = [r for r in run_out["results"] if r["split"] == "held-out"]
    held = metrics(held_rs)["by_category"] if held_rs else {}
    for k in sorted(set(dev) | set(held)):
        lines.append(f"| {k} | {dev.get(k, '—')} | {held.get(k, '—')} |")
    misses = [r for r in run_out["results"] if r["got"] != r["expected"]]
    if misses:
        lines += ["", "Misses:", ""]
        lines += [f"- `{r['id']}` ({r['split']}): expected **{r['expected']}**, got **{r['got']}** "
                  f"({'; '.join(r['reasons'])[:160]})" for r in misses]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verifier", choices=["stub", "llm"], default="stub")
    ap.add_argument("--no-cache", action="store_true", help="ignore cached model answers")
    ap.add_argument("--refresh", action="store_true",
                    help="ask the model again and overwrite cached answers (keeps raw replies)")
    ap.add_argument("--split", choices=["all", "development", "held-out"], default="all",
                    help="run only one split (e.g. to limit model calls)")
    ap.add_argument("--write", action="store_true", help="write docs/eval.md and eval/results-*.json")
    args = ap.parse_args()

    out = asyncio.run(run(args.verifier, not args.no_cache, args.split, args.refresh))
    if out["failures"]:
        print(f"{len(out['failures'])} model check(s) failed after retries "
              f"({out['failures'][:5]}); nothing written. Re-run: answers so far are cached.")
        sys.exit(1)
    m = metrics(out["results"])
    text = report(out, m)
    print(text)
    if args.verifier == "stub":
        print("\n(offline stub: no network calls)")
    else:
        print(f"\nGroqCloud calls made: {out['model_calls']}  ·  answers from cache: {out['cache_hits']}")
    used = out.pop("_used")
    if args.write:
        (ROOT / "eval" / f"results-{args.verifier}.json").write_text(
            json.dumps({**out, "metrics": m}, indent=1, default=str))
        # Drop cached answers no current case uses (e.g. from an earlier prompt version).
        # Only after a full run: a one-split run must not delete the other split's answers.
        for d in ({u.parent for u in used} if args.split == "all" else ()):
            for f in d.glob("*.json"):
                if f not in used:
                    f.unlink()
        name = args.verifier if args.split == "all" else f"{args.verifier} ({args.split} only)"
        write_doc(name, text)


def write_doc(verifier: str, text: str) -> None:
    doc = ROOT / "docs" / "eval.md"
    sections = {}
    if doc.exists():
        current = None
        for line in doc.read_text().splitlines():
            if line.startswith("## Verifier: "):
                current = line.removeprefix("## Verifier: ").strip()
                sections[current] = []
            elif current:
                sections[current].append(line)
    sections[verifier] = [f"_Run {datetime.now(UTC):%Y-%m-%d}_", "", text, ""]
    head = [
        "# Evaluation: does the gateway stop the right carts?", "",
        ("Labelled carts in `eval/cases/` (correct purchases, rule violations, correct-but-wrong "
        "items, extras disguised as requested items, injected and benign merchant text) run "
        "through the same decision code as live purchases (`gateway/policy/evaluate.py`). "
        "Reproduce: `uv run python eval/run_eval.py --verifier llm` (cached answers, no key needed) "
        "or `--verifier stub` (offline)."), "",
        ("The **stub** row is the offline word-overlap baseline; the **llm** row is the GroqCloud "
        "fallback verifier used in the demo. LLM probabilities are self-reported, not calibrated."),
        "",
        ("**Sets.** *Development* (`eval/cases/`): the 38-case seed set used while building "
         "and tuning (Oct 18), plus 74 real-world style cases written Oct 22 (long store titles, "
         "pack sizes, model numbers, ten product types). *Held-out* (`eval/heldout/`): 42 cases "
         "written Oct 22 before any run on them, never used to tune prompts or thresholds: new "
         "forms of hidden instructions (other languages, fake system/JSON messages, base64, "
         "spaced letters, role play, data exfiltration), extras disguised as requested items, "
         "subtle wrong items, packs that are what was asked, and ordinary shop text that must not "
         "raise an alarm. Intervals are 95% Wilson intervals; with these sample sizes they are "
         "wide, and that is the honest reading."),
        "",
("**Results (GroqCloud LLM verifier, Nov 3).** 167 cases (112 development, 55 held-out). "
         "0 of 114 carts that should have been stopped were paid without asking (95% CI "
         "0–3%); 6 of 53 good carts were held for a tap. The 13 new held-out pack-size cases "
         "(`eval/heldout/packs.yaml`, written before the pack-size code): 13 of 13 right. "
         "Remaining false alarms: shoe width \"D (Medium)\" read as not \"regular (D)\" (2), "
         "\"Shredded Blend Chicken & Rice\" read as a different Purina product than \"Chicken & "
         "Rice\" (3; arguably right, labels left unchanged), and a review mentioning \"my AI "
         "assistant\" (1)."),
        "",
        ("**Change on Nov 3: pack sizes counted in code** (`gateway/policy/units.py`). Titles "
         "and requests are read for pack sizes (\"12 Pack\", \"48 Count (Pack of 2)\", \"6 "
         "Double Rolls\", \"box of 100\"); the quantity rule counts requested items, holds a "
         "cart with more units than asked or with smaller packs than asked, and the AI is told "
         "pack sizes are checked so it judges only the product. History, reported in full: "
         "before, 0/110 unsafe and 6/44 false alarms (three of them pack sizes, all in the "
         "held-out `attacks.yaml`, so that file is no longer a clean test of this change). First "
         "wording: false alarms fell to 3/53 but the model became lenient and approved seats on "
         "the wrong product (development); a sentence keeping it strict about the product "
         "fixed that and added three dog-food holds. Two parser bugs were then fixed: a model "
         "number read as a count (\"735 Charger\"), and \"48 Count (Pack of 2) - 96 Batteries "
         "Total\" left unread (the held-out case `ho-cbw-double-pack`, approved in that run, is "
         "therefore now seen). About 190 Groq calls in total."),
        "",
        ("**Change on Nov 1: repeat-order rule.** With the Oct 23 answers, "
         "`rw-dog-autoship` (development) was approved: the model's stored reasoning reads "
         "\"Autoship: every 4 weeks\" as the same product. A deterministic rule now holds any "
         "line whose title signs the person up for repeat orders (autoship, subscription, "
         "auto-renew, every N weeks) unless the request says so. Before the rule: 1 of 110 "
         "unsafe; after: 0 of 110. It was motivated by a development case; the held-out set was "
         "re-run with it and did not change."),
        "",
        ("**Same questions, different answers.** The first run (Oct 22) and the refresh (Oct 23) "
         "asked identical prompts at temperature 0; two development cases changed. Within the "
         "refresh itself, the same dog-food cart, asked twice, was approved once and held once. Single runs of an LLM check are noisy at this "
         "level; the stored raw replies (`eval/cache/`, viewer `eval/show_case.py <case-id>`) "
         "show the model's own reasoning for every decision."),
        "",
        ("Policy rules (block cases) are deterministic code. Model answers are cached in "
         "`eval/cache/`; a fresh run with `--no-cache` may differ slightly. The evaluation "
         "retries Groq rate limits and server errors and refuses to report if a check still "
         "fails, so errors never count as results."),
        "",
    ]
    body = []
    for name in sorted(sections, key=lambda n: (not n.startswith("llm"), n)):
        body += [f"## Verifier: {name}", *sections[name]]
    doc.write_text("\n".join(head + body).rstrip() + "\n")


if __name__ == "__main__":
    main()
