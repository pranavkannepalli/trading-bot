# Evaluation Report Spec — Trading Bot (LLM Counsel)

**Status:** RFC / Specification  
**Scope:** Non-agentic trading engine with regime detection, options risk/greeks, backtesting, and optional LLM counsel  
**Constraint:** No dependency on live credentials — fully self-contained in the backtesting pipeline  

---

## 1. Purpose

The Evaluation Report is the **single artifact** that answers: *"How did this strategy perform, at what risk, and under what constraints?"*  

It is produced by the backtesting pipeline at the end of a run and consumed by:
- **Humans:** to decide if a strategy is worth paper-trading or deploying  
- **Automation:** to compare runs across parameter sweeps, to gate model promotion  
- **LLM Counsel:** to provide context for future counsel sessions (read-only access to prior reports)  

The report is **deterministic given the same inputs** — no randomness, no network calls, no live data.

---

## 2. Design Principles

| # | Principle | Rationale |
|---|-----------|-----------|
| P1 | **Self-contained JSON** | One file = one report. No external references needed to interpret results. |
| P2 | **Reproducible** | Given the same price series + strategy + config, the report must be byte-identical across runs. Timestamps are wall-clock only in a `generated_at` field. |
| P3 | **Leakage-free** | All metrics are computed from fills/equity curves that use only past (or current-step) data. No future information bleeds into any metric. |
| P4 | **Human- and machine-readable** | The same JSON feeds a CLI summary, a dashboard, and an automated model promotion gate. |
| P5 | **Incremental** | Every metric is independently computable. If we add a metric later, old reports remain valid. |
| P6 | **No credentials** | The report contains no API keys, account numbers, or secrets. It is safe to commit, share, or diff. |

---

## 3. Report Sections

The report is organized into six top-level sections:

```
report
├── meta            # Run identity & configuration
├── summary         # Top-line numbers (one-glance verdict)
├── performance     # Time-series & risk-adjusted metrics
├── risk            # Drawdowns, greeks exposure, boundary enforcement
├── regimes         # Per-regime performance decomposition
├── counsel         # LLM counsel audit trail (if active)
└── checks          # Automated pass/fail assertions
```

### 3.1 `meta` — Run Identity

```json
{
  "meta": {
    "report_version": "1.0.0",
    "generated_at": "2026-06-11T22:30:00Z",
    "run_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "strategy": {
      "name": "regime_aware_option_strategy",
      "class": "RegimeAwareOptionStrategy",
      "config": {
        "underlying": "MOCK",
        "strike": 100.0,
        "expiry_years": 0.25,
        "quantity_per_step": 10.0
      }
    },
    "env": {
      "class": "StaggeredInputEnv",
      "config": {
        "window_size": 5,
        "lag_steps": 2,
        "start_t": 10,
        "total_steps": 60
      }
    },
    "price_source": {
      "generator": "_make_closes",
      "count": 70,
      "first_close": 100.0,
      "last_close": 112.0,
      "min_close": 92.0,
      "max_close": 112.0
    },
    "core_boundary": {
      "max_abs_total_delta": 1000.0,
      "max_abs_total_vega": 1000000000.0
    },
    "pricing": {
      "model": "black_scholes",
      "risk_free_rate": 0.01,
      "base_implied_vol": 0.25,
      "iv_by_regime": {
        "bull": 0.20,
        "bear": 0.30,
        "range": 0.25,
        "volatile": 0.40
      },
      "expiry_years": 0.25
    }
  }
}
```

**Key evidence:** The `meta` section is a complete fingerprint. If two reports have identical `meta` but different `summary`, something is wrong.

---

### 3.2 `summary` — Top-Line Verdict

The one-glance section. Everything here is derivable from the full trace.

```json
{
  "summary": {
    "start_value": 0.0,
    "final_cash": -27.93,
    "final_position": 25.0,
    "final_option_mtm": 0.0,
    "total_pnl": -27.93,
    "total_return_pct": -27.93,

    "n_steps": 60,
    "n_fills": 30,
    "n_proposed_orders": 60,
    "n_blocked_orders": 15,
    "block_rate": 0.25,

    "cagr": null,
    "sharpe_ratio": null,
    "sortino_ratio": null,
    "calmar_ratio": null,
    "max_drawdown_pct": null,
    "max_drawdown_duration_steps": null,
    "profit_factor": null,
    "win_rate": null,
    "avg_win": null,
    "avg_loss": null,
    "expectancy": null,

    "regime_summary": {
      "bull":   { "steps": 15, "pnl": -5.00 },
      "bear":   { "steps": 18, "pnl": -15.00 },
      "range":  { "steps": 15, "pnl": -3.00 },
      "volatile": { "steps": 12, "pnl": -4.93 }
    },

    "counsel_active": true,
    "counsel_total_advice_calls": 60,
    "counsel_caps_hit": 15,

    "checks_passed": 3,
    "checks_total": 5
  }
}
```

