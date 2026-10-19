"""Operator job entry point: run the AUDITED rules over what really happened.

Usage (cwd = bundle root, which contains ``reeve_runtime/`` and ``job/``)::

    python3 -m reeve_runtime.operate job

Inputs: job/rules.py (byte-identical to the approved run; the host checks its hash
against the receipt), job/scenario.json (real tenants and the payments the rail has
confirmed, on Reeve's business clock) and job/constraints.json. No keys, no network.

Output: job/operate.json with every event up to the clock and any constraint
violations. The broker executes only the new events, and nothing if a constraint
is violated. Python 3.9 compatible.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from .dispatch import check_constraints, simulate
from .job import load_module


def main(argv: List[str]) -> int:
    job = Path(argv[1] if len(argv) > 1 else "job").resolve()
    rules = load_module(job / "rules.py", "rules")
    scenario = json.loads((job / "scenario.json").read_text())
    limits = json.loads((job / "constraints.json").read_text())
    sim = simulate(rules, scenario)
    out: Dict[str, Any] = {
        "events": sim["events"],
        "summary": sim["summary"],
        "violations": check_constraints(sim["events"], scenario, limits),
    }
    text = json.dumps(out, indent=2)
    (job / "operate.json").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
