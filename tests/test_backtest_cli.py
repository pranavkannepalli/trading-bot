"""Tests for the backtest CLI entry point."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_bot.backtest import main, generate_mock_closes, load_closes_from_csv


class TestMockDataGenerator(unittest.TestCase):
    def test_deterministic_output(self):
        a = generate_mock_closes(50, seed=0.0)
        b = generate_mock_closes(50, seed=0.0)
        self.assertEqual(len(a), 50)
        self.assertEqual(a, b)

    def test_seed_changes_output(self):
        a = generate_mock_closes(50, seed=0.0)
        b = generate_mock_closes(50, seed=1.0)
        self.assertNotEqual(a, b)

    def test_monotonic_overall_trend(self):
        closes = generate_mock_closes(200, seed=0.0)
        # The mock generator has a 12% trend over the series — first < last generally.
        self.assertLess(closes[0], closes[-1])


class TestCSVLoading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8", newline=""
        )

    def tearDown(self):
        if hasattr(self, "tmp") and os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_loads_close_column(self):
        self.tmp.write("close\n100.0\n101.0\n102.0\n")
        self.tmp.close()
        closes = load_closes_from_csv(self.tmp.name)
        self.assertEqual(closes, [100.0, 101.0, 102.0])

    def test_loads_case_insensitive_close(self):
        self.tmp.write("CLOSE\n100.0\n101.0\n")
        self.tmp.close()
        closes = load_closes_from_csv(self.tmp.name)
        self.assertEqual(closes, [100.0, 101.0])

    def test_falls_back_to_first_column(self):
        self.tmp.write("price\n100.0\n101.0\n")
        self.tmp.close()
        closes = load_closes_from_csv(self.tmp.name)
        self.assertEqual(closes, [100.0, 101.0])


class TestBacktestCLI(unittest.TestCase):
    def test_mock_run_produces_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rc = main(["--out", tmpdir, "--steps", "20", "--no-report"])
            self.assertEqual(rc, 0)

            # Verify artifacts.
            art_path = os.path.join(tmpdir, "backtest_artifacts.json")
            sum_path = os.path.join(tmpdir, "backtest_summary.json")
            self.assertTrue(os.path.exists(art_path))
            self.assertTrue(os.path.exists(sum_path))

            with open(art_path) as f:
                artifacts = json.load(f)
            self.assertIn("meta", artifacts)
            self.assertIn("steps", artifacts)
            self.assertIn("final", artifacts)
            self.assertEqual(len(artifacts["steps"]), 20)
            self.assertEqual(artifacts["meta"]["strategy"], "always_buy")

            with open(sum_path) as f:
                summary = json.load(f)
            self.assertEqual(summary["steps"], 20)

    def test_csv_run_produces_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a small CSV fixture.
            csv_path = os.path.join(tmpdir, "prices.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                f.write("close\n")
                for i in range(30):  # 30 rows, so 30-10=20 steps
                    f.write(f"{100.0 + i}\n")

            rc = main(["--out", tmpdir, "--data", csv_path, "--no-report"])
            self.assertEqual(rc, 0)

            with open(os.path.join(tmpdir, "backtest_artifacts.json")) as f:
                artifacts = json.load(f)
            # 30 closes - 10 lead-in = 20 steps
            self.assertEqual(len(artifacts["steps"]), 20)
            self.assertEqual(artifacts["meta"]["data_source"], "prices.csv")

    def test_csv_too_short_returns_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "short.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                f.write("close\n100.0\n101.0\n")

            rc = main(["--out", tmpdir, "--data", csv_path, "--no-report"])
            self.assertEqual(rc, 1)

    def test_custom_quantity_and_symbol(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rc = main([
                "--out", tmpdir, "--steps", "10", "--quantity", "3",
                "--symbol", "CUSTOM", "--no-report",
            ])
            self.assertEqual(rc, 0)

            with open(os.path.join(tmpdir, "backtest_artifacts.json")) as f:
                artifacts = json.load(f)
            first_fill = artifacts["steps"][0]["fills"][0]
            self.assertEqual(first_fill["symbol"], "CUSTOM")
            self.assertEqual(first_fill["quantity"], 3.0)

    def test_seed_reproducibility(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            main(["--out", os.path.join(tmpdir, "a"), "--steps", "15", "--seed", "42", "--no-report"])
            main(["--out", os.path.join(tmpdir, "b"), "--steps", "15", "--seed", "42", "--no-report"])

            with open(os.path.join(tmpdir, "a", "backtest_artifacts.json")) as f:
                a = json.load(f)
            with open(os.path.join(tmpdir, "b", "backtest_artifacts.json")) as f:
                b = json.load(f)

            # Same seed, same steps → identical closes.
            a_closes = [s["close"] for s in a["steps"]]
            b_closes = [s["close"] for s in b["steps"]]
            self.assertEqual(a_closes, b_closes)


if __name__ == "__main__":
    unittest.main()
