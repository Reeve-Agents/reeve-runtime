"""Auditor spec harness: run expectation cases against a rules module.

A case is::

    {"name": "...", "card": "R4", "scenario": {...},
     "expect": [{"date": "...", "tenant_id": "...", "type": "late_fee", "amount": 75000}, ...],
     "types": ["late_fee", "notice"]}

Only events whose type is in ``types`` are compared, so each case checks one behaviour.
Expectations are written by the Auditor from the rulebook, never from the Builder's code.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .dispatch import simulate


def _key(ev: Dict[str, Any], ignore_amount: Any) -> Tuple[Any, ...]:
    amount = None if ev["type"] in ignore_amount else ev.get("amount")
    return (ev["date"], ev["tenant_id"], ev["type"], amount, ev.get("note"))


def run_case(rules: Any, case: Dict[str, Any]) -> List[str]:
    """Return a list of human-readable mismatches (empty = pass)."""
    try:
        result = simulate(rules, case["scenario"])
    except Exception as exc:  # a crashing rules module fails the case
        return [f"{case['name']} [{case['card']}]: crashed with {type(exc).__name__}: {exc}"]
    types = set(case["types"])
    ignore = set(case.get("ignore_amount", []))
    # Generic cases pin one card: only events that card decided are compared.
    only = set(case.get("rules", []))
    got = sorted((_key(e, ignore) for e in result["events"] if e["type"] in types and (not only or e.get("rule") in only)), key=repr)
    want = sorted((_key(e, ignore) for e in case["expect"]), key=repr)
    if got == want:
        return []
    missing = [w for w in want if w not in got]
    extra = [g for g in got if g not in want]
    problems = []
    for m in missing:
        problems.append(f"{case['name']} [{case['card']}]: expected {m[2]} for {m[1]} on {m[0]} (amount={m[3]}, note={m[4]}) but it did not happen")
    for x in extra:
        problems.append(f"{case['name']} [{case['card']}]: unexpected {x[2]} for {x[1]} on {x[0]} (amount={x[3]}, note={x[4]})")
    return problems


def run_cases(rules: Any, cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    failures: List[str] = []
    failed = 0
    for case in cases:
        problems = run_case(rules, case)
        if problems:
            failed += 1
            failures.extend(problems)
    return {"ran": len(cases), "failed": failed, "failures": failures}