**Why many are `null`:** The current demo uses mock prices with a single-contract-per-step model. Time-series metrics (CAGR, Sharpe, etc.) require a meaningful equity curve. The spec defines them now so the schema is stable; implementations populate them when the underlying data supports it (e.g., a multi-contract portfolio run with enough steps).

**Metrics that ARE computable today:**
- `total_pnl`, `total_return_pct`, `final_cash`, `final_position`
- `n_steps`, `n_fills`, `n_proposed_orders`, `n_blocked_orders`, `block_rate`
- `regime_summary` (from per-step regime labels in existing artifacts)
- `counsel_active`, `counsel_total_advice_calls`, `counsel_caps_hit`

---

### 3.3 `performance` — Time-Series Metrics

Computed from the **equity curve** (cash + mark-to-market position at each step).

#### 3.3.1 Equity Curve

```json
{
  "performance": {
    "equity_curve": [
      { "step": 10, "cash": 0.0,   "position": 0.0,  "mtm": 0.0,   "equity": 0.0    },
      { "step": 11, "cash": -18.0, "position": 10.0, "mtm": 1.80,  "equity": 0.0    },
      { "step": 12, "cash": -36.0, "position": 20.0, "mtm": 3.20,  "equity": -4.0   }
    ],
    "equity_stats": {
      "peak_equity": 15.0,
      "trough_equity": -50.0,
      "final_equity": -27.93,
      "mean_equity": -12.5,
      "equity_std": 18.3
    }
  }
}
```

`equity[t] = cash[t] + position[t] * option_premium[t]`  
`mtm` (mark-to-market) is the current step's option premium × position. If no position, `mtm = 0`.

#### 3.3.2 Risk-Adjusted Metrics (defined, computed when applicable)

| Metric | Formula | When `null` |
|--------|---------|-------------|
| **CAGR** | `(final_equity / initial_equity)^(1/years) - 1` | `< 1 year of data` or `initial_equity = 0` |
| **Sharpe Ratio** | `(mean_return - risk_free_rate) / stddev(returns)` | `< 12 periods` |
| **Sortino Ratio** | `(mean_return - risk_free_rate) / stddev(negative_returns)` | `< 12 periods` or `no negative returns` |
| **Calmar Ratio** | `CAGR / abs(max_drawdown_pct)` | `null` if CAGR is `null` or `max_drawdown = 0` |
| **Profit Factor** | `sum(gross_profits) / sum(gross_losses)` | `no losses` → `null` |
| **Win Rate** | `winning_trades / total_trades` | `no trades` → `null` |
| **Avg Win / Avg Loss** | Mean P&L of winning/losing trades | `null` if no trades of that type |
| **Expectancy** | `(win_rate * avg_win) - ((1 - win_rate) * avg_loss)` | `null` if win_rate or avgs are `null` |
| **Max Consecutive Losses** | Longest streak of losing trades | `null` if no trades |

**Trade definition for options:** A "trade" is a buy-fill paired with its eventual position delta. For simplicity in the MVP, each fill is treated as an independent trade with P&L = `-quantity * fill_price` (buy cost). More sophisticated pairing (FIFO matching) is a future enhancement.

#### 3.3.3 Returns Series

```json
{
  "returns": {
    "step_returns": [0.0, 0.0, -0.04, 0.02, ...],
    "step_returns_pct": [0.0, 0.0, -0.022, 0.011, ...],
    "cumulative_return": [1.0, 1.0, 0.978, 0.989, ...]
  }
}
```

Computed from equity curve: `step_return[t] = equity[t] - equity[t-1]` (first step = 0).

---

### 3.4 `risk` — Drawdowns, Greeks, Boundary Enforcement

#### 3.4.1 Drawdown Analysis

