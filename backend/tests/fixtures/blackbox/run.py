"""Black-box runner for the extraction engine.

    cd backend && .venv/bin/python tests/fixtures/blackbox/run.py
    cd backend && .venv/bin/python tests/fixtures/blackbox/run.py --verbose B023

Default output is one line per case (PASS / FAIL <category>) plus a tally. It never
prints request text, definitions, expected or actual values, so the implementer can
use it without seeing the cases. --verbose ID prints the full diff for one case.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]
CASES = HERE / "cases"
sys.path.insert(0, str(BACKEND))


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _r1(v):
    return Decimal(repr(float(v))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def same_value(exp, act) -> bool:
    if isinstance(exp, str) or isinstance(act, str):
        return exp == act
    if not (_num(exp) and _num(act)):
        return False
    if isinstance(exp, int):
        return act == exp
    return _r1(act) == _r1(exp)


def compare(case, actual) -> str | None:
    """None if equal, else the failure category."""
    exp = case["expected"]
    if not isinstance(actual, dict):
        return "wrong-groups"
    if case["mode"] == "spe":
        if "cohort_size" not in actual or "record_types" not in actual:
            return "wrong-groups"
        ok = actual.get("cohort_size") == exp["cohort_size"] and sorted(actual.get("record_types") or []) == exp["record_types"]
        return None if ok else "wrong-values"
    if set(map(str, actual)) != set(exp):
        return "wrong-groups"
    for k, ev in exp.items():
        av = actual[k] if k in actual else actual.get(str(k))
        if not isinstance(av, (list, tuple)) or len(av) != len(ev):
            return "wrong-values"
        if not all(same_value(e, a) for e, a in zip(ev, av)):
            return "wrong-values"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", metavar="ID", help="print the full diff for one case")
    args = ap.parse_args()

    try:
        from app.db import connect
        from app.layers.mapping import InterpretError
        from app.layers.query import extract
        from app.models import DataRequest
    except ImportError as exc:
        print(f"engine not importable: {exc}")
        return 2

    conn = connect()
    files = sorted(CASES.glob("B*.json"))
    if args.verbose:
        files = [f for f in files if f.stem == args.verbose]
        if not files:
            print(f"no case {args.verbose}")
            return 2

    tally: Counter[str] = Counter()
    for f in files:
        case = json.loads(f.read_text(encoding="utf-8"))
        want_error = case["expected"] == {"error": True}
        actual = None
        exc = None
        try:
            req = DataRequest(**case["request"])
            actual = extract(req, case["mode"], conn)
        except Exception as e:  # noqa: BLE001
            exc = e

        if exc is not None:
            if want_error and isinstance(exc, InterpretError):
                cat = None
            else:
                cat = f"unexpected-error {type(exc).__name__}"
        elif want_error:
            cat = "should-error"
        else:
            cat = compare(case, actual)

        if cat is None:
            print(f"{f.stem} PASS")
            tally["PASS"] += 1
        else:
            print(f"{f.stem} FAIL {cat}")
            tally[cat.split(" ")[0]] += 1

        if args.verbose:
            print("--- request")
            print(json.dumps(case["request"], ensure_ascii=False, indent=2))
            print(f"--- mode: {case['mode']}")
            if exc is not None:
                print(f"--- raised: {type(exc).__name__}: {exc}")
            exp = case["expected"]
            if want_error or exc is not None or not isinstance(actual, dict) or case["mode"] == "spe":
                print("--- expected")
                print(json.dumps(exp, ensure_ascii=False, indent=2))
                print("--- actual")
                print(json.dumps(actual, ensure_ascii=False, indent=2, default=str))
            else:
                print("--- diff (group: expected | actual)")
                for k in sorted(set(exp) | set(map(str, actual))):
                    ev, av = exp.get(k, "<missing>"), actual.get(k, "<missing>")
                    same = (
                        isinstance(ev, list) and isinstance(av, (list, tuple)) and len(ev) == len(av)
                        and all(same_value(e, a) for e, a in zip(ev, av))
                    )
                    print(f"  {'  ' if same else '! '}{k}: {ev} | {av}")

    print()
    total = sum(tally.values())
    for k in ["PASS", "wrong-groups", "wrong-values", "should-error", "unexpected-error"]:
        print(f"{k}: {tally.get(k, 0)}")
    print(f"total: {tally.get('PASS', 0)}/{total} passed")
    return 0 if tally.get("PASS", 0) == total else 1


if __name__ == "__main__":
    sys.exit(main())
