"""Sandbox job entry point.

Usage (cwd = bundle root, which contains ``reeve_runtime/`` and ``job/``)::

    python3 -m reeve_runtime.job job

Runs, in one process:
  1. the Builder's own unit tests (job/test_builder.py)
  2. the Auditor's independent spec (job/audit_cases.json) against job/rules.py
  3. mutation testing: the same spec against every job/mutants/*.py
  4. a full simulation of job/scenario.json plus constraint checks (job/constraints.json)
Writes job/results.json and prints it. Exit code 0 even when checks fail;
the verdict is decided by the Auditor on the host.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

from .dispatch import check_constraints, simulate
from .spec import run_cases


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_builder_tests(job: Path) -> Dict[str, Any]:
    sys.path.insert(0, str(job))
    suite = unittest.defaultTestLoader.discover(str(job), pattern="test_builder.py", top_level_dir=str(job))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    failures: List[str] = [f"{t.id()}: {err.strip().splitlines()[-1]}" for t, err in result.failures + result.errors]
    sys.path.remove(str(job))
    return {"ran": result.testsRun, "failed": len(failures), "failures": failures}


def main(argv: List[str]) -> int:
    job = Path(argv[1] if len(argv) > 1 else "job").resolve()
    cases = json.loads((job / "audit_cases.json").read_text())
    rules = load_module(job / "rules.py", "rules")

    results: Dict[str, Any] = {"builder_tests": run_builder_tests(job)}
    results["audit_spec"] = run_cases(rules, cases)

    mutants_dir = job / "mutants"
    manifest = json.loads((mutants_dir / "manifest.json").read_text()) if (mutants_dir / "manifest.json").exists() else []
    survivors = []
    killed = 0
    for m in manifest:
        try:
            mod = load_module(mutants_dir / m["file"], "mutant_" + m["id"])
            outcome = run_cases(mod, cases)
            is_killed = outcome["failed"] > 0
        except Exception:
            is_killed = True  # a mutant that cannot even load is detected
        if is_killed:
            killed += 1
        else:
            survivors.append({"id": m["id"], "description": m["description"]})
    results["mutation"] = {"total": len(manifest), "killed": killed, "survivors": survivors}

    scenario = json.loads((job / "scenario.json").read_text())
    sim = simulate(rules, scenario)
    constraints = json.loads((job / "constraints.json").read_text())
    results["constraints"] = {"violations": check_constraints(sim["events"], scenario, constraints)}
    results["simulation"] = sim

    out = json.dumps(results, indent=2)
    (job / "results.json").write_text(out)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