```json
{
  "risk": {
    "drawdown": {
      "max_drawdown_pct": 0.35,
      "max_drawdown_absolute": -35.0,
      "peak_step": 15,
      "trough_step": 42,
      "recovery_step": null,
      "drawdown_duration_steps": 27,
      "drawdown_series": [
        { "step": 10, "equity": 0.0,   "peak": 0.0,   "drawdown_pct": 0.0   },
        { "step": 15, "equity": 15.0,  "peak": 15.0,  "drawdown_pct": 0.0   },
        { "step": 20, "equity": -5.0,  "peak": 15.0,  "drawdown_pct": -1.33 }
      ]
    }
  }
}
```

Drawdown at step `t`: `(equity[t] - max_peak_so_far) / abs(max_peak_so_far)` when peak > 0, else `equity[t] - peak`.  
`recovery_step = null` means drawdown had not recovered to a new high by the end of the run.

#### 3.4.2 Greeks Exposure Over Time

```json
{
  "risk": {
    "greeks_exposure": {
      "timeseries": [
        {
          "step": 10,
          "regime": "bear",
          "implied_vol": 0.30,
          "total_delta": -2.20,
          "total_gamma": 0.23,
          "total_vega": 65.60,
          "total_theta": -11.75,
          "delta_abs": 2.20,
          "vega_abs": 65.60
        }
      ],
      "extremes": {
        "max_abs_delta": 4.50,
        "max_abs_vega": 140.0,
        "max_abs_gamma": 0.55,
        "max_abs_theta": 30.0
      },
      "by_regime": {
        "bear": {
          "mean_abs_delta": 2.10,
          "mean_abs_vega": 60.0,
          "step_count": 18
        }
      }
    }
  }
}
```

**Key insight:** Greeks exposure is computed on the **position held after fills**, not on proposed orders. This is the risk the portfolio actually carries.

#### 3.4.3 Core Boundary Enforcement

```json
{
  "risk": {
    "boundary_enforcement": {
      "delta_cap_effective": 2.0,
      "vega_cap_effective": 1000000000.0,
      "total_orders_proposed": 60,
      "total_orders_blocked": 15,
      "blocked_by": {
        "delta": 15,
        "vega": 0,
        "both": 0
      },
      "blocked_at_steps": [12, 15, 18, 22, 25, 28, 30, 33, 36, 40, 42, 45, 48, 50, 55],
      "worst_blocked_risk": {
        "step": 15,
        "delta_abs": 4.50,
        "vega_abs": 140.0,
        "cap_delta": 2.0,
        "cap_vega": 1000000000.0
      }
    }
  }
}
```

---

### 3.5 `regimes` — Per-Regime Decomposition

```json
{
  "regimes": {
    "detector": "SimpleTrendVolRegimeDetector",
    "detector_config": {
      "window_size": 5,
      "vol_threshold": 0.10,
      "range_slope_threshold": 0.01
    },
    "distribution": {
      "bull":      { "count": 15, "pct": 0.25 },
      "bear":      { "count": 18, "pct": 0.30 },
      "range":     { "count": 15, "pct": 0.25 },
      "volatile":  { "count": 12, "pct": 0.20 }
    },
    "transitions": {
      "bull->bear": 3,
      "bull->range": 2,
      "bear->bull": 4,
      "bear->volatile": 3,
      "volatile->bear": 5,
      "volatile->range": 2,
      "range->bull": 4,
      "range->bear": 3
    },
    "performance": {
      "bull": {
        "steps": 15,
        "fills": 8,
        "total_pnl": -5.0,
        "mean_pnl_per_step": -0.333,
        "blocked_orders": 7,
        "option_type_used": "call"
      },
      "bear": {
        "steps": 18,
        "fills": 10,
        "total_pnl": -15.0,
        "mean_pnl_per_step": -0.833,
        "blocked_orders": 8,
        "option_type_used": "put"
      }
    }
  }
}
```

**Purpose:** Tells you if the strategy works in all regimes or only in specific ones. A strategy that makes money only in `bull` is fragile.

**Transition matrix:** Counts regime changes between consecutive steps. Helps detect regime-hopping frequency and whether the detector is too noisy.

---

### 3.6 `counsel` — LLM Counsel Audit Trail

Only present if counsel was active during the run.

