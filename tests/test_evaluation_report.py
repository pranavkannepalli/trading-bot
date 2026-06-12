"""Tests for evaluation report generator."""

from __future__ import annotations

import json
import os
import sys
import unittest

# Ensure repo root is on path for imports.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_bot.evaluation_report import (
    _build_equity_curve,
    _build_returns,
    _build_drawdown,
    _build_greeks_exposure,
    _build_boundary_enforcement,
    _build_regimes,
    _build_counsel_audit,
    _run_checks,
    _compute_trade_metrics,
    _compute_risk_adjusted_metrics,
    build_evaluation_report,
)


def _make_step(
    time_index: int,
    regime: str = "bull",
    premium: float = 5.0,
    iv: float = 0.25,
    delta: float = 0.5,
    gamma: float = 0.05,
    vega: float = 15.0,
    theta: float = -5.0,
    fills: list | None = None,
    proposed: list | None = None,
    decisions: list | None = None,
    advice: dict | None = None,
) -> dict:
    return {
        "time_index": time_index,
        "underlying_close": 100.0,
        "regime": regime,
        "implied_vol": iv,
        "remaining_T": 0.2,
        "option": {
            "option_type": "call" if regime in ("bull", "range") else "put",
            "strike": 100.0,
            "premium": premium,
            "greeks": {
                "delta": delta,
                "gamma": gamma,
                "vega": vega,
                "theta": theta,
            },
        },
        "proposed_orders": proposed or [],
        "advice": advice,
        "orders_to_execute": [f for f in (fills or [])],  # simplified
        "decisions": decisions or [],
        "fills": fills or [],
    }


class TestEquityCurve(unittest.TestCase):
    def test_empty_steps(self):
        curve, stats = _build_equity_curve([])
        self.assertEqual(curve, [])
        self.assertEqual(stats.peak_equity, 0.0)
        self.assertEqual(stats.final_equity, 0.0)

    def test_single_buy_fill(self):
        steps = [
            _make_step(0, fills=[
                {"symbol": "X", "side": "buy", "quantity": 10.0, "price": 1.5, "timestamp": 0},
            ], premium=1.5),
        ]
        curve, stats = _build_equity_curve(steps)
        self.assertEqual(len(curve), 1)
        self.assertAlmostEqual(curve[0]["cash"], -15.0)
        self.assertAlmostEqual(curve[0]["position"], 10.0)
        self.assertAlmostEqual(curve[0]["mtm"], 15.0)
        self.assertAlmostEqual(curve[0]["equity"], 0.0)

    def test_buy_then_mtm_gain(self):
        steps = [
            _make_step(0, fills=[
                {"symbol": "X", "side": "buy", "quantity": 10.0, "price": 1.5, "timestamp": 0},
            ], premium=1.5),
            _make_step(1, premium=3.0),
        ]
        curve, stats = _build_equity_curve(steps)
        self.assertEqual(len(curve), 2)
        self.assertAlmostEqual(curve[1]["cash"], -15.0)
        self.assertAlmostEqual(curve[1]["position"], 10.0)
        self.assertAlmostEqual(curve[1]["mtm"], 30.0)
        self.assertAlmostEqual(curve[1]["equity"], 15.0)

    def test_sell_reduces_position(self):
        steps = [
            _make_step(0, fills=[
                {"symbol": "X", "side": "buy", "quantity": 10.0, "price": 1.5, "timestamp": 0},
            ], premium=1.5),
            _make_step(1, fills=[
                {"symbol": "X", "side": "sell", "quantity": 5.0, "price": 2.0, "timestamp": 1},
            ], premium=2.0),
        ]
        curve, stats = _build_equity_curve(steps)
        self.assertAlmostEqual(curve[1]["cash"], -15.0 + 10.0)  # -15 buy + 10 sell
        self.assertAlmostEqual(curve[1]["position"], 5.0)
        self.assertAlmostEqual(curve[1]["mtm"], 10.0)
        self.assertAlmostEqual(curve[1]["equity"], -5.0 + 10.0)


