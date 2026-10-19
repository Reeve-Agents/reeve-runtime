"""Constraint checks over simulation events (Auditor-owned invariants)."""
from __future__ import annotations

from typing import Any, Dict, List


def check_constraints(events: List[Dict[str, Any]], scenario: Dict[str, Any], constraints: Dict[str, Any]) -> List[str]:
    violations: List[str] = []
    rent = {t["id"]: int(t["rent"]) for t in scenario["tenants"]}

    for ev in events:
        if ev.get("amount", 0) < 0:
            violations.append(f"negative amount in {ev['type']} for {ev['tenant_id']} on {ev['date']}")

    max_pct = constraints.get("max_fee_percent_of_rent")
    if max_pct is not None:
        fees: Dict[str, int] = {}
        owner: Dict[str, str] = {}
        for ev in events:
            if ev["type"] == "late_fee":
                fees[ev["invoice_id"]] = fees.get(ev["invoice_id"], 0) + int(ev["amount"])
                owner[ev["invoice_id"]] = ev["tenant_id"]
        for inv_id, total in fees.items():
            cap = rent[owner[inv_id]] * float(max_pct) / 100
            if total > cap:
                violations.append(f"[{constraints.get('max_fee_card', '?')}] invoice {inv_id}: late fees {total} exceed cap {int(cap)} ({max_pct}% of rent)")

    threshold = constraints.get("refund_approval_above")
    if threshold is not None:
        for ev in events:
            if ev["type"] == "refund_request" and int(ev["amount"]) > int(threshold) and ev.get("note") != "approval_required":
                violations.append(f"[{constraints.get('approval_card', '?')}] refund {ev['amount']} for {ev['tenant_id']} on {ev['date']} above {threshold} without owner approval")

    return violations


def check_stock_constraints(events: List[Dict[str, Any]], scenario: Dict[str, Any], limits: Dict[str, Any]) -> List[str]:
    """Minimarket invariants: no negative amounts, no markdown below cost, no PO above the limit without approval."""
    violations: List[str] = []
    cost = {i["id"]: int(i["cost"]) for i in scenario["items"]}

    for ev in events:
        if ev.get("amount", 0) < 0:
            violations.append(f"negative amount in {ev['type']} for {ev['tenant_id']} on {ev['date']}")

    if limits.get("markdown_floor") == "cost":
        for ev in events:
            if ev["type"] == "markdown" and int(ev["amount"]) < cost[ev["tenant_id"]]:
                violations.append(f"[{limits.get('floor_card', '?')}] {ev['tenant_id']} marked down to {ev['amount']} on {ev['date']}, below cost {cost[ev['tenant_id']]}")

    limit = limits.get("po_approval_above")
    if limit is not None:
        for ev in events:
            if ev["type"] == "po_created" and int(ev["amount"]) > int(limit):
                violations.append(f"[{limits.get('po_card', '?')}] PO {ev['amount']} for {ev['tenant_id']} on {ev['date']} above {limit} without owner approval")

    return violations


# ── Generic pack. The Auditor's own reading of the conditions, separate from the Builder's code. ──

def _num(v: Any) -> Any:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = "".join(ch for ch in v if ch.isdigit() or ch in ".-")
        try:
            return float(s) if s not in ("", "-", ".") else None
        except ValueError:
            return None
    return None


def _holds(c: Dict[str, Any], rec: Dict[str, Any], today: str) -> bool:
    import datetime as dt

    v = rec.get(c["field"])
    op = c["op"]
    blank = v is None or str(v).strip() == ""
    if op == "empty":
        return blank
    if op == "not_empty":
        return not blank
    if op in ("eq", "neq"):
        same = str(v if v is not None else "").strip().lower() == str(c["value"] if c["value"] is not None else "").strip().lower()
        return same if op == "eq" else not same
    if op in ("days_until_lte", "days_since_gte"):
        try:
            d = dt.date.fromisoformat(str(v)[:10])
        except ValueError:
            return False
        until = (d - dt.date.fromisoformat(today)).days
        n = _num(c["value"])
        if n is None:
            return False
        return 0 <= until <= n if op == "days_until_lte" else -until >= n
    x, n = _num(v), _num(c["value"])
    if x is None or n is None:
        return False
    return {"lt": x < n, "lte": x <= n, "gt": x > n, "gte": x >= n}[op]


def check_generic_constraints(events: List[Dict[str, Any]], scenario: Dict[str, Any], limits: Dict[str, Any]) -> List[str]:
    """No executed action above an approval limit; no executed action a "never" card forbids."""
    violations: List[str] = []
    for ev in events:
        if ev["type"] != "action":
            continue
        tool = ev.get("tool")
        for a in limits.get("approvals", []):
            amount = ev.get("amount")
            if (a["tool"] in ("*", tool)) and amount is not None and amount > a["above"]:
                violations.append(f"[{a['card']}] {tool} for {ev['tenant_id']} on {ev['date']}: amount {amount} > {a['above']} ran without the owner's approval")
        for n in limits.get("never", []):
            if n["tool"] not in ("*", tool):
                continue
            rec = ev.get("record", {})
            checks = [_holds(c, rec, ev["date"]) for c in n["conditions"]]
            hit = (all(checks) if n["match"] == "all" else any(checks)) if checks else True
            if hit:
                violations.append(f"[{n['card']}] {tool} ran for {ev['tenant_id']} on {ev['date']} although the rulebook forbids it")
    return violations