```json
{
  "counsel": {
    "active": true,
    "counsel_type": "StrictDeltaCounsel",
    "counsel_config": {
      "max_abs_total_delta": 2.0
    },
    "total_calls": 60,
    "caps_applied": {
      "delta_cap_hit_count": 15,
      "vega_cap_hit_count": 0,
      "quantity_clamp_count": 0
    },
    "advice_summary": [
      {
        "step": 10,
        "advice": { "max_abs_total_delta": 2.0 },
        "orders_before": 1,
        "orders_after": 1,
        "delta_cap_triggered": false
      },
      {
        "step": 15,
        "advice": { "max_abs_total_delta": 2.0 },
        "orders_before": 1,
        "orders_after": 0,
        "delta_cap_triggered": true,
        "blocked_delta_abs": 4.50
      }
    ]
  }
}
```

**Critical invariant:** The counsel section records what the counsel *advised* and whether its caps were *hit*. It does not record the counsel's internal reasoning (that's an LLM black box outside our boundary). The audit trail proves the counsel was consulted but did not have execution authority.

---

### 3.7 `checks` — Automated Assertions

A set of boolean checks that run after report generation. These are **machine-actionable gates**.

```json
{
  "checks": {
    "results": [
      {
        "id": "no_future_leakage",
        "passed": true,
        "detail": "All 60 steps reference time_index ≤ max allowed"
      },
      {
        "id": "boundary_enforced",
        "passed": true,
        "detail": "0 allowed orders exceeded risk caps; 15 blocked"
      },
      {
        "id": "greeks_computed_all_steps",
        "passed": true,
        "detail": "All 60 steps have delta, gamma, vega, theta present"
      },
      {
        "id": "no_negative_position",
        "passed": true,
        "detail": "Position never went negative (no naked shorts)"
      },
      {
        "id": "counsel_caps_not_exceeded",
        "passed": true,
        "detail": "No allowed order exceeded counsel delta cap of 2.0"
      }
    ],
    "summary": {
      "passed": 5,
      "failed": 0,
      "total": 5
    }
  }
}
```

**Standard checks (all runs):**

| Check ID | What it verifies |
|----------|-----------------|
| `no_future_leakage` | No step references a time index beyond the price series |
| `boundary_enforced` | Allowed orders all satisfy risk caps |
| `greeks_computed_all_steps` | Every step has greeks computed |
| `order_counts_consistent` | `n_proposed ≥ n_executed ≥ n_filled` |
| `regime_labels_valid` | All regime labels are in `{bull, bear, range, volatile}` |

**Conditional checks:**

| Check ID | Condition | What it verifies |
|----------|-----------|-----------------|
| `counsel_caps_not_exceeded` | `counsel.active = true` | No executed order exceeds counsel's caps |
| `no_negative_position` | options backtest | Position never goes negative |
| `equity_curve_monotonic_peaks` | equity curve present | Peaks are non-decreasing |

**Purpose of checks:** A failed check is a **blocker**. The run is invalid regardless of P&L. These are the equivalent of CI lint rules for trading.

---

## 4. Complete Report JSON Schema

### Top-level structure

```json
{
  "$schema": "trading-bot-eval-report-v1",
  "meta": { ... },
  "summary": { ... },
  "performance": { ... },
  "risk": { ... },
  "regimes": { ... },
  "counsel": { ... } | null,
  "checks": { ... }
}
```

### Type definitions (Python dataclass-style)

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class EvalReport:
    schema_: str  # "$schema" in JSON
    meta: Meta
    summary: Summary
    performance: Performance
    risk: Risk
    regimes: Regimes
    counsel: Optional[CounselAudit]
    checks: Checks

@dataclass
class Meta:
    report_version: str
    generated_at: str  # ISO 8601
    run_id: str
    strategy: StrategyMeta
    env: EnvMeta
    price_source: PriceSourceMeta
    core_boundary: CoreBoundaryMeta
    pricing: PricingMeta

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
    regime_summary: dict[str, RegimeSummaryItem]
    counsel_active: bool
    counsel_total_advice_calls: int
    counsel_caps_hit: int
    checks_passed: int
    checks_total: int

@dataclass
class RegimeSummaryItem:
    steps: int
    pnl: float