class TestReturns(unittest.TestCase):
    def test_single_step(self):
        curve = [{"step": 0, "equity": 5.0, "cash": 0.0, "position": 0.0, "mtm": 0.0}]
        r = _build_returns(curve)
        self.assertEqual(r["step_returns"], [0.0])
        self.assertEqual(r["cumulative_return"], [1.0])

    def test_two_steps_gain(self):
        curve = [
            {"step": 0, "equity": 10.0, "cash": 0.0, "position": 0.0, "mtm": 0.0},
            {"step": 1, "equity": 15.0, "cash": 0.0, "position": 0.0, "mtm": 0.0},
        ]
        r = _build_returns(curve)
        self.assertAlmostEqual(r["step_returns"][1], 5.0)
        self.assertAlmostEqual(r["step_returns_pct"][1], 0.5)
        # cum: 1.0 * (1 + 0.5) = 1.5
        self.assertAlmostEqual(r["cumulative_return"][1], 1.5)

    def test_zero_initial_equity(self):
        curve = [
            {"step": 0, "equity": 0.0, "cash": 0.0, "position": 0.0, "mtm": 0.0},
            {"step": 1, "equity": 5.0, "cash": 0.0, "position": 0.0, "mtm": 0.0},
        ]
        r = _build_returns(curve)
        # When prev equity is ~0, returns default to 0.0.
        self.assertAlmostEqual(r["step_returns_pct"][1], 0.0)


class TestDrawdown(unittest.TestCase):
    def test_no_drawdown(self):
        curve = [
            {"step": 0, "equity": 0.0, "cash": 0.0, "position": 0.0, "mtm": 0.0},
            {"step": 1, "equity": 5.0, "cash": 0.0, "position": 0.0, "mtm": 0.0},
            {"step": 2, "equity": 10.0, "cash": 0.0, "position": 0.0, "mtm": 0.0},
        ]
        dd = _build_drawdown(curve)
        self.assertEqual(dd["max_drawdown_pct"], 0.0)
        self.assertEqual(dd["max_drawdown_absolute"], 0.0)
        self.assertEqual(dd["recovery_step"], None)

    def test_drawdown_and_recovery(self):
        curve = [
            {"step": 0, "equity": 0.0, "cash": 0.0, "position": 0.0, "mtm": 0.0},
            {"step": 1, "equity": 10.0, "cash": 0.0, "position": 0.0, "mtm": 0.0},
            {"step": 2, "equity": 5.0, "cash": 0.0, "position": 0.0, "mtm": 0.0},   # -50% from peak 10
            {"step": 3, "equity": 12.0, "cash": 0.0, "position": 0.0, "mtm": 0.0},  # new peak → recovered
        ]
        dd = _build_drawdown(curve)
        self.assertAlmostEqual(dd["max_drawdown_pct"], -0.5)
        self.assertAlmostEqual(dd["max_drawdown_absolute"], -5.0)
        self.assertEqual(dd["peak_step"], 1)
        self.assertEqual(dd["trough_step"], 2)
        self.assertEqual(dd["recovery_step"], 3)


class TestGreeksExposure(unittest.TestCase):
    def test_aggregates_across_fills(self):
        steps = [
            _make_step(0, regime="bull", iv=0.25, delta=0.5, vega=15.0,
                       fills=[
                           {"symbol": "X", "side": "buy", "quantity": 10.0, "price": 1.5, "timestamp": 0},
                       ]),
            _make_step(1, regime="bull", iv=0.25, delta=0.5, vega=15.0,
                       fills=[
                           {"symbol": "X", "side": "buy", "quantity": 5.0, "price": 1.5, "timestamp": 1},
                       ]),
        ]
        result = _build_greeks_exposure(steps)
        self.assertEqual(len(result["timeseries"]), 2)
        # After step 0: position=10, delta=0.5*10=5.0, vega=15*10=150
        self.assertAlmostEqual(result["timeseries"][0]["total_delta"], 5.0)
        self.assertAlmostEqual(result["timeseries"][0]["total_vega"], 150.0)
        # After step 1: position=15, delta=0.5*15=7.5, vega=15*15=225
        self.assertAlmostEqual(result["timeseries"][1]["total_delta"], 7.5)
        self.assertAlmostEqual(result["extremes"]["max_abs_delta"], 7.5)
        self.assertEqual(result["by_regime"]["bull"]["step_count"], 2)


