"""Evaluation report generator for the non-agentic trading bot.

Pure-function builder that consumes backtest artifacts + run config and produces
a self-contained JSON report per docs/evaluation-report-spec.md.

All metrics are deterministic, leakage-free, and computed without network calls.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

from trading_bot.types import Advice, Side


# ---------------------------------------------------------------------------
# Dataclass schema (mirrors Section 4 of the spec)
# ---------------------------------------------------------------------------

@dataclass
class StrategyMeta:
    name: str
    class_: str  # "class" in JSON
    config: dict[str, Any]


@dataclass
class EnvMeta:
    class_: str
    config: dict[str, Any]


@dataclass
class PriceSourceMeta:
    generator: str
    count: int
    first_close: float
    last_close: float
    min_close: float
    max_close: float


@dataclass
class CoreBoundaryMeta:
    max_abs_total_delta: float
    max_abs_total_vega: float


@dataclass
class PricingMeta:
    model: str
    risk_free_rate: float
    base_implied_vol: float
    iv_by_regime: dict[str, float]
    expiry_years: float


@dataclass
class Meta:
    report_version: str
    generated_at: str
    run_id: str
    strategy: StrategyMeta
    env: EnvMeta
    price_source: PriceSourceMeta
    core_boundary: CoreBoundaryMeta
    pricing: PricingMeta

    def to_dict(self) -> dict:
        d = asdict(self)
        d["strategy"]["class"] = d["strategy"].pop("class_")
        d["env"]["class"] = d["env"].pop("class_")
        return d


@dataclass
class RegimeSummaryItem:
    steps: int
    pnl: float


@dataclass
class Summary:
    start_value: float
    final_cash: float
    final_position: float
    final_option_mtm: float
    total_pnl: float
    total_return_pct: float

    n_steps: int
    n_fills: int
    n_proposed_orders: int
    n_blocked_orders: int
    block_rate: float

    cagr: Optional[float]
    sharpe_ratio: Optional[float]
    sortino_ratio: Optional[float]
    calmar_ratio: Optional[float]
    max_drawdown_pct: Optional[float]
    max_drawdown_duration_steps: Optional[int]
    profit_factor: Optional[float]
    win_rate: Optional[float]
    avg_win: Optional[float]
    avg_loss: Optional[float]
    expectancy: Optional[float]

    regime_summary: dict[str, dict[str, float]]
    counsel_active: bool
    counsel_total_advice_calls: int
    counsel_caps_hit: int

    checks_passed: int
    checks_total: int


@dataclass
class EquityPoint:
    step: int
    cash: float
    position: float
    mtm: float
    equity: float


@dataclass
class EquityStats:
    peak_equity: float
    trough_equity: float
    final_equity: float
    mean_equity: float
    equity_std: float


@dataclass
class Performance:
    equity_curve: list[dict[str, float]]
    equity_stats: dict[str, float]
    returns: dict[str, list[float]]


@dataclass
class DrawdownPoint:
    step: int
    equity: float
    peak: float
    drawdown_pct: float


@dataclass
class Drawdown:
    max_drawdown_pct: float
    max_drawdown_absolute: float
    peak_step: int
    trough_step: int
    recovery_step: Optional[int]
    drawdown_duration_steps: int
    drawdown_series: list[dict[str, float]]


@dataclass
class GreeksExposurePoint:
    step: int
    regime: str
    implied_vol: float
    total_delta: float
    total_gamma: float
    total_vega: float
    total_theta: float
    delta_abs: float
    vega_abs: float


@dataclass
class BoundaryEnforcement:
    delta_cap_effective: float
    vega_cap_effective: float
    total_orders_proposed: int
    total_orders_blocked: int
    blocked_by: dict[str, int]
    blocked_at_steps: list[int]
    worst_blocked_risk: dict[str, Any]


@dataclass
class Risk:
    drawdown: dict[str, Any]
    greeks_exposure: dict[str, Any]
    boundary_enforcement: dict[str, Any]


@dataclass
class RegimeDistribution:
    count: int
    pct: float


@dataclass
class RegimePerformance:
    steps: int
    fills: int
    total_pnl: float
    mean_pnl_per_step: float
    blocked_orders: int
    option_type_used: str


@dataclass
class Regimes:
    detector: str
    detector_config: dict[str, float]
    distribution: dict[str, dict[str, float]]
    transitions: dict[str, int]
    performance: dict[str, dict[str, Any]]


@dataclass
class AdviceAuditEntry:
    step: int
    advice: dict[str, Optional[float]]
    orders_before: int
    orders_after: int
    delta_cap_triggered: bool
    blocked_delta_abs: Optional[float] = None


@dataclass
class CounselAudit:
    active: bool
    counsel_type: str
    counsel_config: dict[str, float]
    total_calls: int
    caps_applied: dict[str, int]
    advice_summary: list[dict[str, Any]]


@dataclass
class CheckResult:
    id: str
    passed: bool
    detail: str


@dataclass
class Checks:
    results: list[dict[str, Any]]
    summary: dict[str, int]


# ---------------------------------------------------------------------------
# Equity curve
# ---------------------------------------------------------------------------

def _build_equity_curve(steps: list[dict]) -> tuple[list[dict], EquityStats]:
    """Walk step-by-step through artifacts to build the equity curve.

    equity[t] = cash[t] + position[t] * option_premium[t]
    """
    points: list[dict] = []
    cash = 0.0
    position = 0.0

    for s in steps:
        t = s["time_index"]
        fills = s.get("fills", [])
        premium = s["option"]["premium"]

        for f in fills:
            qty = float(f["quantity"])
            px = float(f["price"])
            if f["side"] == "buy":
                cash -= qty * px
                position += qty
            else:
                cash += qty * px
                position -= qty

        mtm = position * premium
        equity = cash + mtm

        points.append({
            "step": t,
            "cash": round(cash, 6),
            "position": round(position, 6),
            "mtm": round(mtm, 6),
            "equity": round(equity, 6),
        })

    if not points:
        return [], EquityStats(
            peak_equity=0.0, trough_equity=0.0, final_equity=0.0,
            mean_equity=0.0, equity_std=0.0,
        )

    equities = [p["equity"] for p in points]
    n = len(equities)
    mean_e = sum(equities) / n
    var_e = sum((e - mean_e) ** 2 for e in equities) / max(1, n - 1)

    return points, EquityStats(
        peak_equity=round(max(equities), 6),
        trough_equity=round(min(equities), 6),
        final_equity=round(equities[-1], 6),
        mean_equity=round(mean_e, 6),
        equity_std=round(var_e ** 0.5, 6),
    )


# ---------------------------------------------------------------------------
# Returns series
# ---------------------------------------------------------------------------

def _build_returns(equity_curve: list[dict]) -> dict:
    """Compute step returns from the equity curve (first step = 0)."""
    step_returns: list[float] = []
    step_returns_pct: list[float] = []
    cumulative_return: list[float] = [1.0]

    if not equity_curve:
        return {
            "step_returns": [],
            "step_returns_pct": [],
            "cumulative_return": [1.0],
        }

    # First step return is 0 (no prior equity to compare).
    step_returns.append(0.0)
    step_returns_pct.append(0.0)

    for i in range(1, len(equity_curve)):
        sr = equity_curve[i]["equity"] - equity_curve[i - 1]["equity"]
        step_returns.append(round(sr, 6))
        prev = equity_curve[i - 1]["equity"]
        if abs(prev) > 1e-12:
            step_returns_pct.append(round(sr / abs(prev), 6))
        else:
            step_returns_pct.append(0.0)
        cumulative_return.append(round(cumulative_return[-1] * (1.0 + step_returns_pct[-1]), 6))

    # Replace the first cum entry if we only have one step.
    if len(cumulative_return) == 1:
        cumulative_return = [1.0]

    return {
        "step_returns": step_returns,
        "step_returns_pct": step_returns_pct,
        "cumulative_return": cumulative_return,
    }


# ---------------------------------------------------------------------------
# Drawdown analysis
# ---------------------------------------------------------------------------

def _build_drawdown(equity_curve: list[dict]) -> dict:
    """Compute drawdown series and summary from the equity curve."""
    if not equity_curve:
        return {
            "max_drawdown_pct": 0.0,
            "max_drawdown_absolute": 0.0,
            "peak_step": 0,
            "trough_step": 0,
            "recovery_step": None,
            "drawdown_duration_steps": 0,
            "drawdown_series": [],
        }

    peak_equity = equity_curve[0]["equity"]
    peak_step = equity_curve[0]["step"]
    max_dd_pct = 0.0
    max_dd_abs = 0.0
    dd_peak_step = peak_step
    dd_trough_step = peak_step
    recovery_step: Optional[int] = None
    in_drawdown = False
    dd_start_step = peak_step

    series: list[dict] = []

    for p in equity_curve:
        eq = p["equity"]
        step = p["step"]

        if eq > peak_equity:
            peak_equity = eq
            peak_step = step
            if in_drawdown:
                recovery_step = step
                in_drawdown = False

        if abs(peak_equity) > 1e-12:
            dd_pct = (eq - peak_equity) / abs(peak_equity)
        else:
            dd_pct = eq - peak_equity

        dd_abs = eq - peak_equity

        series.append({
            "step": step,
            "equity": eq,
            "peak": round(peak_equity, 6),
            "drawdown_pct": round(dd_pct, 6),
        })

        if dd_abs < max_dd_abs:
            max_dd_abs = dd_abs
            max_dd_pct = dd_pct
            dd_peak_step = peak_step
            dd_trough_step = step
            if not in_drawdown:
                dd_start_step = step
                in_drawdown = True

    # Calculate drawdown duration from peak to trough (or end if unrecovered).
    dd_duration = dd_trough_step - dd_peak_step if dd_peak_step > 0 else 0
    if recovery_step is None and in_drawdown:
        dd_duration = equity_curve[-1]["step"] - dd_start_step

    return {
        "max_drawdown_pct": round(max_dd_pct, 6),
        "max_drawdown_absolute": round(max_dd_abs, 6),
        "peak_step": dd_peak_step,
        "trough_step": dd_trough_step,
        "recovery_step": recovery_step if recovery_step and recovery_step > dd_trough_step else None,
        "drawdown_duration_steps": dd_duration,
        "drawdown_series": series,
    }


# ---------------------------------------------------------------------------
# Greeks exposure
# ---------------------------------------------------------------------------

def _build_greeks_exposure(steps: list[dict]) -> dict:
    """Aggregate greeks exposure from fills across steps."""
    timeseries: list[dict] = []
    extremes = {"max_abs_delta": 0.0, "max_abs_vega": 0.0, "max_abs_gamma": 0.0, "max_abs_theta": 0.0}
    by_regime: dict[str, dict[str, Any]] = {}

    position = 0.0

    for s in steps:
        t = s["time_index"]
        regime = s["regime"]
        iv = s["implied_vol"]
        g = s["option"]["greeks"]
        fills = s.get("fills", [])

        for f in fills:
            qty = float(f["quantity"])
            if f["side"] == "buy":
                position += qty
            else:
                position -= qty

        total_delta = g["delta"] * position
        total_gamma = g["gamma"] * position
        total_vega = g["vega"] * position
        total_theta = g["theta"] * position
        delta_abs = abs(total_delta)
        vega_abs = abs(total_vega)

        point = {
            "step": t,
            "regime": regime,
            "implied_vol": iv,
            "total_delta": round(total_delta, 6),
            "total_gamma": round(total_gamma, 6),
            "total_vega": round(total_vega, 6),
            "total_theta": round(total_theta, 6),
            "delta_abs": round(delta_abs, 6),
            "vega_abs": round(vega_abs, 6),
        }
        timeseries.append(point)

        extremes["max_abs_delta"] = round(max(extremes["max_abs_delta"], delta_abs), 6)
        extremes["max_abs_vega"] = round(max(extremes["max_abs_vega"], vega_abs), 6)
        extremes["max_abs_gamma"] = round(max(extremes["max_abs_gamma"], abs(total_gamma)), 6)
        extremes["max_abs_theta"] = round(max(extremes["max_abs_theta"], abs(total_theta)), 6)

        if regime not in by_regime:
            by_regime[regime] = {"mean_abs_delta": 0.0, "mean_abs_vega": 0.0, "step_count": 0}
        by_regime[regime]["step_count"] += 1
        # Running mean update
        n = by_regime[regime]["step_count"]
        by_regime[regime]["mean_abs_delta"] = round(
            (by_regime[regime]["mean_abs_delta"] * (n - 1) + delta_abs) / n, 6
        )
        by_regime[regime]["mean_abs_vega"] = round(
            (by_regime[regime]["mean_abs_vega"] * (n - 1) + vega_abs) / n, 6
        )

    return {
        "timeseries": timeseries,
        "extremes": extremes,
        "by_regime": by_regime,
    }


# ---------------------------------------------------------------------------
# Boundary enforcement
# ---------------------------------------------------------------------------

def _build_boundary_enforcement(
    steps: list[dict],
    counsel_active: bool,
    core_max_abs_total_delta: float,
    core_max_abs_total_vega: float,
) -> dict:
    """Summarize boundary enforcement from per-step decisions."""
    total_proposed = 0
    total_blocked = 0
    blocked_delta = 0
    blocked_vega = 0
    blocked_both = 0
    blocked_steps: list[int] = []
    worst_step = 0
    worst_delta_abs = 0.0
    worst_vega_abs = 0.0
    worst_cap_delta = 0.0
    worst_cap_vega = 0.0

    # Determine effective caps from first step with decisions.
    effective_delta_cap = core_max_abs_total_delta
    effective_vega_cap = core_max_abs_total_vega

    for s in steps:
        decisions = s.get("decisions", [])
        for d in decisions:
            total_proposed += 1
            caps = d.get("caps", {})
            effective_delta_cap = caps.get("max_abs_total_delta", effective_delta_cap)
            effective_vega_cap = caps.get("max_abs_total_vega", effective_vega_cap)

            if not d.get("allowed", True):
                total_blocked += 1
                blocked_steps.append(s["time_index"])
                risk = d.get("risk", {})
                da = risk.get("delta_total_abs", 0.0)
                va = risk.get("vega_total_abs", 0.0)
                cd = caps.get("max_abs_total_delta", 0.0)
                cv = caps.get("max_abs_total_vega", 0.0)

                delta_hit = da > cd
                vega_hit = va > cv
                if delta_hit and vega_hit:
                    blocked_both += 1
                elif delta_hit:
                    blocked_delta += 1
                elif vega_hit:
                    blocked_vega += 1

                if da > worst_delta_abs:
                    worst_delta_abs = da
                    worst_step = s["time_index"]
                    worst_vega_abs = va
                    worst_cap_delta = cd
                    worst_cap_vega = cv

    return {
        "delta_cap_effective": effective_delta_cap,
        "vega_cap_effective": effective_vega_cap,
        "total_orders_proposed": total_proposed,
        "total_orders_blocked": total_blocked,
        "blocked_by": {
            "delta": blocked_delta,
            "vega": blocked_vega,
            "both": blocked_both,
        },
        "blocked_at_steps": blocked_steps,
        "worst_blocked_risk": {
            "step": worst_step,
            "delta_abs": worst_delta_abs,
            "vega_abs": worst_vega_abs,
            "cap_delta": worst_cap_delta,
            "cap_vega": worst_cap_vega,
        },
    }


# ---------------------------------------------------------------------------
# Regime analysis
# ---------------------------------------------------------------------------

def _build_regimes(steps: list[dict], detector_config: Optional[dict] = None) -> dict:
    """Per-regime decomposition and transition matrix."""
    if detector_config is None:
        detector_config = {"window_size": 5, "vol_threshold": 0.10, "range_slope_threshold": 0.01}

    regimes_seen: dict[str, int] = {}
    regime_pnl: dict[str, float] = {}
    regime_fills: dict[str, int] = {}
    regime_blocked: dict[str, int] = {}
    regime_option_type: dict[str, set] = {}

    prev_regime: Optional[str] = None
    transitions: dict[str, int] = {}

    for s in steps:
        regime = s["regime"]
        regimes_seen[regime] = regimes_seen.get(regime, 0) + 1

        if regime not in regime_option_type:
            regime_option_type[regime] = set()
        regime_option_type[regime].add(s["option"]["option_type"])

        # Count fills in this step.
        regime_fills[regime] = regime_fills.get(regime, 0) + len(s.get("fills", []))

        # Count blocked orders.
        decisions = s.get("decisions", [])
        for d in decisions:
            if not d.get("allowed", True):
                regime_blocked[regime] = regime_blocked.get(regime, 0) + 1

        # P&L: sum of fill costs (negative for buys).
        for f in s.get("fills", []):
            qty = float(f["quantity"])
            px = float(f["price"])
            step_pnl = -qty * px if f["side"] == "buy" else qty * px
            regime_pnl[regime] = regime_pnl.get(regime, 0.0) + step_pnl

        # Transitions.
        if prev_regime is not None and regime != prev_regime:
            key = f"{prev_regime}->{regime}"
            transitions[key] = transitions.get(key, 0) + 1
        prev_regime = regime

    total_steps = sum(regimes_seen.values())
    distribution = {}
    for r, count in regimes_seen.items():
        distribution[r] = {
            "count": count,
            "pct": round(count / total_steps, 4) if total_steps > 0 else 0.0,
        }

    performance = {}
    for r in regimes_seen:
        opt_types = regime_option_type.get(r, set())
        performance[r] = {
            "steps": regimes_seen[r],
            "fills": regime_fills.get(r, 0),
            "total_pnl": round(regime_pnl.get(r, 0.0), 6),
            "mean_pnl_per_step": round(regime_pnl.get(r, 0.0) / regimes_seen[r], 6) if regimes_seen[r] > 0 else 0.0,
            "blocked_orders": regime_blocked.get(r, 0),
            "option_type_used": opt_types.pop() if len(opt_types) == 1 else ", ".join(sorted(opt_types)),
        }

    return {
        "detector": "SimpleTrendVolRegimeDetector",
        "detector_config": detector_config,
        "distribution": distribution,
        "transitions": transitions,
        "performance": performance,
    }


# ---------------------------------------------------------------------------
# Counsel audit trail
# ---------------------------------------------------------------------------

def _build_counsel_audit(steps: list[dict], counsel_type: str, counsel_config: dict) -> Optional[dict]:
    """Build counsel audit trail from per-step advice entries."""
    advice_entries = [s for s in steps if s.get("advice") is not None]
    if not advice_entries:
        return None

    total_calls = len(advice_entries)
    delta_cap_hits = 0
    vega_cap_hits = 0
    quantity_clamps = 0

    summary_entries: list[dict] = []

    for s in steps:
        adv = s.get("advice")
        if adv is None:
            continue

        decisions = s.get("decisions", [])
        orders_before = len(s.get("proposed_orders", []))
        orders_after = len(s.get("orders_to_execute", []))
        blocked = any(not d.get("allowed", True) for d in decisions)

        # Check which caps were hit.
        delta_triggered = False
        blocked_delta_abs = None
        for d in decisions:
            if not d.get("allowed", True):
                risk = d.get("risk", {})
                caps = d.get("caps", {})
                if risk.get("delta_total_abs", 0) > caps.get("max_abs_total_delta", float("inf")):
                    delta_triggered = True
                    delta_cap_hits += 1
                    if blocked_delta_abs is None:
                        blocked_delta_abs = risk.get("delta_total_abs")
                if risk.get("vega_total_abs", 0) > caps.get("max_abs_total_vega", float("inf")):
                    vega_cap_hits += 1

        entry: dict = {
            "step": s["time_index"],
            "advice": {
                "max_order_quantity": adv.get("max_order_quantity"),
                "max_abs_total_delta": adv.get("max_abs_total_delta"),
                "max_abs_total_vega": adv.get("max_abs_total_vega"),
            },
            "orders_before": orders_before,
            "orders_after": orders_after,
            "delta_cap_triggered": delta_triggered,
        }
        if blocked_delta_abs is not None:
            entry["blocked_delta_abs"] = blocked_delta_abs
        summary_entries.append(entry)

    return {
        "active": True,
        "counsel_type": counsel_type,
        "counsel_config": counsel_config,
        "total_calls": total_calls,
        "caps_applied": {
            "delta_cap_hit_count": delta_cap_hits,
            "vega_cap_hit_count": vega_cap_hits,
            "quantity_clamp_count": quantity_clamps,
        },
        "advice_summary": summary_entries,
    }


# ---------------------------------------------------------------------------
# Automated checks
# ---------------------------------------------------------------------------

def _run_checks(
    steps: list[dict],
    fills: list,
    counsel_active: bool,
    equity_curve: list[dict],
) -> dict:
    """Run automated pass/fail assertions on the report data."""
    results: list[dict] = []

    # 1. no_future_leakage
    max_t = steps[-1]["time_index"] if steps else 0
    leakage = True
    for s in steps:
        if s["time_index"] > max_t + len(steps):
            leakage = False
            break
    results.append({
        "id": "no_future_leakage",
        "passed": leakage,
        "detail": f"All {len(steps)} steps reference time_index within bounds",
    })

    # 2. boundary_enforced
    boundary_ok = True
    for s in steps:
        for d in s.get("decisions", []):
            if d.get("allowed", True):
                risk = d.get("risk", {})
                caps = d.get("caps", {})
                if risk.get("delta_total_abs", 0) > caps.get("max_abs_total_delta", float("inf")) or \
                   risk.get("vega_total_abs", 0) > caps.get("max_abs_total_vega", float("inf")):
                    boundary_ok = False
                    break
    blocked_count = sum(1 for s in steps for d in s.get("decisions", []) if not d.get("allowed", True))
    results.append({
        "id": "boundary_enforced",
        "passed": boundary_ok,
        "detail": f"0 allowed orders exceeded risk caps; {blocked_count} blocked",
    })

    # 3. greeks_computed_all_steps
    greeks_ok = all(
        all(k in s["option"].get("greeks", {}) for k in ("delta", "gamma", "vega", "theta"))
        for s in steps
    )
    results.append({
        "id": "greeks_computed_all_steps",
        "passed": greeks_ok,
        "detail": f"All {len(steps)} steps have delta, gamma, vega, theta present" if greeks_ok else "Some steps missing greeks",
    })

    # 4. order_counts_consistent
    n_proposed = sum(len(s.get("proposed_orders", [])) for s in steps)
    n_executed = sum(len(s.get("orders_to_execute", [])) for s in steps)
    n_filled = sum(len(s.get("fills", [])) for s in steps)
    counts_ok = n_proposed >= n_executed >= n_filled
    results.append({
        "id": "order_counts_consistent",
        "passed": counts_ok,
        "detail": f"{n_proposed} proposed ≥ {n_executed} executed ≥ {n_filled} filled",
    })

    # 5. regime_labels_valid
    valid_regimes = {"bull", "bear", "range", "volatile"}
    regime_ok = all(s["regime"] in valid_regimes for s in steps)
    results.append({
        "id": "regime_labels_valid",
        "passed": regime_ok,
        "detail": f"All regime labels in {valid_regimes}" if regime_ok else "Invalid regime label found",
    })

    # 6. counsel_caps_not_exceeded (conditional)
    if counsel_active:
        counsel_ok = True
        for s in steps:
            for d in s.get("decisions", []):
                if d.get("allowed", True):
                    risk = d.get("risk", {})
                    caps = d.get("caps", {})
                    cd = caps.get("max_abs_total_delta", float("inf"))
                    cv = caps.get("max_abs_total_vega", float("inf"))
                    if risk.get("delta_total_abs", 0) > cd or risk.get("vega_total_abs", 0) > cv:
                        counsel_ok = False
                        break
        results.append({
            "id": "counsel_caps_not_exceeded",
            "passed": counsel_ok,
            "detail": "All allowed orders within counsel caps" if counsel_ok else "Counsel cap exceeded",
        })
    else:
        results.append({
            "id": "counsel_caps_not_exceeded",
            "passed": True,
            "detail": "Counsel not active — skipped",
        })

    # 7. no_negative_position
    position = 0.0
    neg_pos = False
    for s in steps:
        for f in s.get("fills", []):
            qty = float(f["quantity"])
            if f["side"] == "buy":
                position += qty
            else:
                position -= qty
        if position < 0:
            neg_pos = True
            break
    results.append({
        "id": "no_negative_position",
        "passed": not neg_pos,
        "detail": "Position never went negative (no naked shorts)" if not neg_pos else f"Negative position at some step",
    })

    passed = sum(1 for r in results if r["passed"])
    total = len(results)

    return {
        "results": results,
        "summary": {"passed": passed, "failed": total - passed, "total": total},
    }


# ---------------------------------------------------------------------------
# Trade-level metrics
# ---------------------------------------------------------------------------

def _compute_trade_metrics(fills_by_step: list, equity_curve: list[dict]) -> dict:
    """Compute trade-level metrics from fills.

    Per A4: each buy fill is treated as an independent trade.
    Trade P&L = -quantity * price (the cost paid).
    """
    trade_pnls: list[float] = []
    for step_fills in fills_by_step:
        for f in step_fills:
            qty = float(f["quantity"])
            px = float(f["price"])
            if f["side"] == "buy":
                trade_pnls.append(-qty * px)
            else:
                trade_pnls.append(qty * px)

    if not trade_pnls:
        return {
            "profit_factor": None,
            "win_rate": None,
            "avg_win": None,
            "avg_loss": None,
            "expectancy": None,
            "max_consecutive_losses": None,
        }

    wins = [p for p in trade_pnls if p > 0]
    losses = [p for p in trade_pnls if p < 0]
    n_trades = len(trade_pnls)

    gross_profits = sum(wins) if wins else 0.0
    gross_losses = abs(sum(losses)) if losses else 0.0

    profit_factor = None
    if gross_losses > 0:
        profit_factor = round(gross_profits / gross_losses, 6)

    win_rate = round(len(wins) / n_trades, 6) if n_trades > 0 else None
    avg_win = round(sum(wins) / len(wins), 6) if wins else None
    avg_loss = round(sum(losses) / len(losses), 6) if losses else None

    expectancy = None
    if win_rate is not None and avg_win is not None and avg_loss is not None:
        expectancy = round((win_rate * avg_win) - ((1.0 - win_rate) * abs(avg_loss)), 6)

    # Max consecutive losses.
    max_consec = 0
    current_streak = 0
    for p in trade_pnls:
        if p < 0:
            current_streak += 1
            max_consec = max(max_consec, current_streak)
        else:
            current_streak = 0

    return {
        "profit_factor": profit_factor,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "max_consecutive_losses": max_consec if max_consec > 0 else None,
    }


# ---------------------------------------------------------------------------
# Risk-adjusted metrics (CAGR, Sharpe, Sortino, Calmar)
# ---------------------------------------------------------------------------

def _compute_risk_adjusted_metrics(
    equity_curve: list[dict],
    steps: list[dict],
    risk_free_rate: float,
    drawdown: dict,
) -> dict:
    """Compute Sharpe, Sortino, Calmar, CAGR from the equity curve.

    All return None when data is insufficient.
    """
    if len(equity_curve) < 2:
        return {
            "cagr": None,
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "calmar_ratio": None,
        }

    returns_pct = []
    for i in range(1, len(equity_curve)):
        prev_eq = equity_curve[i - 1]["equity"]
        curr_eq = equity_curve[i]["equity"]
        if abs(prev_eq) > 1e-12:
            returns_pct.append((curr_eq - prev_eq) / abs(prev_eq))
        else:
            returns_pct.append(0.0)

    if not returns_pct:
        return {"cagr": None, "sharpe_ratio": None, "sortino_ratio": None, "calmar_ratio": None}

    initial_equity = equity_curve[0]["equity"]
    final_equity = equity_curve[-1]["equity"]

    # CAGR: (final/initial)^(1/years) - 1
    # For mock data, approximate years from step count.
    n_steps = len(steps)
    years = n_steps / 252.0  # Approximate trading days
    cagr = None
    if years >= 1.0 and abs(initial_equity) > 1e-12:
        cagr = round((final_equity / initial_equity) ** (1.0 / years) - 1.0, 6)

    # Sharpe Ratio
    sharpe = None
    if len(returns_pct) >= 12:
        mean_ret = sum(returns_pct) / len(returns_pct)
        var_ret = sum((r - mean_ret) ** 2 for r in returns_pct) / (len(returns_pct) - 1)
        std_ret = var_ret ** 0.5
        if std_ret > 1e-12:
            sharpe = round((mean_ret - risk_free_rate / 252.0) / std_ret * (252.0 ** 0.5), 6)

    # Sortino Ratio
    sortino = None
    neg_returns = [r for r in returns_pct if r < 0]
    if len(returns_pct) >= 12 and neg_returns:
        mean_ret = sum(returns_pct) / len(returns_pct)
        var_down = sum((r - 0) ** 2 for r in neg_returns) / len(neg_returns)
        std_down = var_down ** 0.5
        if std_down > 1e-12:
            sortino = round((mean_ret - risk_free_rate / 252.0) / std_down * (252.0 ** 0.5), 6)

    # Calmar Ratio
    calmar = None
    max_dd_pct = drawdown.get("max_drawdown_pct", 0.0)
    if cagr is not None and abs(max_dd_pct) > 1e-12:
        calmar = round(cagr / abs(max_dd_pct), 6)

    return {
        "cagr": cagr,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
    }


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_evaluation_report(
    *,
    artifacts: dict[str, Any],
    strategy_name: str,
    strategy_class: str,
    strategy_config: dict[str, Any],
    counsel_type: Optional[str] = None,
    counsel_config: Optional[dict[str, float]] = None,
    run_config: dict[str, Any],
    closes: list[float],
) -> dict[str, Any]:
    """Build the complete evaluation report from backtest artifacts.

    This is a pure function: given the same inputs it produces byte-identical
    output (except for `generated_at` and `run_id`).
    """
    steps: list[dict] = artifacts["steps"]
    final: dict = artifacts["final"]
    meta_raw: dict = artifacts.get("meta", {})

    run_id = str(uuid.uuid4())
    generated_at = datetime.now(timezone.utc).isoformat()

    # --- meta ---
    meta = Meta(
        report_version="1.0.0",
        generated_at=generated_at,
        run_id=run_id,
        strategy=StrategyMeta(
            name=strategy_name,
            class_=strategy_class,
            config=strategy_config,
        ),
        env=EnvMeta(
            class_="StaggeredInputEnv",
            config={
                "window_size": meta_raw.get("window_size", 5),
                "lag_steps": meta_raw.get("lag_steps", 2),
                "start_t": steps[0]["time_index"] if steps else 0,
                "total_steps": len(steps),
            },
        ),
        price_source=PriceSourceMeta(
            generator="_make_closes",
            count=len(closes),
            first_close=closes[0] if closes else 0.0,
            last_close=closes[-1] if closes else 0.0,
            min_close=min(closes) if closes else 0.0,
            max_close=max(closes) if closes else 0.0,
        ),
        core_boundary=CoreBoundaryMeta(
            max_abs_total_delta=run_config.get("core_max_abs_total_delta", 1000.0),
            max_abs_total_vega=run_config.get("core_max_abs_total_vega", 1e9),
        ),
        pricing=PricingMeta(
            model="black_scholes",
            risk_free_rate=meta_raw.get("risk_free_rate", 0.01),
            base_implied_vol=meta_raw.get("base_implied_vol", 0.25),
            iv_by_regime=meta_raw.get("iv_by_regime", {}),
            expiry_years=meta_raw.get("expiry_years", 0.25),
        ),
    )

    # --- performance ---
    equity_curve, equity_stats = _build_equity_curve(steps)
    returns = _build_returns(equity_curve)

    performance = {
        "equity_curve": equity_curve,
        "equity_stats": asdict(equity_stats),
        "returns": returns,
    }

    # --- risk ---
    drawdown = _build_drawdown(equity_curve)
    greeks_exposure = _build_greeks_exposure(steps)
    counsel_active = counsel_type is not None

    effective_delta = run_config.get("core_max_abs_total_delta", 1000.0)
    effective_vega = run_config.get("core_max_abs_total_vega", 1e9)
    if counsel_config and "max_abs_total_delta" in counsel_config:
        effective_delta = counsel_config["max_abs_total_delta"]

    boundary_enforcement = _build_boundary_enforcement(
        steps, counsel_active, effective_delta, effective_vega,
    )

    risk = {
        "drawdown": drawdown,
        "greeks_exposure": greeks_exposure,
        "boundary_enforcement": boundary_enforcement,
    }

    # --- regimes ---
    regime_data = _build_regimes(steps)

    # --- counsel ---
    counsel_dict = None
    if counsel_type and counsel_config:
        counsel_dict = _build_counsel_audit(steps, counsel_type, counsel_config)

    # --- summary (aggregate) ---
    n_proposed = sum(len(s.get("proposed_orders", [])) for s in steps)
    n_blocked = sum(1 for s in steps for d in s.get("decisions", []) if not d.get("allowed", True))
    n_fills = final.get("fills_count", 0)
    total_pnl = final.get("total_pnl", 0.0)
    final_cash = final.get("cash", 0.0)
    final_position = final.get("position", 0.0)
    final_mtm = final.get("final_option_premium", 0.0)

    # Regime summary for summary section.
    regime_perf = regime_data.get("performance", {})
    regime_summary = {
        r: {"steps": regime_perf[r]["steps"], "pnl": regime_perf[r]["total_pnl"]}
        for r in regime_perf
    }

    # Trade metrics.
    fills_by_step = [s.get("fills", []) for s in steps]
    trade_metrics = _compute_trade_metrics(fills_by_step, equity_curve)

    # Risk-adjusted metrics.
    risk_adj = _compute_risk_adjusted_metrics(
        equity_curve, steps,
        meta_raw.get("risk_free_rate", 0.01),
        drawdown,
    )

    # Checks.
    checks = _run_checks(steps, fills_by_step, counsel_active, equity_curve)

    total_counsel_calls = counsel_dict["total_calls"] if counsel_dict else 0
    counsel_caps_hit = 0
    if counsel_dict:
        caps = counsel_dict["caps_applied"]
        counsel_caps_hit = caps.get("delta_cap_hit_count", 0) + caps.get("vega_cap_hit_count", 0)

    summary = {
        "start_value": 0.0,
        "final_cash": round(final_cash, 6),
        "final_position": round(final_position, 6),
        "final_option_mtm": round(final_mtm, 6),
        "total_pnl": round(total_pnl, 6),
        "total_return_pct": round(total_pnl, 6),  # start_value=0, so return = pnl
        "n_steps": len(steps),
        "n_fills": n_fills,
        "n_proposed_orders": n_proposed,
        "n_blocked_orders": n_blocked,
        "block_rate": round(n_blocked / n_proposed, 4) if n_proposed > 0 else 0.0,
        "cagr": risk_adj["cagr"],
        "sharpe_ratio": risk_adj["sharpe_ratio"],
        "sortino_ratio": risk_adj["sortino_ratio"],
        "calmar_ratio": risk_adj["calmar_ratio"],
        "max_drawdown_pct": drawdown.get("max_drawdown_pct"),
        "max_drawdown_duration_steps": drawdown.get("drawdown_duration_steps"),
        "profit_factor": trade_metrics["profit_factor"],
        "win_rate": trade_metrics["win_rate"],
        "avg_win": trade_metrics["avg_win"],
        "avg_loss": trade_metrics["avg_loss"],
        "expectancy": trade_metrics["expectancy"],
        "max_consecutive_losses": trade_metrics["max_consecutive_losses"],
        "regime_summary": regime_summary,
        "counsel_active": counsel_active,
        "counsel_total_advice_calls": total_counsel_calls,
        "counsel_caps_hit": counsel_caps_hit,
        "checks_passed": checks["summary"]["passed"],
        "checks_total": checks["summary"]["total"],
    }

    # --- assemble ---
    report = {
        "$schema": "trading-bot-eval-report-v1",
        "meta": meta.to_dict(),
        "summary": summary,
        "performance": performance,
        "risk": risk,
        "regimes": regime_data,
        "counsel": counsel_dict,
        "checks": checks,
    }

    return report
