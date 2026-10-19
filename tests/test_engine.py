"""Unit tests for the fixed runtime engine (independent of any generated rules)."""
import datetime as dt
import types
import unittest

from reeve_runtime.engine import simulate, tenure_months


def make_rules(**overrides):
    rules = types.SimpleNamespace(
        issues_invoice_today=lambda today: today.day == 1,
        reminder_due=lambda d: False,
        late_fee_amount=lambda inv, t, d, today: 0,
        notice_due=lambda d: False,
        handle_overpayment=lambda amount, t: ("credit", amount, False),
        kpi_check_today=lambda today: False,
        kpi_alert=lambda c, i, today: False,
    )
    for k, v in overrides.items():
        setattr(rules, k, v)
    return rules


def scenario(payments, days=35):
    return {
        "start_date": "2026-11-01",
        "days": days,
        "tenants": [{"id": "t1", "name": "t1", "room": "1", "rent": 1000, "move_in": "2026-01-01"}],
        "payments": payments,
    }


class EngineTests(unittest.TestCase):
    def test_tenure_months(self):
        self.assertEqual(tenure_months(dt.date(2025, 11, 1), dt.date(2026, 11, 1)), 12)
        self.assertEqual(tenure_months(dt.date(2025, 11, 2), dt.date(2026, 11, 1)), 11)

    def test_overpayment_credit_is_applied_to_next_invoice(self):
        out = simulate(make_rules(), scenario([{"tenant_id": "t1", "day": 0, "amount": 1300}]))
        credit = [e for e in out["events"] if e["type"] == "credit"]
        self.assertEqual(credit[0]["amount"], 300)
        applied = [e for e in out["events"] if e.get("note") == "applied credit"]
        self.assertEqual(applied[0]["date"], "2026-12-01")
        self.assertEqual(applied[0]["amount"], 300)

    def test_payment_before_overdue_checks(self):
        seen = []
        rules = make_rules(late_fee_amount=lambda inv, t, d, today: seen.append(d) or 0)
        simulate(rules, scenario([{"tenant_id": "t1", "day": 3, "amount": 1000}], days=10))
        self.assertEqual(seen, [1, 2])  # paid on day 3, so no overdue check that day


if __name__ == "__main__":
    unittest.main()