class TestBoundaryEnforcement(unittest.TestCase):
    def test_no_blocked(self):
        steps = [
            _make_step(0, decisions=[
                {"symbol": "X", "side": "buy", "quantity": 10.0,
                 "risk": {"delta_total_abs": 1.0, "vega_total_abs": 50.0},
                 "allowed": True,
                 "caps": {"max_abs_total_delta": 2.0, "max_abs_total_vega": 1e9}},
            ]),
        ]
        result = _build_boundary_enforcement(steps, False, 1000.0, 1e9)
        self.assertEqual(result["total_orders_proposed"], 1)
        self.assertEqual(result["total_orders_blocked"], 0)
        self.assertEqual(result["blocked_at_steps"], [])

    def test_blocked_by_delta(self):
        steps = [
            _make_step(0, decisions=[
                {"symbol": "X", "side": "buy", "quantity": 10.0,
                 "risk": {"delta_total_abs": 5.0, "vega_total_abs": 50.0},
                 "allowed": False,
                 "caps": {"max_abs_total_delta": 2.0, "max_abs_total_vega": 1e9}},
            ]),
        ]
        result = _build_boundary_enforcement(steps, True, 1000.0, 1e9)
        self.assertEqual(result["total_orders_blocked"], 1)
        self.assertEqual(result["blocked_by"]["delta"], 1)
        self.assertEqual(result["blocked_by"]["vega"], 0)
        self.assertEqual(result["blocked_at_steps"], [0])
        self.assertEqual(result["worst_blocked_risk"]["delta_abs"], 5.0)


class TestRegimes(unittest.TestCase):
    def test_distribution_and_transitions(self):
        steps = [
            _make_step(0, regime="bull"),
            _make_step(1, regime="bull"),
            _make_step(2, regime="bear"),
            _make_step(3, regime="bear"),
            _make_step(4, regime="bull"),
        ]
        result = _build_regimes(steps)
        self.assertEqual(result["distribution"]["bull"]["count"], 3)
        self.assertEqual(result["distribution"]["bear"]["count"], 2)
        self.assertAlmostEqual(result["distribution"]["bull"]["pct"], 0.6)
        self.assertEqual(result["transitions"]["bull->bear"], 1)
        self.assertEqual(result["transitions"]["bear->bull"], 1)

    def test_regime_pnl(self):
        steps = [
            _make_step(0, regime="bull",
                       fills=[{"symbol": "X", "side": "buy", "quantity": 5.0, "price": 2.0, "timestamp": 0}]),
            _make_step(1, regime="bear",
                       fills=[{"symbol": "X", "side": "buy", "quantity": 3.0, "price": 4.0, "timestamp": 1}]),
        ]
        result = _build_regimes(steps)
        self.assertAlmostEqual(result["performance"]["bull"]["total_pnl"], -10.0)
        self.assertAlmostEqual(result["performance"]["bear"]["total_pnl"], -12.0)


