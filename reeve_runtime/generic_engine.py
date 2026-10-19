"""Generic engine: any SOP of the form "on a schedule, for every record where conditions hold, do X".

The engine is fixed; the business logic lives in a generated ``rules`` module that must define:

    RULE_IDS, KPI_IDS
    fires(rule_id, today) -> bool
    applies(rule_id, record, today) -> bool
    once(rule_id) -> bool
    action(rule_id, record) -> (connector, tool, args: dict, amount or None)
    blocked(tool, record, today) -> bool          # "never" constraints
    needs_approval(tool, amount) -> bool          # approval limits
    kpi_fires(kpi_id, today) -> bool
    kpi_value(kpi_id, records, today) -> number
    kpi_alert(kpi_id, value) -> bool

Each day: field updates for that day, then every rule in order over every record (sorted by key),
then the KPIs. An action is never executed here: it becomes an event the broker may execute.
Python 3.9 compatible.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Set, Tuple


def simulate(rules: Any, scenario: Dict[str, Any]) -> Dict[str, Any]:
    start = dt.date.fromisoformat(scenario["start_date"])
    key = scenario["key_field"]
    records: Dict[str, Dict[str, Any]] = {str(r[key]): dict(r) for r in scenario["records"]}
    updates: Dict[int, List[Dict[str, Any]]] = {}
    for u in scenario.get("updates", []):
        updates.setdefault(int(u["day"]), []).append(u)

    events: List[Dict[str, Any]] = []
    fired: Set[Tuple[str, str]] = set()

    def emit(date: dt.date, subject: str, kind: str, **kw: Any) -> None:
        ev = {"date": date.isoformat(), "tenant_id": subject, "type": kind}
        ev.update({k: v for k, v in kw.items() if v is not None})
        events.append(ev)

    for offset in range(int(scenario["days"])):
        today = start + dt.timedelta(days=offset)
        for u in updates.get(offset, []):
            if str(u["key"]) in records:
                records[str(u["key"])][u["field"]] = u["value"]

        for rid in rules.RULE_IDS:
            if not rules.fires(rid, today):
                continue
            for k in sorted(records):
                rec = records[k]
                if not rules.applies(rid, rec, today):
                    continue
                if rules.once(rid) and (rid, k) in fired:
                    continue
                fired.add((rid, k))
                connector, tool, args, amount = rules.action(rid, rec)
                common = {"rule": rid, "tool": tool, "note": tool, "args": args, "amount": amount}
                if rules.blocked(tool, rec, today):
                    emit(today, k, "blocked", **common)
                elif rules.needs_approval(tool, amount):
                    emit(today, k, "approval_request", **common)
                else:
                    emit(today, k, "action", connector=connector, record=dict(rec), **common)

        for kid in rules.KPI_IDS:
            if rules.kpi_fires(kid, today):
                value = rules.kpi_value(kid, list(records.values()), today)
                if rules.kpi_alert(kid, value):
                    emit(today, "-", "kpi_alert", rule=kid, note=kid, amount=value)

    summary = {
        "records": len(records),
        "actions": sum(1 for e in events if e["type"] == "action"),
        "approval_requests": sum(1 for e in events if e["type"] == "approval_request"),
        "blocked": sum(1 for e in events if e["type"] == "blocked"),
        "kpi_alerts": sum(1 for e in events if e["type"] == "kpi_alert"),
    }
    return {"events": events, "summary": summary}