```

---

## 5. Integration Points

### 5.1 Producer: `NonAgenticOptionsBacktestRunner`

The report generator lives **after** the existing artifact writing in `demo_non_agentic_boundary.py`:

```python
# After self.run() returns artifacts dict:
report = build_evaluation_report(
    artifacts=artifacts,
    strategy=strategy,
    counsel=counsel,
    run_config={...}
)
with open(os.path.join(args.out, "evaluation_report.json"), "w") as f:
    json.dump(report, f, indent=2)
```

The `build_evaluation_report()` function is a **pure function** of the artifacts dict + config. It does not re-run the backtest.

### 5.2 Consumer: CLI Summary

```bash
# Print the one-glance verdict
cat artifacts/evaluation_report.json | python3 -c "
import json, sys
r = json.load(sys.stdin)
s = r['summary']
print(f'P&L: {s[\"total_pnl\"]:.2f} | Fills: {s[\"n_fills\"]} | Blocked: {s[\"n_blocked_orders\"]} ({s[\"block_rate\"]:.0%})')
print(f'Checks: {s[\"checks_passed\"]}/{s[\"checks_total\"]} passed')
for reg, data in s['regime_summary'].items():
    print(f'  {reg}: {data[\"steps\"]} steps, P&L={data[\"pnl\"]:.2f}')
"
```

### 5.3 Consumer: Automated Gate

```python
def should_promote_to_paper(report: dict) -> bool:
    """Gate: pass all checks AND have non-negative total P&L."""
    checks = report["checks"]
    if checks["summary"]["failed"] > 0:
        return False
    # Optional: require positive expectancy, Sharpe > 0.5, etc.
    return True
```

### 5.4 Consumer: LLM Counsel Context

The counsel can receive the last N evaluation reports as context:

```python
def advise_with_history(self, proposed_orders, market_state, prior_reports):
    # prior_reports: list[EvalReport] — read-only
    # Use regime performance, drawdown history, blocked order patterns
    # to inform dynamic risk caps
    return Advice(max_abs_total_delta=adaptive_cap)