class TestCounselAudit(unittest.TestCase):
    def test_null_when_no_advice(self):
        steps = [_make_step(0), _make_step(1)]
        result = _build_counsel_audit(steps, "Test", {"max_abs_total_delta": 2.0})
        self.assertIsNone(result)

    def test_records_advice(self):
        steps = [
            _make_step(0, advice={"max_abs_total_delta": 2.0, "max_abs_total_vega": None, "max_order_quantity": None},
                       proposed=[{"symbol": "X"}],
                       decisions=[
                           {"symbol": "X", "side": "buy", "quantity": 10.0,
                            "risk": {"delta_total_abs": 1.0, "vega_total_abs": 50.0},
                            "allowed": True,
                            "caps": {"max_abs_total_delta": 2.0, "max_abs_total_vega": 1e9}},
                       ]),
        ]
        result = _build_counsel_audit(steps, "StrictDelta", {"max_abs_total_delta": 2.0})
        self.assertIsNotNone(result)
        self.assertEqual(result["active"], True)
        self.assertEqual(result["total_calls"], 1)
        self.assertEqual(len(result["advice_summary"]), 1)
        self.assertFalse(result["advice_summary"][0]["delta_cap_triggered"])

    def test_captures_blocks(self):
        steps = [
            _make_step(0, advice={"max_abs_total_delta": 2.0, "max_abs_total_vega": None, "max_order_quantity": None},
                       proposed=[{"symbol": "X"}, {"symbol": "Y"}],
                       decisions=[
                           {"symbol": "X", "side": "buy", "quantity": 10.0,
                            "risk": {"delta_total_abs": 5.0, "vega_total_abs": 50.0},
                            "allowed": False,
                            "caps": {"max_abs_total_delta": 2.0, "max_abs_total_vega": 1e9}},
                           {"symbol": "Y", "side": "buy", "quantity": 5.0,
                            "risk": {"delta_total_abs": 1.0, "vega_total_abs": 25.0},
                            "allowed": True,
                            "caps": {"max_abs_total_delta": 2.0, "max_abs_total_vega": 1e9}},
                       ]),
        ]
        result = _build_counsel_audit(steps, "StrictDelta", {"max_abs_total_delta": 2.0})
        self.assertEqual(result["caps_applied"]["delta_cap_hit_count"], 1)
        self.assertTrue(result["advice_summary"][0]["delta_cap_triggered"])
        self.assertAlmostEqual(result["advice_summary"][0]["blocked_delta_abs"], 5.0)


class TestChecks(unittest.TestCase):
    def test_all_pass_empty(self):
        steps = [_make_step(0)]
        result = _run_checks(steps, [], False, [])
        self.assertEqual(result["summary"]["passed"], result["summary"]["total"])

    def test_boundary_enforced_fails(self):
        steps = [
            _make_step(0, decisions=[
                {"symbol": "X", "side": "buy", "quantity": 10.0,
                 "risk": {"delta_total_abs": 10.0, "vega_total_abs": 50.0},
                 "allowed": True,
                 "caps": {"max_abs_total_delta": 2.0, "max_abs_total_vega": 1e9}},
            ]),
        ]
        result = _run_checks(steps, [], False, [])
        boundary = next(r for r in result["results"] if r["id"] == "boundary_enforced")
        self.assertFalse(boundary["passed"])

    def test_counsel_check_skipped_when_inactive(self):
        result = _run_checks([_make_step(0)], [], False, [])
        cc = next(r for r in result["results"] if r["id"] == "counsel_caps_not_exceeded")
        self.assertTrue(cc["passed"])
        self.assertIn("skipped", cc["detail"])

    def test_negative_position_fails(self):
        steps = [
            _make_step(0, fills=[
                {"symbol": "X", "side": "sell", "quantity": 10.0, "price": 1.5, "timestamp": 0},
            ]),
        ]
        result = _run_checks(steps, [], False, [])
        np_check = next(r for r in result["results"] if r["id"] == "no_negative_position")
        self.assertFalse(np_check["passed"])

    def test_invalid_regime_fails(self):
        steps = [_make_step(0, regime="invalid")]
        result = _run_checks(steps, [], False, [])
        regime_check = next(r for r in result["results"] if r["id"] == "regime_labels_valid")
        self.assertFalse(regime_check["passed"])


