"""Billing simulation engine.

The engine is fixed; the business logic lives in a generated ``rules`` module that must define:

    issues_invoice_today(today) -> bool
    reminder_due(days_overdue) -> bool
    late_fee_amount(invoice, tenant, days_overdue, today) -> int
    notice_due(days_overdue) -> bool
    handle_overpayment(amount, tenant) -> (kind, amount, needs_approval)   kind: "credit" | "refund_request"
    kpi_check_today(today) -> bool
    kpi_alert(collected_mtd, invoiced_mtd, today) -> bool

Python 3.9 compatible (local dev); sandboxes run 3.12.
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class Invoice:
    id: str
    tenant_id: str
    issued: dt.date
    amount: int
    fee: int = 0
    paid: int = 0
    fee_charged: bool = False
    notice_sent: bool = False

    @property
    def balance(self) -> int:
        return self.amount + self.fee - self.paid

    @property
    def outstanding_principal(self) -> int:
        return max(self.amount - self.paid, 0)


@dataclass
class TenantState:
    id: str
    name: str
    room: str
    rent: int
    move_in: dt.date
    credit: int = 0
    invoices: List[Invoice] = field(default_factory=list)


def parse_date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def tenure_months(move_in: dt.date, today: dt.date) -> int:
    months = (today.year - move_in.year) * 12 + (today.month - move_in.month)
    if today.day < move_in.day:
        months -= 1
    return max(months, 0)


def simulate(rules: Any, scenario: Dict[str, Any]) -> Dict[str, Any]:
    start = parse_date(scenario["start_date"])
    tenants = {
        t["id"]: TenantState(t["id"], t["name"], t["room"], int(t["rent"]), parse_date(t["move_in"]))
        for t in scenario["tenants"]
    }
    payments: Dict[int, List[Dict[str, Any]]] = {}
    for p in scenario["payments"]:
        payments.setdefault(int(p["day"]), []).append(p)

    events: List[Dict[str, Any]] = []
    month_collected: Dict[str, int] = {}
    month_invoiced: Dict[str, int] = {}

    def emit(date: dt.date, tenant_id: str, kind: str, **kw: Any) -> None:
        ev = {"date": date.isoformat(), "tenant_id": tenant_id, "type": kind}
        ev.update({k: v for k, v in kw.items() if v is not None})
        events.append(ev)

    def apply_payment(t: TenantState, amount: int, today: dt.date, note: str = "") -> int:
        remaining = amount
        for inv in sorted(t.invoices, key=lambda i: i.issued):
            if remaining <= 0:
                break
            if inv.balance <= 0:
                continue
            pay = min(remaining, inv.balance)
            inv.paid += pay
            remaining -= pay
            emit(today, t.id, "payment", amount=pay, invoice_id=inv.id, note=note or None)
        return remaining

    for offset in range(int(scenario["days"])):
        today = start + dt.timedelta(days=offset)
        mkey = today.strftime("%Y-%m")

        # 1. Invoices
        if rules.issues_invoice_today(today):
            for t in tenants.values():
                inv = Invoice(id=f"{t.id}-{mkey}", tenant_id=t.id, issued=today, amount=t.rent)
                t.invoices.append(inv)
                month_invoiced[mkey] = month_invoiced.get(mkey, 0) + inv.amount
                emit(today, t.id, "invoice", amount=inv.amount, invoice_id=inv.id)
                if t.credit > 0:
                    used = min(t.credit, inv.balance)
                    t.credit -= used
                    inv.paid += used
                    emit(today, t.id, "payment", amount=used, invoice_id=inv.id, note="applied credit")

        # 2. Payments received today
        for p in payments.get(offset, []):
            t = tenants[p["tenant_id"]]
            amount = int(p["amount"])
            month_collected[mkey] = month_collected.get(mkey, 0) + amount
            leftover = apply_payment(t, amount, today)
            if leftover > 0:
                kind, amt, needs_approval = rules.handle_overpayment(leftover, t)
                if kind == "credit":
                    t.credit += amt
                    emit(today, t.id, "credit", amount=amt)
                else:
                    emit(today, t.id, "refund_request", amount=amt,
                         note="approval_required" if needs_approval else "auto_approved")

        # 3. Overdue handling
        for t in tenants.values():
            for inv in t.invoices:
                if inv.balance <= 0:
                    continue
                days_overdue = (today - inv.issued).days
                if days_overdue <= 0:
                    continue
                if rules.reminder_due(days_overdue):
                    emit(today, t.id, "reminder", invoice_id=inv.id, amount=inv.balance)
                fee = int(rules.late_fee_amount(inv, t, days_overdue, today))
                if fee > 0:
                    inv.fee += fee
                    inv.fee_charged = True
                    emit(today, t.id, "late_fee", amount=fee, invoice_id=inv.id)
                if rules.notice_due(days_overdue) and not inv.notice_sent:
                    inv.notice_sent = True
                    emit(today, t.id, "notice", invoice_id=inv.id)

        # 4. KPI monitor
        if rules.kpi_check_today(today):
            if rules.kpi_alert(month_collected.get(mkey, 0), month_invoiced.get(mkey, 0), today):
                emit(today, "-", "kpi_alert", amount=month_collected.get(mkey, 0), note=mkey)

    summary = {
        "invoiced": sum(e.get("amount", 0) for e in events if e["type"] == "invoice"),
        "collected": sum(int(p["amount"]) for p in scenario["payments"]),
        "fees": sum(e.get("amount", 0) for e in events if e["type"] == "late_fee"),
        "credits": sum(e.get("amount", 0) for e in events if e["type"] == "credit"),
        "refund_requests": sum(1 for e in events if e["type"] == "refund_request"),
        "kpi_alerts": sum(1 for e in events if e["type"] == "kpi_alert"),
    }
    return {"events": events, "summary": summary}


def days_in_month(today: dt.date) -> int:
    return calendar.monthrange(today.year, today.month)[1]
