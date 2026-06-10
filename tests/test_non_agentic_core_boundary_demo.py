import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from trading_bot.demo_non_agentic_boundary import main


class TestNonAgenticCoreBoundaryDemo(unittest.TestCase):
    def test_demo_writes_artifacts_and_applies_counsel_risk_cap(self):
        with tempfile.TemporaryDirectory() as d:
            rc = main(
                [
                    "--out",
                    d,
                    "--case",
                    "counsel_strict_delta",
                    "--steps",
                    "40",
                    "--quantity",
                    "10",
                    "--core-max-abs-total-delta",
                    "1000",
                    "--max-abs-total-delta-from-counsel",
                    "2.0",
                ]
            )
            self.assertEqual(rc, 0)

            p = os.path.join(d, "non_agentic_core_boundary_demo.json")
            self.assertTrue(os.path.exists(p))
            with open(p, "r", encoding="utf-8") as f:
                j = json.load(f)

            steps = j["steps"]
            self.assertGreater(len(steps), 0)

            decisions = [dec for s in steps for dec in s["decisions"]]
            allowed = [dec for dec in decisions if dec["allowed"]]
            blocked = [dec for dec in decisions if not dec["allowed"]]
            self.assertGreater(len(blocked), 0)
            self.assertGreater(len(allowed), 0)

            cap = allowed[0]["caps"]["max_abs_total_delta"]
            eps = 1e-9
            self.assertTrue(all(dec["risk"]["delta_total_abs"] <= cap + eps for dec in allowed))

            # Evidence that greeks were computed and persisted.
            first_step = steps[0]
            self.assertIn("option", first_step)
            self.assertIn("greeks", first_step["option"])
            self.assertIn("delta", first_step["option"]["greeks"])


    def test_demo_script_can_run_as_one_command(self):
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as d:
            cmd = [
                "python3",
                "trading_bot/demo_non_agentic_boundary.py",
                "--out",
                d,
                "--case",
                "counsel_strict_delta",
                "--steps",
                "20",
                "--quantity",
                "5",
                "--core-max-abs-total-delta",
                "1000",
                "--max-abs-total-delta-from-counsel",
                "2.0",
            ]
            res = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
            self.assertEqual(
                res.returncode,
                0,
                msg=f"CLI run failed. stdout:\n{res.stdout}\nstderr:\n{res.stderr}",
            )

            p = os.path.join(d, "non_agentic_core_boundary_demo.json")
            self.assertTrue(os.path.exists(p))


if __name__ == "__main__":
    unittest.main()