class TestTradeMetrics(unittest.TestCase):
    def test_empty(self):
        m = _compute_trade_metrics([], [])
        self.assertIsNone(m["profit_factor"])
        self.assertIsNone(m["win_rate"])

    def test_all_losses(self):
        fills = [[
            {"symbol": "X", "side": "buy", "quantity": 10.0, "price": 1.5, "timestamp": 0},
            {"symbol": "X", "side": "buy", "quantity": 5.0, "price": 2.0, "timestamp": 1},
        ]]
        m = _compute_trade_metrics(fills, [])
        self.assertEqual(m["win_rate"], 0.0)
        self.assertIsNone(m["avg_win"])
        self.assertAlmostEqual(m["avg_loss"], (-15.0 + -10.0) / 2)
        # profit_factor: no wins → gross_profits = 0, profit_factor = 0 / gross_losses = 0
        self.assertAlmostEqual(m["profit_factor"], 0.0)
        self.assertEqual(m["max_consecutive_losses"], 2)

    def test_mixed(self):
        fills = [[
            {"symbol": "X", "side": "buy", "quantity": 10.0, "price": 1.0, "timestamp": 0},   # -10 (loss)
            {"symbol": "X", "side": "sell", "quantity": 5.0, "price": 5.0, "timestamp": 1},    # +25 (win)
            {"symbol": "X", "side": "buy", "quantity": 2.0, "price": 3.0, "timestamp": 2},     # -6 (loss)
        ]]
        m = _compute_trade_metrics(fills, [])
        self.assertAlmostEqual(m["win_rate"], 1.0 / 3.0, places=6)
        self.assertAlmostEqual(m["avg_win"], 25.0)
        self.assertAlmostEqual(m["avg_loss"], (-10.0 + -6.0) / 2)
        self.assertAlmostEqual(m["profit_factor"], 25.0 / 16.0)
        self.assertEqual(m["max_consecutive_losses"], 1)


class TestRiskAdjustedMetrics(unittest.TestCase):
    def test_insufficient_data(self):
        curve = [{"step": 0, "equity": 10.0, "cash": 0.0, "position": 0.0, "mtm": 0.0}]
        m = _compute_risk_adjusted_metrics(curve, [_make_step(0)], 0.01, {"max_drawdown_pct": 0.0})
        self.assertIsNone(m["cagr"])
        self.assertIsNone(m["sharpe_ratio"])

    def test_cagr_with_gain(self):
        # 252 steps = 1 year, equity from 100 to 121 → CAGR = 0.21
        curve = []
        for i in range(253):  # 0..252 = 252 returns
            eq = 100.0 * (1.21 ** (i / 252.0))
            curve.append({"step": i, "equity": eq, "cash": 0.0, "position": 0.0, "mtm": 0.0})
        steps = [_make_step(i) for i in range(252)]
        m = _compute_risk_adjusted_metrics(curve, steps, 0.01, {"max_drawdown_pct": 0.0})
        self.assertIsNotNone(m["cagr"])
        self.assertAlmostEqual(m["cagr"], 0.21, places=3)
        # Calmar is None because no drawdown.
        self.assertIsNone(m["calmar_ratio"])

    def test_calmar_computed(self):
        curve = [
            {"step": 0, "equity": 100.0, "cash": 0.0, "position": 0.0, "mtm": 0.0},
            {"step": 1, "equity": 80.0, "cash": 0.0, "position": 0.0, "mtm": 0.0},
            {"step": 2, "equity": 121.0, "cash": 0.0, "position": 0.0, "mtm": 0.0},
        ]
        # Not enough returns for CAGR (need 1 year), so Calmar stays null.
        m = _compute_risk_adjusted_metrics(curve, [_make_step(i) for i in range(3)], 0.01,
                                           {"max_drawdown_pct": -0.20})
        self.assertIsNone(m["cagr"])  # < 1 year


