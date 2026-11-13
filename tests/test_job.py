"""Unit tests for the mutation loop's time limit."""
import json
import signal
import tempfile
import unittest
from pathlib import Path

from reeve_runtime import job


@unittest.skipUnless(hasattr(signal, "setitimer"), "needs SIGALRM")
class MutantTimeLimit(unittest.TestCase):
    def setUp(self):
        self.saved = job.MUTANT_TIMEOUT_S
        job.MUTANT_TIMEOUT_S = 0.2
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        job.MUTANT_TIMEOUT_S = self.saved
        self.dir.cleanup()

    def write(self, source):
        path = Path(self.dir.name) / "m001.py"
        path.write_text(source)
        return path

    def test_a_mutant_that_never_finishes_is_stopped(self):
        path = self.write("while True:\n    pass\n")
        with self.assertRaises(job.MutantTimeout):
            job.run_mutant(path, "mutant_loop", [])

    def test_the_timer_is_cleared_after_a_normal_mutant(self):
        path = self.write("X = 1\n")
        self.assertEqual(job.run_mutant(path, "mutant_ok", [])["failed"], 0)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0.0)

    def test_the_time_limit_is_not_a_regular_exception(self):
        # run_cases catches Exception per case; the time limit must escape it and end the whole mutant.
        self.assertFalse(issubclass(job.MutantTimeout, Exception))


class InlineManifest(unittest.TestCase):
    """Mutant sources travel inside manifest.json (one upload); the job writes and runs each one."""

    def test_inline_sources_are_written_and_judged(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "mutants").mkdir()
            (root / "rules.py").write_text("X = 1\n")
            (root / "test_builder.py").write_text("")
            (root / "audit_cases.json").write_text("[]")
            (root / "scenario.json").write_text('{"tenants": [], "payments": [], "start": "2026-10-01", "days": 0}')
            (root / "constraints.json").write_text("{}")
            manifest = [
                {"id": "m001", "file": "m001.py", "description": "crashes on load", "source": "raise RuntimeError('planted')\n"},
                {"id": "m002", "file": "../escape.py", "description": "harmless", "source": "X = 2\n"},
            ]
            (root / "mutants" / "manifest.json").write_text(json.dumps(manifest))
            saved = job.simulate
            job.simulate = lambda rules, scenario: {"events": [], "summary": {}}
            try:
                job.main(["job", str(root)])
            finally:
                job.simulate = saved
            results = json.loads((root / "results.json").read_text())
            self.assertEqual(results["mutation"]["total"], 2)
            self.assertEqual(results["mutation"]["killed"], 1)
            self.assertEqual([s["id"] for s in results["mutation"]["survivors"]], ["m002"])
            self.assertTrue(results["mutation"]["inline_sources"])
            self.assertTrue((root / "mutants" / "escape.py").exists())  # the file name never leaves mutants/
            self.assertFalse((root / "escape.py").exists())


if __name__ == "__main__":
    unittest.main()
