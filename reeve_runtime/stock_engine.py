"""Minimarket stock and expiry simulation engine (pack 2, docs/PACKS.md).

The engine is fixed; the business logic lives in a generated ``rules`` module that must define:

    default_supplier() -> str
    backup_supplier() -> str | None
    below_reorder_point(on_hand, on_order) -> bool
    is_slow_mover(days_since_sale) -> bool
    reorder_due(on_hand, on_order, days_since_sale) -> bool
    order_quantity(on_hand, on_order) -> int
    po_needs_approval(value) -> bool
    switch_supplier(days_late) -> bool
    markdown_percent(days_to_expiry) -> int
    markdown_price(price, cost, percent) -> int
    counts_as_expiring(days_to_expiry) -> bool
    kpi_check_today(today) -> bool
    kpi_alert(value) -> bool

Each day runs in a fixed order: deliveries, late suppliers, sales (first-expiry-first-out),
expiry write-offs, markdowns, reorders, status changes, the day's close and the KPI check.

Python 3.9 compatible (local dev); sandboxes run 3.12.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Batch:
    units: int
    expires: dt.date
    percent: int = 0


@dataclass
class Item:
    id: str
    name: str
    cost: int
    price: int
    shelf_life_days: int
    last_sold: dt.date
    batches: List[Batch] = field(default_factory=list)
    stopped: bool = False
    awaiting_approval: bool = False
    status: str = ""

    @property
    def on_hand(self) -> int:
        return sum(b.units for b in self.batches)


@dataclass
class Order:
    item_id: str
    supplier: str
    qty: int
    value: int
    expected: dt.date
    arrives: dt.date
    open: bool = True


def parse_date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def simulate(rules: Any, scenario: Dict[str, Any]) -> Dict[str, Any]:
    start = parse_date(scenario["start_date"])
    lead = {s["id"]: int(s["lead_days"]) for s in scenario["suppliers"]}
    delays = scenario.get("delays", [])
    items: Dict[str, Item] = {}
    for it in scenario["items"]:
        items[it["id"]] = Item(
            id=it["id"],
            name=it["name"],
            cost=int(it["cost"]),
            price=int(it["price"]),
            shelf_life_days=int(it["shelf_life_days"]),
            last_sold=start - dt.timedelta(days=int(it["last_sold_days_ago"])),
            batches=[Batch(int(b["units"]), parse_date(b["expires"])) for b in it["batches"] if int(b["units"]) > 0],
        )
    sales: Dict[int, List[Dict[str, Any]]] = {}
    for s in scenario["sales"]:
        sales.setdefault(int(s["day"]), []).append(s)

    orders: List[Order] = []
    events: List[Dict[str, Any]] = []

    def emit(date: dt.date, subject: str, kind: str, **kw: Any) -> None:
        ev = {"date": date.isoformat(), "tenant_id": subject, "type": kind}
        ev.update({k: v for k, v in kw.items() if v is not None})
        events.append(ev)

    def extra_delay(supplier: str, offset: int) -> int:
        return sum(int(d["extra_days"]) for d in delays if d["supplier"] == supplier and int(d["from_day"]) <= offset <= int(d["to_day"]))

    def place_order(item: Item, qty: int, supplier: str, today: dt.date, offset: int) -> None:
        value = qty * item.cost
        note = f"{supplier}:{qty}"
        if rules.po_needs_approval(value):
            if not item.awaiting_approval:
                item.awaiting_approval = True
                emit(today, item.id, "po_approval_request", amount=value, note=note)
            return
        if supplier not in lead:
            raise ValueError(f"the rulebook orders from supplier {supplier!r}, but the shop's suppliers are {sorted(lead)}")
        expected = today + dt.timedelta(days=lead[supplier])
        arrives = expected + dt.timedelta(days=extra_delay(supplier, offset))
        orders.append(Order(item.id, supplier, qty, value, expected, arrives))
        emit(today, item.id, "po_created", amount=value, note=note)

    for offset in range(int(scenario["days"])):
        today = start + dt.timedelta(days=offset)

        # 1. Deliveries
        for o in orders:
            if o.open and o.arrives == today:
                o.open = False
                item = items[o.item_id]
                item.batches.append(Batch(o.qty, today + dt.timedelta(days=item.shelf_life_days)))
                emit(today, o.item_id, "po_received", amount=o.value, note=f"{o.supplier}:{o.qty}")

        # 2. Late suppliers: cancel and re-order from the backup
        backup: Optional[str] = rules.backup_supplier()
        for o in list(orders):
            if not o.open or backup is None or o.supplier == backup:
                continue
            days_late = (today - o.expected).days
            if days_late > 0 and rules.switch_supplier(days_late):
                o.open = False
                emit(today, o.item_id, "supplier_switched", amount=o.value, note=f"{o.supplier}->{backup}")
                place_order(items[o.item_id], o.qty, backup, today, offset)

        # 3. Sales, first-expiry-first-out
        revenue = 0
        units_sold = 0
        for s in sales.get(offset, []):
            item = items[s["item_id"]]
            want = int(s["units"])
            if want > 0:
                # Demand counts as "selling", even when the shelf is empty: an item out of stock is not a slow mover.
                item.last_sold = today
            for b in sorted(item.batches, key=lambda b: b.expires):
                if want <= 0:
                    break
                take = min(want, b.units)
                if take <= 0:
                    continue
                b.units -= take
                want -= take
                units_sold += take
                revenue += take * int(rules.markdown_price(item.price, item.cost, b.percent))
            item.batches = [b for b in item.batches if b.units > 0]
            if want > 0:
                emit(today, item.id, "stockout", amount=want, note=f"{want} short")
        if units_sold:
            emit(today, "-", "sales", amount=revenue, note=f"{units_sold} units")

        # 4. Expiry write-offs
        for item in items.values():
            for b in item.batches:
                if b.expires <= today and b.units > 0:
                    emit(today, item.id, "writeoff", amount=b.units * item.cost, note=f"{b.units} units")
                    b.units = 0
            item.batches = [b for b in item.batches if b.units > 0]

        # 5. Markdowns before expiry
        for item in items.values():
            for b in item.batches:
                days_to_expiry = (b.expires - today).days
                percent = int(rules.markdown_percent(days_to_expiry))
                if percent > b.percent:
                    b.percent = percent
                    new_price = int(rules.markdown_price(item.price, item.cost, percent))
                    emit(today, item.id, "markdown", amount=new_price, note=f"{percent}% off, H-{days_to_expiry}")

        # 6. Reorders
        for item in items.values():
            on_order = sum(o.qty for o in orders if o.open and o.item_id == item.id)
            since = (today - item.last_sold).days
            if rules.reorder_due(item.on_hand, on_order, since):
                if not item.awaiting_approval:
                    qty = int(rules.order_quantity(item.on_hand, on_order))
                    if qty > 0:
                        place_order(item, qty, rules.default_supplier(), today, offset)
            elif rules.is_slow_mover(since) and rules.below_reorder_point(item.on_hand, on_order) and not item.stopped:
                item.stopped = True
                emit(today, item.id, "reorder_stopped")

        # 7. Status changes, for the owner's view
        for item in items.values():
            on_order = sum(o.qty for o in orders if o.open and o.item_id == item.id)
            if item.on_hand == 0:
                status = "out"
            elif item.stopped:
                status = "stopped"
            elif any(b.percent > 0 for b in item.batches):
                status = "marked_down"
            elif rules.below_reorder_point(item.on_hand, 0):
                status = "ordered" if on_order else "low"
            else:
                status = "ok"
            if status != item.status:
                item.status = status
                emit(today, item.id, "status", amount=item.on_hand, note=status, label=item.name)

        # 8. Day close (the KPI measure) and the KPI check
        expiring = sum(
            b.units * item.cost
            for item in items.values()
            for b in item.batches
            if rules.counts_as_expiring((b.expires - today).days)
        )
        emit(today, "-", "day_close", amount=expiring)
        if rules.kpi_check_today(today) and rules.kpi_alert(expiring):
            emit(today, "-", "kpi_alert", amount=expiring)

    summary = {
        "sales": sum(e.get("amount", 0) for e in events if e["type"] == "sales"),
        "purchase_orders": sum(1 for e in events if e["type"] == "po_created"),
        "purchase_value": sum(e.get("amount", 0) for e in events if e["type"] == "po_created"),
        "approval_requests": sum(1 for e in events if e["type"] == "po_approval_request"),
        "supplier_switches": sum(1 for e in events if e["type"] == "supplier_switched"),
        "markdowns": sum(1 for e in events if e["type"] == "markdown"),
        "writeoff_value": sum(e.get("amount", 0) for e in events if e["type"] == "writeoff"),
        "stockouts": sum(1 for e in events if e["type"] == "stockout"),
        "kpi_alerts": sum(1 for e in events if e["type"] == "kpi_alert"),
    }
    return {"events": events, "summary": summary}