class TestFullReport(unittest.TestCase):
    def test_default_case_report(self):
        """Generate a report with no counsel and verify structure."""
        steps = [
            _make_step(0, regime="bull",
                       proposed=[{"symbol": "X__call__K100.0__T0.25", "side": "buy", "quantity": 5.0}],
                       fills=[
                {"symbol": "X__call__K100.0__T0.25", "side": "buy", "quantity": 5.0, "price": 2.0, "timestamp": 0},
            ]),
            _make_step(1, regime="bear",
                       proposed=[{"symbol": "X__put__K100.0__T0.25", "side": "buy", "quantity": 5.0}],
                       fills=[
                {"symbol": "X__put__K100.0__T0.25", "side": "buy", "quantity": 5.0, "price": 1.5, "timestamp": 1},
            ]),
        ]
        artifacts = {
            "meta": {
                "window_size": 5, "lag_steps": 2, "strike": 100.0,
                "expiry_years": 0.25, "risk_free_rate": 0.01, "base_implied_vol": 0.25,
                "iv_by_regime": {"bull": 0.20, "bear": 0.30, "range": 0.25, "volatile": 0.40},
                "strategy": "regime_aware_option_strategy",
            },
            "steps": steps,
            "final": {
                "fills_count": 2,
                "cash": -17.5,
                "position": 10.0,
                "final_option_premium": 2.0,
                "total_pnl": 2.5,
            },
        }
        closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0, 110.0, 111.0]

        report = build_evaluation_report(
            artifacts=artifacts,
            strategy_name="regime_aware_option_strategy",
            strategy_class="RegimeAwareOptionStrategy",
            strategy_config={"underlying": "MOCK", "strike": 100.0, "expiry_years": 0.25, "quantity_per_step": 5.0},
            run_config={"core_max_abs_total_delta": 1000.0, "core_max_abs_total_vega": 1e9},
            closes=closes,
        )

        self.assertEqual(report["$schema"], "trading-bot-eval-report-v1")
        self.assertIn("meta", report)
        self.assertIn("summary", report)
        self.assertIn("performance", report)
        self.assertIn("risk", report)
        self.assertIn("regimes", report)
        self.assertIn("checks", report)
        self.assertIsNone(report["counsel"])
        self.assertEqual(report["summary"]["n_steps"], 2)
        self.assertEqual(report["summary"]["n_fills"], 2)
        self.assertEqual(report["checks"]["summary"]["passed"], report["checks"]["summary"]["total"])

    def test_counsel_case_report(self):
        """Generate a report with counsel active."""
        steps = [
            _make_step(0, regime="bull",
                       advice={"max_abs_total_delta": 2.0, "max_abs_total_vega": None, "max_order_quantity": None},
                       proposed=[{"symbol": "X"}],
                       decisions=[
                           {"symbol": "X", "side": "buy", "quantity": 10.0,
                            "risk": {"delta_total_abs": 1.0, "vega_total_abs": 50.0},
                            "allowed": True,
                            "caps": {"max_abs_total_delta": 2.0, "max_abs_total_vega": 1e9}},
                       ],
                       fills=[
                           {"symbol": "X__call__K100.0__T0.25", "side": "buy", "quantity": 10.0, "price": 2.0, "timestamp": 0},
                       ]),
        ]
        artifacts = {
            "meta": {"window_size": 5, "lag_steps": 2, "strike": 100.0, "expiry_years": 0.25,
                     "risk_free_rate": 0.01, "base_implied_vol": 0.25,
                     "iv_by_regime": {}, "strategy": "test"},
            "steps": steps,
            "final": {"fills_count": 1, "cash": -20.0, "position": 10.0, "final_option_premium": 2.0, "total_pnl": 0.0},
        }
        closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0, 110.0, 111.0]

        report = build_evaluation_report(
            artifacts=artifacts,
            strategy_name="test",
            strategy_class="Test",
            strategy_config={},
            counsel_type="StrictDeltaCounsel",
            counsel_config={"max_abs_total_delta": 2.0},
            run_config={"core_max_abs_total_delta": 1000.0, "core_max_abs_total_vega": 1e9},
            closes=closes,
        )

        self.assertIsNotNone(report["counsel"])
        self.assertTrue(report["counsel"]["active"])
        self.assertEqual(report["counsel"]["total_calls"], 1)
        self.assertTrue(report["summary"]["counsel_active"])
        self.assertEqual(report["checks"]["summary"]["passed"], report["checks"]["summary"]["total"])


if __name__ == "__main__":
    unittest.main()
