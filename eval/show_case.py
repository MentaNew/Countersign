"""Show what the models were asked and exactly what they replied, for one evaluation case.

    uv run python eval/show_case.py ho-ok-12-pack          # one case (after an llm run)
    uv run python eval/show_case.py ho-ok-12-pack --json   # the stored records as JSON

Reads eval/results-llm.json (which answers each case used) and the cached answer files in
eval/cache/, where every exchange with Groq is kept: exact request body, exact response text,
status, timing and attempt (failed attempts included).
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case_id")
    ap.add_argument("--json", action="store_true", help="print the stored records as JSON")
    args = ap.parse_args()

    results = json.loads((ROOT / "eval" / "results-llm.json").read_text())["results"]
    case = next((r for r in results if r["id"] == args.case_id), None)
    if case is None:
        print(f"no case {args.case_id} in eval/results-llm.json")
        return 1
    records = []
    for rel in case.get("answers", []):
        path = ROOT / rel
        records.append({"file": rel, **(json.loads(path.read_text()) if path.exists() else {})})
    if args.json:
        print(json.dumps({"case": case, "answers": records}, indent=1, ensure_ascii=False))
        return 0

    print(f"{case['id']} ({case['split']}, {case['category']}): expected {case['expected']}, "
          f"got {case['got']}")
    for r in case["reasons"]:
        print(f"  - {r}")
    for rec in records:
        print(f"\n=== {rec.get('kind', '?')} check  ·  {rec['file']}")
        if "exchanges" not in rec:
            print("  (answer cached before raw replies were kept; re-run with --refresh)")
            print(f"  parsed answer: {json.dumps(rec.get('verdict', rec))}")
            continue
        for ex in rec["exchanges"]:
            req = ex["request"]
            print(f"\n--- {req.get('model')}  ·  attempt {ex['attempt']}  ·  HTTP {ex['status']}  "
                  f"·  {ex['latency_ms']} ms  ·  {ex['started_at']}")
            for m in req.get("messages", []):
                text = m["content"] if len(m["content"]) < 1500 else m["content"][:1500] + " …"
                print(f"[{m['role']}]\n{text}")
            print("[raw response]")
            try:
                body = json.loads(ex["response_text"])
                msg = body["choices"][0]["message"]
                if msg.get("reasoning"):
                    print(f"(reasoning) {msg['reasoning']}")
                print(f"(content) {msg.get('content')}")
                print(f"(usage) {json.dumps(body.get('usage'))}")
            except (ValueError, KeyError, IndexError, TypeError):
                print(ex["response_text"])
        print(f"\n[parsed answer] {json.dumps(rec['verdict'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