```

**Safety invariant:** The counsel reads reports but cannot mutate them. Reports are append-only artifacts.

---

## 6. Assumptions

| # | Assumption | Impact |
|---|-----------|--------|
| A1 | **Mock prices only (MVP)** | No real market data in reports. Metrics like Sharpe/CAGR are defined but may be `null` for short/synthetic runs. |
| A2 | **Single contract per step** | The demo proposes one option order per step. Multi-contract portfolios would need aggregated greeks exposure. |
| A3 | **No transaction costs** | `BacktestExecutionEngine` fills at market close with no commission, slippage, or fees. Reports reflect this. |
| A4 | **Fill = trade (MVP)** | Each buy fill is treated as an independent trade. FIFO/weighted-average matching is a future enhancement. |
| A5 | **Time decay is linear** | `remaining_T` decreases linearly. Real options use actual calendar days and trading-day conventions. |
| A6 | **Report is append-only** | Once written, a report is immutable. Diffing two reports with identical `meta` should produce no changes (except `generated_at`). |
| A7 | **No portfolio-level risk** | Current core boundary checks per-order delta/vega. Aggregate portfolio greeks (sum across all positions) are computed for reporting but not filtered. |
| A8 | **Regime detector is toy-grade** | `SimpleTrendVolRegimeDetector` uses naive slope+vol heuristics. Reports faithfully record what the detector saw, not ground truth. |
| A9 | **Counsel is deterministic for MVP** | `StrictDeltaCounsel` returns a static cap. Real counsel would be dynamic but still only return `Advice`. |
| A10 | **Python ≥ 3.10** | Uses `from __future__ import annotations`, `dataclasses`, and `Literal` types. |

---

## 7. Limitations (Explicit Non-Goals for v1)

1. **No benchmark comparison.** The report doesn't compare to S&P 500 or any index. That requires a benchmark price series fed through the same pipeline — a v2 feature.
2. **No statistical significance tests.** No t-tests, no bootstrap confidence intervals for Sharpe. These are important but out of scope for MVP.
3. **No walk-forward optimization report.** The report covers a single backtest run. Walk-forward (train/test split analysis) is a v2 feature.
4. **No Monte Carlo / scenario stress testing.** All metrics are from a single deterministic price path.
5. **No real-time / live trading report variant.** The spec is for backtesting only. A live report would need mark-to-market, open P&L, and broker state — different schema entirely.
6. **No multi-asset correlation analysis.** Single underlying, single contract type per step.

---

## 8. Example: Minimal Valid Report

A run with `--case default --steps 20 --quantity 5` (no counsel, short run) would produce:

```json
{
  "$schema": "trading-bot-eval-report-v1",
  "meta": {
    "report_version": "1.0.0",
    "generated_at": "2026-06-11T22:30:00Z",
    "run_id": "abc123",
    "strategy": {
      "name": "regime_aware_option_strategy",
      "class": "RegimeAwareOptionStrategy",
      "config": { "underlying": "MOCK", "strike": 100.0, "expiry_years": 0.25, "quantity_per_step": 5.0 }
    },
    "env": { "class": "StaggeredInputEnv", "config": { "window_size": 5, "lag_steps": 2, "start_t": 10, "total_steps": 20 } },
    "price_source": { "generator": "_make_closes", "count": 30, "first_close": 100.0, "last_close": 112.0, "min_close": 100.0, "max_close": 112.0 },
    "core_boundary": { "max_abs_total_delta": 1000.0, "max_abs_total_vega": 1000000000.0 },
    "pricing": { "model": "black_scholes", "risk_free_rate": 0.01, "base_implied_vol": 0.25, "iv_by_regime": { "bull": 0.20, "bear": 0.30, "range": 0.25, "volatile": 0.40 }, "expiry_years": 0.25 }
  },
  "summary": {
    "start_value": 0.0, "final_cash": -45.20, "final_position": 25.0, "final_option_mtm": 0.0,
    "total_pnl": -45.20, "total_return_pct": -45.20,
    "n_steps": 20, "n_fills": 10, "n_proposed_orders": 20, "n_blocked_orders": 10, "block_rate": 0.50,
    "cagr": null, "sharpe_ratio": null, "sortino_ratio": null, "calmar_ratio": null,
    "max_drawdown_pct": null, "max_drawdown_duration_steps": null,
    "profit_factor": null, "win_rate": null, "avg_win": null, "avg_loss": null, "expectancy": null,
    "regime_summary": { "bull": { "steps": 5, "pnl": -10.0 }, "bear": { "steps": 8, "pnl": -25.0 }, "range": { "steps": 4, "pnl": -5.0 }, "volatile": { "steps": 3, "pnl": -5.20 } },
    "counsel_active": false, "counsel_total_advice_calls": 0, "counsel_caps_hit": 0,
    "checks_passed": 4, "checks_total": 5
  },
  "performance": {
    "equity_curve": [ { "step": 10, "cash": 0.0, "position": 0.0, "mtm": 0.0, "equity": 0.0 } ],
    "equity_stats": { "peak_equity": 0.0, "trough_equity": -45.20, "final_equity": -45.20, "mean_equity": -22.60, "equity_std": 15.0 },
    "returns": { "step_returns": [0.0], "step_returns_pct": [0.0], "cumulative_return": [1.0] }
  },
  "risk": {
    "drawdown": { "max_drawdown_pct": 0.452, "max_drawdown_absolute": -45.20, "peak_step": 10, "trough_step": 29, "recovery_step": null, "drawdown_duration_steps": 19, "drawdown_series": [] },
    "greeks_exposure": { "timeseries": [], "extremes": { "max_abs_delta": 3.20, "max_abs_vega": 95.0, "max_abs_gamma": 0.30, "max_abs_theta": 25.0 }, "by_regime": {} },
    "boundary_enforcement": { "delta_cap_effective": 1000.0, "vega_cap_effective": 1000000000.0, "total_orders_proposed": 20, "total_orders_blocked": 10, "blocked_by": { "delta": 10, "vega": 0, "both": 0 }, "blocked_at_steps": [], "worst_blocked_risk": { "step": 15, "delta_abs": 3.20, "vega_abs": 95.0, "cap_delta": 1000.0, "cap_vega": 1000000000.0 } }
  },
  "regimes": {
    "detector": "SimpleTrendVolRegimeDetector",
    "detector_config": { "window_size": 5, "vol_threshold": 0.10, "range_slope_threshold": 0.01 },
    "distribution": { "bull": { "count": 5, "pct": 0.25 }, "bear": { "count": 8, "pct": 0.40 }, "range": { "count": 4, "pct": 0.20 }, "volatile": { "count": 3, "pct": 0.15 } },
    "transitions": {},
    "performance": {}
  },
  "counsel": null,
  "checks": {
    "results": [
      { "id": "no_future_leakage", "passed": true, "detail": "All steps OK" },
      { "id": "boundary_enforced", "passed": true, "detail": "No violations" },
      { "id": "greeks_computed_all_steps", "passed": true, "detail": "All 20 steps have greeks" },
      { "id": "order_counts_consistent", "passed": true, "detail": "20 proposed ≥ 10 executed ≥ 10 filled" },
      { "id": "counsel_caps_not_exceeded", "passed": true, "detail": "Counsel not active — skipped" }
    ],
    "summary": { "passed": 5, "failed": 0, "total": 5 }
  }
}
```

---

## 9. Implementation Roadmap

### Phase 1: Report Generator (this spec → code)

- [ ] `trading_bot/evaluation_report.py` — module with `build_evaluation_report(artifacts, strategy, counsel, config) -> dict`
- [ ] Unit tests: `tests/test_evaluation_report.py`
  - `test_empty_run_produces_valid_report`
  - `test_all_checks_pass_on_valid_artifacts`
  - `test_leakage_check_fails_when_future_index_present`
  - `test_boundary_check_fails_when_cap_exceeded`
  - `test_counsel_section_null_when_no_counsel`
  - `test_report_is_deterministic`
- [ ] Wire into `demo_non_agentic_boundary.py` → writes `evaluation_report.json`
- [ ] Add `--report` flag (default: `true`) to control report generation

### Phase 2: Time-Series Metrics

- [ ] Implement equity curve computation
- [ ] Implement drawdown series
- [ ] Implement CAGR, Sharpe, Sortino, Calmar (with `null` guards)
- [ ] Implement trade-level metrics (profit factor, win rate, expectancy)
- [ ] Unit tests for each metric with known-answer fixtures

### Phase 3: Regime & Counsel Deep-Dive

- [ ] Regime transition matrix computation
- [ ] Per-regime greeks statistics
- [ ] Counsel audit trail compaction (deduplicate unchanged advice)
- [ ] Counsel effectiveness score: `(orders_blocked_under_counsel_cap) / (total_orders)`

### Phase 4: Consumer Tooling

- [ ] CLI: `python3 -m trading_bot.evaluate --report artifacts/evaluation_report.json`
- [ ] Gate function: `should_promote(report)` with configurable thresholds
- [ ] Diff tool: `python3 -m trading_bot.diff_reports report_v1.json report_v2.json`

---

## 10. Key Evidence (Summary Bullets)

1. **The spec defines a self-contained JSON report** with 7 sections covering identity, performance, risk, regimes, counsel, and automated checks — all derivable from existing backtest artifacts without re-running.

2. **Metrics are defined with explicit nullability.** Time-series metrics (Sharpe, CAGR, Calmar) are specified now with clear "when null" rules, so the schema is stable even when mock/short runs can't populate them.

3. **Regime decomposition** is first-class: per-regime P&L, greeks exposure, transition matrix, and option-type usage are all tracked. This is critical because the strategy switches between calls and puts based on regime.

4. **The counsel audit trail** records every advice call, which caps were hit, and whether orders were blocked — proving the counsel was consulted but never had execution authority (the core invariant).

5. **Automated checks** function as CI-style gates: `no_future_leakage`, `boundary_enforced`, `greeks_computed_all_steps`, `order_counts_consistent`, `counsel_caps_not_exceeded`. A failed check invalidates the run regardless of P&L.

6. **The report is a pure function** of the existing artifacts dict + run config. It does not re-run the backtest, does not access the network, and is byte-identical given identical inputs (except `generated_at`).

7. **Industry-standard metrics** are included: Sharpe Ratio (risk-adjusted return), Sortino Ratio (downside-only), Calmar Ratio (return vs. max drawdown), Profit Factor (gross profit / gross loss), Win Rate, Expectancy, and Max Consecutive Losses.

8. **Greeks exposure over time** is tracked at the portfolio level — total delta, gamma, vega, theta per step — with extremes and per-regime breakdowns. This is essential for options strategies.

9. **The drawdown analysis** includes not just max drawdown percentage but also peak/trough/recovery steps, drawdown duration, and the full drawdown time series — enabling recovery math and psychological impact assessment.

10. **Integration is minimal and backward-compatible.** The report generator is a new module (`evaluation_report.py`) that reads existing artifacts. The demo CLI gets a `--report` flag. Existing tests and pipelines are unaffected.
