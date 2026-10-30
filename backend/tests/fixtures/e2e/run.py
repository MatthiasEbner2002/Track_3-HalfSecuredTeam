"""End-to-end black-box runner: the whole pipeline on the cases E001..E200.

    cd backend && .venv/bin/python tests/fixtures/e2e/run.py                 # all, needs Ollama
    cd backend && .venv/bin/python tests/fixtures/e2e/run.py E001-E020 E150  # a range / single ids
    cd backend && .venv/bin/python tests/fixtures/e2e/run.py --no-llm        # routing + data only
    cd backend && .venv/bin/python tests/fixtures/e2e/run.py --verbose E042

One line per case: PASS, or FAIL with what differs (decision / articles / answer).
--no-llm replaces the decision layer by the expected verdict, so the run is fast and
checks only routing and data extraction; the decision column is then marked "given".
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]
CASES = HERE / "cases"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(HERE.parent / "blackbox"))

from run import compare as compare_data  # noqa: E402  (blackbox/run.py, same answer contract)


def select(ids: list[str]) -> list[Path]:
    files = sorted(CASES.glob("E*.json"))
    if not ids:
        return files
    wanted: set[str] = set()
    for token in ids:
        if m := re.fullmatch(r"(E\d{3})-(E\d{3})", token):
            lo, hi = int(m[1][1:]), int(m[2][1:])
            wanted |= {f"E{n:03d}" for n in range(lo, hi + 1)}
        else:
            wanted.add(token)
    return [f for f in files if f.stem in wanted]


def check(case: dict, result: dict, decision_given: bool) -> list[str]:
    exp = case["expected"]
    problems = []
    if not decision_given and result["decision"] != exp["decision"]:
        problems.append(f"decision {result['decision']} (expected {exp['decision']})")
    if sorted(result["articles"]) != sorted(exp["articles"]):
        problems.append(f"articles {','.join(result['articles'])} (expected {','.join(exp['articles'])})")
    if exp["decision"] == "REJECT" or result["decision"] == "REJECT":
        return problems
    if exp["answer"] == {"error": True}:
        if result.get("data") or not any(f.startswith("not_interpreted") for f in result.get("flags", [])):
            problems.append("answer: expected the data layer to refuse (not_interpreted)")
        return problems
    mode = "spe" if exp["decision"] == "APPROVE_SPE" else "statistics"
    if cat := compare_data({"mode": mode, "expected": exp["answer"]}, result.get("data")):
        problems.append(f"answer {cat}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*", help="case ids or ranges, e.g. E001-E020")
    ap.add_argument("--no-llm", action="store_true", help="skip the decision layer, use the expected verdict")
    ap.add_argument("--verbose", action="store_true", help="print expected and actual for failures")
    args = ap.parse_args()

    from app import pipeline
    from app.db import connect
    from app.layers import decision
    from app.models import DataRequest, Decision

    files = select(args.ids)
    if not files:
        print("no cases selected")
        return 2
    conn = connect()
    tally: Counter[str] = Counter()
    slowest = 0.0
    for f in files:
        case = json.loads(f.read_text(encoding="utf-8"))
        exp = case["expected"]
        if args.no_llm:
            accepted = exp["decision"] != "REJECT"
            decision.decide = lambda req, _a=accepted: Decision(  # type: ignore[assignment]
                accepted=_a, articles=["53"] if _a else ["54"], reason="(decision given by the test)"
            )
        start = time.time()
        try:
            result = pipeline.run(DataRequest(**case["request"]), conn).model_dump()
            problems = check(case, result, args.no_llm)
        except Exception as e:  # noqa: BLE001
            result, problems = None, [f"crashed {type(e).__name__}: {e}"]
        took = time.time() - start
        slowest = max(slowest, took)
        if problems:
            tally["FAIL"] += 1
            print(f"{f.stem} FAIL {'; '.join(problems)}  [{took:.0f}s]")
            if args.verbose and result is not None:
                print("  expected:", json.dumps({k: exp[k] for k in ("decision", "articles")}), exp["answer"] and json.dumps(exp["answer"], ensure_ascii=False)[:400])
                print("  actual:  ", json.dumps({k: result[k] for k in ("decision", "articles", "flags")}), result.get("data") and json.dumps(result["data"], ensure_ascii=False, default=str)[:400])
                print("  reason:  ", result["reason"][:300])
        else:
            tally["PASS"] += 1
            print(f"{f.stem} PASS  [{took:.0f}s]")

    total = sum(tally.values())
    print(f"\npassed {tally['PASS']}/{total}  failed {tally['FAIL']}  slowest {slowest:.1f}s"
          + ("  (decision given, routing + data only)" if args.no_llm else ""))
    return 0 if tally["FAIL"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
