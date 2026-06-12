"""Tests for Phase 4 consumer tooling: evaluate, gate, diff_reports."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_bot.evaluate import summarize_report, main as evaluate_main
from trading_bot.gate import should_promote, GateRule, GateResult, _DEFAULT_THRESHOLDS
from trading_bot.diff_reports import diff_reports, format_diff, main as diff_main


def _make_minimal_report(**overrides) -> dict:
    """Build a minimal valid evaluation report for testing."""
    report = {
        "$schema": "trading-bot-eval-report-v1",
        "meta": {
            "report_version": "1.0.0",
            "generated_at": "2026-06-12T00:00:00Z",
            "run_id": "test-run-id",
            "strategy": {"name": "test", "class": "TestStrategy", "config": {}},
            "env": {"class": "StaggeredInputEnv", "config": {}},
            "price_source": {"generator": "mock", "count": 10, "first_close": 100.0,
                             "last_close": 110.0, "min_close": 95.0, "max_close": 115.0},
            "core_boundary": {"max_abs_total_delta": 1000.0, "max_abs_total_vega": 1e9},
            "pricing": {"model": "black_scholes", "risk_free_rate": 0.01, "base_implied_vol": 0.25,
                        "iv_by_regime": {}, "expiry_years": 0.25},
        },
        "summary": {
            "start_value": 0.0,
            "final_cash": 100.0,
            "final_position": 0.0,
            "final_option_mtm": 0.0,
            "total_pnl": 100.0,
            "total_return_pct": 100.0,
            "n_steps": 60,
            "n_fills": 30,
            "n_proposed_orders": 60,
            "n_blocked_orders": 10,
            "block_rate": 0.1667,
            "cagr": None,
            "sharpe_ratio": 1.5,
            "sortino_ratio": 2.0,
            "calmar_ratio": 1.0,
            "max_drawdown_pct": -0.15,
            "max_drawdown_duration_steps": 5,
            "profit_factor": 2.0,
            "win_rate": 0.6,
            "avg_win": 10.0,
            "avg_loss": -5.0,
            "expectancy": 4.0,
            "max_consecutive_losses": 3,
            "regime_summary": {},
            "counsel_active": False,
            "counsel_total_advice_calls": 0,
            "counsel_caps_hit": 0,
            "checks_passed": 7,
            "checks_total": 7,
        },
        "performance": {},
        "risk": {
            "drawdown": {"max_drawdown_pct": -0.15, "max_drawdown_absolute": -15.0,
                         "peak_step": 10, "trough_step": 25, "recovery_step": 40,
                         "drawdown_duration_steps": 15, "drawdown_series": []},
            "greeks_exposure": {"extremes": {"max_abs_delta": 5.0, "max_abs_vega": 150.0,
                                            "max_abs_gamma": 0.5, "max_abs_theta": 30.0}},
            "boundary_enforcement": {},
        },
        "regimes": {
            "detector": "SimpleTrendVolRegimeDetector",
            "detector_config": {},
            "distribution": {"bull": {"count": 30, "pct": 0.5}, "bear": {"count": 30, "pct": 0.5}},
            "transitions": {},
            "performance": {
                "bull": {"steps": 30, "fills": 15, "total_pnl": 80.0, "mean_pnl_per_step": 2.67,
                         "blocked_orders": 5, "option_type_used": "call"},
                "bear": {"steps": 30, "fills": 15, "total_pnl": 20.0, "mean_pnl_per_step": 0.67,
                         "blocked_orders": 5, "option_type_used": "put"},
            },
        },
        "counsel": None,
        "checks": {
            "results": [
                {"id": "no_future_leakage", "passed": True, "detail": "ok"},
                {"id": "boundary_enforced", "passed": True, "detail": "ok"},
                {"id": "greeks_computed_all_steps", "passed": True, "detail": "ok"},
                {"id": "order_counts_consistent", "passed": True, "detail": "ok"},
                {"id": "regime_labels_valid", "passed": True, "detail": "ok"},
                {"id": "counsel_caps_not_exceeded", "passed": True, "detail": "ok"},
                {"id": "no_negative_position", "passed": True, "detail": "ok"},
            ],
            "summary": {"passed": 7, "failed": 0, "total": 7},
        },
    }
    for k, v in overrides.items():
        if isinstance(v, dict) and k in report:
            report[k].update(v)
        elif k in report:
            report[k] = v
    return report


class TestEvaluate(unittest.TestCase):
    def test_summarize_produces_text(self):
        report = _make_minimal_report()
        text = summarize_report(report)
        self.assertIn("TRADING BOT", text)
        self.assertIn("Total P&L", text)
        self.assertIn("Sharpe Ratio", text)
        self.assertIn("REGIME BREAKDOWN", text)
        self.assertIn("AUTOMATED CHECKS", text)
        self.assertIn("7 / 7 passed", text)

    def test_summarize_with_counsel(self):
        report = _make_minimal_report()
        report["counsel"] = {
            "active": True,
            "counsel_type": "StrictDeltaCounsel",
            "counsel_config": {"max_abs_total_delta": 2.0},
            "total_calls": 60,
            "caps_applied": {"delta_cap_hit_count": 15, "vega_cap_hit_count": 0, "quantity_clamp_count": 0},
            "advice_summary": [],
        }
        report["summary"]["counsel_active"] = True
        text = summarize_report(report)
        self.assertIn("COUNSEL AUDIT", text)
        self.assertIn("StrictDeltaCounsel", text)
        self.assertIn("Delta caps hit", text)

    def test_cli_checks_only(self):
        report = _make_minimal_report()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(report, f)
            path = f.name
        try:
            ret = evaluate_main(["--report", path, "--checks-only"])
            self.assertEqual(ret, 0)
        finally:
            os.unlink(path)

    def test_cli_checks_only_fails(self):
        report = _make_minimal_report()
        report["checks"]["results"][0]["passed"] = False
        report["checks"]["summary"]["passed"] = 6
        report["checks"]["summary"]["failed"] = 1
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(report, f)
            path = f.name
        try:
            ret = evaluate_main(["--report", path, "--checks-only"])
            self.assertEqual(ret, 1)
        finally:
            os.unlink(path)

    def test_cli_json_output(self):
        report = _make_minimal_report()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(report, f)
            path = f.name
        try:
            ret = evaluate_main(["--report", path, "--json"])
            self.assertEqual(ret, 0)
        finally:
            os.unlink(path)

    def test_cli_missing_file(self):
        ret = evaluate_main(["--report", "/nonexistent/report.json"])
        self.assertEqual(ret, 1)


class TestGate(unittest.TestCase):
    def test_promotes_good_report(self):
        report = _make_minimal_report()
        result = should_promote(report)
        self.assertTrue(result.passed)

    def test_fails_on_negative_sharpe(self):
        report = _make_minimal_report()
        report["summary"]["sharpe_ratio"] = -0.5
        result = should_promote(report)
        self.assertFalse(result.passed)
        failed = [r for r in result.rules if not r.passed]
        self.assertTrue(any("sharpe_ratio" in r.metric for r in failed))

    def test_fails_on_deep_drawdown(self):
        report = _make_minimal_report()
        report["summary"]["max_drawdown_pct"] = -0.75
        result = should_promote(report)
        self.assertFalse(result.passed)

    def test_fails_check_failure(self):
        report = _make_minimal_report()
        report["checks"]["summary"]["failed"] = 1
        result = should_promote(report)
        self.assertFalse(result.passed)

    def test_null_metric_auto_passes(self):
        report = _make_minimal_report()
        report["summary"]["sharpe_ratio"] = None
        result = should_promote(report)
        sharpe_rule = next(r for r in result.rules if r.metric == "sharpe_ratio")
        self.assertTrue(sharpe_rule.passed)
        self.assertIn("N/A", sharpe_rule.reason)

    def test_custom_thresholds(self):
        report = _make_minimal_report()
        report["summary"]["sharpe_ratio"] = 0.5
        thresholds = {
            "sharpe_ratio": {"operator": "gte", "threshold": 1.0},
        }
        result = should_promote(report, thresholds)
        self.assertFalse(result.passed)

    def test_custom_threshold_simple_format(self):
        """Numeric thresholds default to 'gte' operator."""
        report = _make_minimal_report()
        report["summary"]["win_rate"] = 0.4
        thresholds = {"win_rate": 0.5}
        result = should_promote(report, thresholds)
        self.assertFalse(result.passed)

    def test_multiple_failures(self):
        report = _make_minimal_report()
        report["summary"].update({
            "sharpe_ratio": -1.0,
            "profit_factor": 0.3,
            "win_rate": 0.2,
            "max_drawdown_pct": -0.60,
        })
        result = should_promote(report)
        self.assertFalse(result.passed)
        self.assertGreater(len(result.failed_rules), 1)

    def test_empty_report_passes_on_null_metrics(self):
        """All metrics null → all auto-pass. Only check rule runs, and it passes."""
        report = _make_minimal_report()
        report["summary"] = {}
        report["checks"] = {}
        result = should_promote(report)
        self.assertTrue(result.passed)  # all null = auto-pass, checks=0/0 passed


class TestDiffReports(unittest.TestCase):
    def test_identical_reports(self):
        r1 = _make_minimal_report()
        r2 = _make_minimal_report()
        diff = diff_reports(r1, r2)
        for d in diff["summary"]:
            self.assertEqual(d["delta"], "—", f"{d['metric']} should be identical")

    def test_different_pnl(self):
        r1 = _make_minimal_report()
        r2 = _make_minimal_report()
        r2["summary"]["total_pnl"] = 200.0
        diff = diff_reports(r1, r2)
        pnl_diff = next(d for d in diff["summary"] if d["metric"] == "total_pnl")
        self.assertAlmostEqual(pnl_diff["v1"], 100.0)
        self.assertAlmostEqual(pnl_diff["v2"], 200.0)

    def test_different_regimes(self):
        r1 = _make_minimal_report()
        r2 = _make_minimal_report()
        r2["regimes"]["performance"]["bull"]["total_pnl"] = 200.0
        diff = diff_reports(r1, r2)
        bull_diff = next(d for d in diff["regimes"]["bull"] if d["field"] == "total_pnl")
        self.assertEqual(bull_diff["v1"], 80.0)
        self.assertEqual(bull_diff["v2"], 200.0)

    def test_format_diff_produces_text(self):
        r1 = _make_minimal_report()
        r2 = _make_minimal_report()
        r2["summary"]["total_pnl"] = 200.0
        diff = diff_reports(r1, r2)
        text = format_diff(diff)
        self.assertIn("REPORT DIFF", text)
        self.assertIn("total_pnl", text)

    def test_cli_json_output(self):
        r1 = _make_minimal_report()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(r1, f)
            path1 = f.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(r1, f)
            path2 = f.name
        try:
            ret = diff_main([path1, path2, "--json"])
            self.assertEqual(ret, 0)
        finally:
            os.unlink(path1)
            os.unlink(path2)

    def test_cli_missing_file(self):
        ret = diff_main(["/nonexistent/r1.json", "/nonexistent/r2.json"])
        self.assertEqual(ret, 1)

    def test_cli_missing_second_file(self):
        r1 = _make_minimal_report()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(r1, f)
            path1 = f.name
        try:
            ret = diff_main([path1, "/nonexistent/r2.json"])
            self.assertEqual(ret, 1)
        finally:
            os.unlink(path1)


class TestGateRule(unittest.TestCase):
    def test_gate_rule_dataclass(self):
        rule = GateRule(metric="sharpe", operator="gte", threshold=0.5, passed=True, actual=1.0)
        self.assertEqual(rule.metric, "sharpe")
        self.assertTrue(rule.passed)

    def test_gate_result_properties(self):
        result = GateResult(
            passed=False,
            rules=[
                GateRule(metric="a", operator="gte", threshold=1.0, passed=True, actual=2.0),
                GateRule(metric="b", operator="gte", threshold=1.0, passed=False, actual=0.5),
            ],
        )
        self.assertFalse(result.passed)
        self.assertEqual(len(result.passed_rules), 1)
        self.assertEqual(len(result.failed_rules), 1)


if __name__ == "__main__":
    unittest.main()
