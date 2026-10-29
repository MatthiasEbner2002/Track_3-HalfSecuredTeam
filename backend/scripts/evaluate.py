"""Score the purpose check on the evaluation set (tests/fixtures/eval/requests.json).

    .venv/bin/python scripts/evaluate.py                # all 100
    .venv/bin/python scripts/evaluate.py --split test   # held-out part only
    .venv/bin/python scripts/evaluate.py --failures     # list the wrong cases

Protocol: the statements and thresholds in app/purpose/config.yaml are set from the
law and requests/R01-R10 only. Report the "test" split as the honest number; if you
tune on failures, tune on "dev" only and say so.

A case is correct when the decision and the articles match (adding Art. 67 or 71 is
accepted, as in the challenge). Runs the decision and routing layers, not the database.
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.layers import decision, routing  # noqa: E402
from app.models import DataRequest  # noqa: E402

EVAL = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "eval" / "requests.json"
OPTIONAL = {"67", "71"}
DECISIONS = {"statistics": "APPROVE_STATISTICAL", "spe": "APPROVE_SPE"}


def run(request: DataRequest) -> tuple[str, list[str], list[str]]:
    verdict = decision.decide(request)
    if not verdict.accepted:
        return "REJECT", verdict.articles, verdict.flags
    route = routing.choose_route(request, verdict)
    extra, _ = routing.explain(request, route)
    return DECISIONS[route], list(dict.fromkeys(verdict.articles + extra)), verdict.flags


def category(expected: dict) -> str:
    if expected["prohibited_point"]:
        return f"REJECT {expected['prohibited_point']}"
    if expected["decision"] == "APPROVE_STATISTICAL" and "66" in expected["articles"]:
        return "DOWNGRADE"
    return expected["decision"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "test", "all"], default="all")
    parser.add_argument("--failures", action="store_true")
    args = parser.parse_args()

    items = [i for i in json.loads(EVAL.read_text(encoding="utf-8")) if args.split in ("all", i["split"])]
    tally: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    failures, slowest, review = [], 0.0, 0
    for item in items:
        request = DataRequest.model_validate(item["request"])
        start = time.perf_counter()
        got, articles, flags = run(request)
        slowest = max(slowest, time.perf_counter() - start)
        want = item["expected"]
        ok = got == want["decision"] and set(want["articles"]) <= set(articles) and set(articles) - set(want["articles"]) <= OPTIONAL
        review += "needs_review" in flags
        for key in ("all", f"split {item['split']}", category(want), f"lang {item['language']}", item["difficulty"]):
            tally[key][0] += ok
            tally[key][1] += 1
        if not ok:
            failures.append((item, got, articles, flags))

    print(f"{'group':22} correct")
    for key in sorted(tally, key=lambda k: (k != "all", k)):
        ok, n = tally[key]
        print(f"{key:22} {ok:3}/{n:<3} {100 * ok / n:5.1f}%")
    print(f"flagged for staff review: {review}")
    print(f"slowest decision: {slowest:.1f} s")
    if args.failures:
        print()
        for item, got, articles, flags in failures:
            r = item["request"]
            print(f"{r['id']} [{item['split']}] want {category(item['expected'])}, got {got} {articles} {flags}")
            print(f"     {r['purpose'][:160]}")


if __name__ == "__main__":
    main()
