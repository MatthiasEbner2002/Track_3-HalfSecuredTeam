"""Score the full decision (REJECT / APPROVE_STATISTICAL / APPROVE_SPE and articles).

    .venv/bin/python scripts/evaluate.py --set tasks --runs 3   # requests/R01-R10 vs expected.json
    .venv/bin/python scripts/evaluate.py --split test           # evaluation set, held-out part
    .venv/bin/python scripts/evaluate.py --failures             # list the wrong cases

Evaluation set: tests/fixtures/eval/requests.json (100 labelled requests). Report the
"test" split as the honest number; if you tune on failures, tune on "dev" only.

A case is correct when the decision and the articles match (adding Art. 67 or 71 is
accepted, as in the challenge). Runs the decision and routing layers (RAG + local LLM),
not the database. Every run calls the model afresh (no cache), so --runs measures
whether the model itself answers the same way each time.
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import REQUESTS_DIR  # noqa: E402
from app.layers import decision, routing  # noqa: E402
from app.models import DataRequest  # noqa: E402

EVAL = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "eval" / "requests.json"
OPTIONAL = {"67", "71"}
DECISIONS = {"statistics": "APPROVE_STATISTICAL", "spe": "APPROVE_SPE"}


def run(request: DataRequest) -> tuple[str, list[str], list[str]]:
    """One uncached decision: explicit backends bypass the caches in decide() and choose()."""
    retriever, llm = decision._default_retriever(), decision._default_llm()
    verdict = decision.decide(request, retriever, llm)
    if not verdict.accepted:
        return "REJECT", verdict.articles, verdict.flags
    choice = routing.choose(request, retriever, llm)
    return DECISIONS[choice.route], list(dict.fromkeys(verdict.articles + choice.articles)), verdict.flags + choice.flags


def category(expected: dict) -> str:
    if expected["decision"] == "REJECT":
        return f"REJECT {expected.get('prohibited_point') or ''}".strip()
    if expected["decision"] == "APPROVE_STATISTICAL" and "66" in expected["articles"]:
        return "DOWNGRADE"
    return expected["decision"]


def load(which: str, split: str) -> list[dict]:
    """Items as {"request", "expected", "split", "language", "difficulty"}."""
    if which == "tasks":
        expected = json.loads((REQUESTS_DIR / "expected.json").read_text(encoding="utf-8"))
        return [
            {"request": json.loads(p.read_text(encoding="utf-8")), "expected": expected[p.stem],
             "split": "tasks", "language": "en", "difficulty": "task"}
            for p in sorted(REQUESTS_DIR.glob("R*.json"))
        ]
    return [i for i in json.loads(EVAL.read_text(encoding="utf-8")) if split in ("all", i["split"])]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", choices=["tasks", "eval"], default="eval", dest="which")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="all", help="eval set only")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--failures", action="store_true")
    args = parser.parse_args()

    items = load(args.which, args.split)
    tally: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    failures, slowest, review, inconsistent = [], 0.0, 0, 0
    for item in items:
        request = DataRequest.model_validate(item["request"])
        results = []
        for _ in range(args.runs):
            start = time.perf_counter()
            results.append(run(request))
            slowest = max(slowest, time.perf_counter() - start)
        got, articles, flags = results[0]
        inconsistent += len({(g, tuple(a)) for g, a, _ in results}) > 1
        want = item["expected"]
        ok = got == want["decision"] and set(want["articles"]) <= set(articles) and set(articles) - set(want["articles"]) <= OPTIONAL
        review += "needs_review" in flags or "spe_quote_unverified" in flags
        for key in ("all", f"split {item['split']}", category(want), f"lang {item['language']}", item["difficulty"]):
            tally[key][0] += ok
            tally[key][1] += 1
        if not ok:
            failures.append((item, got, articles, flags))
        print(f"{request.id} {'OK ' if ok else 'BAD'} {got} {articles}"
              + ("" if ok else f"  (want {want['decision']} {want['articles']})"), flush=True)

    print(f"\n{'group':22} correct")
    for key in sorted(tally, key=lambda k: (k != "all", k)):
        ok, n = tally[key]
        print(f"{key:22} {ok:3}/{n:<3} {100 * ok / n:5.1f}%")
    print(f"flagged for staff review: {review}")
    print(f"slowest decision: {slowest:.1f} s")
    if args.runs > 1:
        print(f"inconsistent across {args.runs} runs: {inconsistent}/{len(items)}")
    if args.failures:
        print()
        for item, got, articles, flags in failures:
            r = item["request"]
            print(f"{r['id']} [{item['split']}] want {category(item['expected'])}, got {got} {articles} {flags}")
            print(f"     {r['purpose'][:160]}")


if __name__ == "__main__":
    main()
