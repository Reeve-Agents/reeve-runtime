"""Pick the engine and the constraint checks for a scenario's domain pack (docs/PACKS.md)."""
from __future__ import annotations

from typing import Any, Dict, List

from . import constraints, engine, generic_engine, stock_engine


def is_stock(scenario: Dict[str, Any]) -> bool:
    return scenario.get("pack") == "minimarket"


def is_generic(scenario: Dict[str, Any]) -> bool:
    return scenario.get("pack") == "generic"


def simulate(rules: Any, scenario: Dict[str, Any]) -> Dict[str, Any]:
    if is_generic(scenario):
        return generic_engine.simulate(rules, scenario)
    return stock_engine.simulate(rules, scenario) if is_stock(scenario) else engine.simulate(rules, scenario)


def check_constraints(events: List[Dict[str, Any]], scenario: Dict[str, Any], limits: Dict[str, Any]) -> List[str]:
    if is_generic(scenario):
        return constraints.check_generic_constraints(events, scenario, limits)
    if is_stock(scenario):
        return constraints.check_stock_constraints(events, scenario, limits)
    return constraints.check_constraints(events, scenario, limits)
