# Evaluation Report Specification
## Trading Bot (LLM Counsel) — Non-Agentic Core Boundary

**Status:** RFC Completed  
**Author:** nerd (research sweep)  
**Date:** 2026-06-10  
**Last Updated:** 2026-06-10 (added Omega Ratio, SQN, K-Ratio calculation references)  
**Scope:** Independent non-agentic trading engine with regime detection, options risk/greeks, backtesting, and optional LLM counsel boundaries.

---

## 1. Purpose

The evaluation report is the primary output artifact after a backtest run. It synthesizes performance, risk, regime, and counsel-effectiveness data into a machine-readable JSON document and a human-readable summary. The report enables:

- **Strategy comparison** — rank proposals by risk-adjusted returns
- **Regime attribution** — understand which market conditions the strategy thrives or fails in
- **Counsel boundary validation** — prove the non-agentic core is enforcing risk caps correctly
- **Audit trail** — every fill, greek snapshot, and counsel decision is traceable

## 2. Assumptions

1. **Single underlying, single-leg options.** The current engine operates on one symbol with synthetic European call/put options. No spreads, combos, or multi-asset portfolios.
2. **Black-Scholes greeks only.** Delta, gamma, vega, theta computed via textbook BS with synthetic IV. No market-observed surfaces, no dividends, no early exercise.
3. **Mock execution.** `BacktestExecutionEngine` fills at the market close price. No slippage, no liquidity constraints, no partial fills.
4. **Deterministic regime labels.** `SimpleTrendVolRegimeDetector` classifies each window as `bull`, `bear`, `range`, or `volatile` using only closing prices.
5. **Counsel is a numeric cap contract.** The LLM counsel adapter returns an `Advice` dataclass with optional numeric fields (`max_abs_total_delta`, `max_abs_total_vega`, `max_order_quantity`). There is no free-form text or tool call in the counsel output.
6. **No live credentials or real market data.** All inputs are synthetic/deterministic. The report spec must not assume live feeds, broker APIs, or real-money positions.
7. **Mark-to-market at last close.** Unrealized P&L uses the final closing price. No multi-path discounting or counterparty risk modeling.
8. **Zero external dependencies.** The report generator must use only Python stdlib (consistent with the rest of the codebase).

## 3. Report Structure

The evaluation report is a single JSON document with the following top-level sections:

```
{
  "meta":           {...},   // Run metadata
  "summary":        {...},   // One-page summary stats
  "time_series":    {...},   // Per-step time series
  "performance":    {...},   // Aggregate performance metrics
  "risk":           {...},   // Greek exposure + risk metrics
  "regime":         {...},   // Per-regime breakdown
  "counsel":        {...},   // Counsel boundary analysis
  "trades":         {...},   // Trade-level detail
  "warnings":       [...]    // Data quality / boundary issues
}
```

## 4. JSON Schema

### 4.1 `meta` — Run Metadata

```json
{
  "meta": {
    "report_version": "1.0.0",
    "generated_at": "2026-06-10T23:00:00Z",
    "run_id": "uuid-v4",
    "strategy_name": "NaiveMeanReversion",
    "config": {
      "case": "counsel_strict_delta",
      "steps": 40,
      "window_size": 5,
      "lag_steps": 2,
      "counsel_enabled": true,
      "counsel_max_abs_total_delta": 2.0,
      "counsel_max_abs_total_vega": null,
      "counsel_max_order_quantity": null
    },
    "symbol": "SYNTH",
    "option_type": "call",
    "strike": 100.0,
    "risk_free_rate": 0.02,
    "implied_volatility": 0.25,
    "time_to_expiry_years": 0.25,
    "data_source": "synthetic_sine_wave",
    "start_step": 0,
    "end_step": 39
  }
}
```

### 4.2 `summary` — One-Page Dashboard

```json
{
  "summary": {
    "total_pnl": 1234.56,
    "total_pnl_pct": 12.35,
    "total_return_pct": 12.35,
    "annualized_return_pct": null,
    "sharpe_ratio": 1.85,
    "sortino_ratio": 2.40,
    "calmar_ratio": 2.10,
    "max_drawdown_pct": -5.88,
    "max_drawdown_duration_steps": 8,
    "profit_factor": 2.15,
    "win_rate_pct": 62.5,
    "total_trades": 24,
    "winning_trades": 15,
    "losing_trades": 9,
    "avg_win": 150.20,
    "avg_loss": -75.30,
    "avg_win_loss_ratio": 1.99,
    "expectancy": 28.45,
    "benchmark_return_pct": null,
    "excess_return_pct": null,
    "regime_summary": {
      "bull": {"steps": 10, "pnl": 500.00},
      "bear": {"steps": 8, "pnl": -200.00},
      "range": {"steps": 14, "pnl": 850.56},
      "volatile": {"steps": 7, "pnl": 84.00}
    }
  }
}
```

**Field definitions:**

| Field | Formula / Source |
|---|---|
| `total_pnl` | `cash + position * final_close` (already in `BacktestResult`) |
| `total_pnl_pct` | `total_pnl / initial_capital * 100` |
| `annualized_return_pct` | `((1 + total_return)^(252/n_steps) - 1) * 100` (null if < 20 steps) |
| `sharpe_ratio` | `mean(step_returns - rf_step) / std(step_returns) * sqrt(steps_per_year)` |
| `sortino_ratio` | Same as Sharpe but std computed over negative returns only |
| `calmar_ratio` | `annualized_return / abs(max_drawdown_pct / 100)` |
| `max_drawdown_pct` | Largest peak-to-trough decline in equity curve (%) |
| `max_drawdown_duration_steps` | Steps from peak to recovery (or end of run) |
| `profit_factor` | `sum(gross_profits) / sum(abs(gross_losses))` |
| `win_rate_pct` | `winning_trades / total_trades * 100` |
| `expectancy` | `(win_rate * avg_win) - ((1-win_rate) * avg_loss)` |
| `benchmark_return_pct` | Return of a buy-and-hold benchmark over the same period (null if not configured) |

### 4.3 `time_series` — Per-Step Snapshots

```json
{
  "time_series": {
    "steps": 40,
    "series": {
      "equity":        [10000.00, 10012.50, 10008.30, ...],
      "returns_pct":   [0.125, -0.042, 0.310, ...],
      "drawdown_pct":  [0.0, 0.0, -0.042, ...],
      "position":      [0.0, 1.0, 1.0, ...],
      "cash":          [10000.00, 9987.50, 9987.50, ...],
      "regime":        ["range", "range", "bull", ...],
      "counsel_active":[true, true, false, ...],
      "orders_proposed":[2, 1, 3, ...],
      "orders_filled": [1, 1, 2, ...]
    }
  }
}
```

### 4.4 `performance` — Aggregate Performance Detail

```json
{
  "performance": {
    "returns": {
      "total_return_pct": 12.35,
      "annualized_return_pct": null,
      "benchmark_return_pct": null,
      "excess_return_pct": null,
      "best_step_pct": 2.45,
      "worst_step_pct": -1.80,
      "avg_step_return_pct": 0.31,
      "std_step_return_pct": 0.85,
      "positive_step_pct": 58.0
    },
    "ratios": {
      "sharpe_ratio": 1.85,
      "sortino_ratio": 2.40,
      "calmar_ratio": 2.10,
      "information_ratio": null,
      "omega_ratio": 1.50,
      "sqn": 3.20,
      "k_ratio": 2.15
    },
    "trades_summary": {
      "total_trades": 24,
      "winning_trades": 15,
      "losing_trades": 9,
      "win_rate_pct": 62.5,
      "profit_factor": 2.15,
      "avg_holding_steps": 3.2,
      "max_consecutive_wins": 6,
      "max_consecutive_losses": 3
    },
    "distribution": {
      "pnl_skew": 0.45,
      "pnl_kurtosis": 2.80
    }
  }
}
```

### 4.5 `risk` — Greek Exposure & Risk Metrics

```json
{
  "risk": {
    "greeks_snapshot": {
      "max_abs_delta": 1.85,
      "max_abs_gamma": 0.045,
      "max_abs_vega": 12.30,
      "max_abs_theta": -3.20,
      "avg_abs_delta": 0.92,
      "avg_abs_vega": 6.15,
      "delta_exceeded_counsel_cap_steps": 3,
      "vega_exceeded_counsel_cap_steps": 0
    },
    "var": {
      "historical_var_95_pct": -420.50,
      "historical_var_99_pct": -680.00,
      "cvar_95_pct": -550.30,
      "max_single_step_loss": -180.00
    },
    "drawdown": {
      "max_drawdown_pct": -5.88,
      "max_drawdown_absolute": -588.00,
      "max_drawdown_duration_steps": 8,
      "drawdown_start_step": 12,
      "drawdown_end_step": 19,
      "avg_drawdown_pct": -1.20,
      "drawdown_count": 4
    },
    "exposure": {
      "max_leverage": 1.5,
      "avg_leverage": 0.75,
      "time_in_market_pct": 65.0
    }
  }
}
```

### 4.6 `regime` — Per-Regime Performance

```json
{
  "regime": {
    "detector": "SimpleTrendVolRegimeDetector",
    "breakdown": {
      "bull": {
        "steps": 10,
        "step_pct": 25.0,
        "total_pnl": 500.00,
        "win_rate_pct": 70.0,
        "avg_step_return_pct": 0.50,
        "max_drawdown_in_regime_pct": -2.0,
        "trades": 7,
        "sharpe_in_regime": 2.20
      },
      "bear": {
        "steps": 8,
        "step_pct": 20.0,
        "total_pnl": -200.00,
        "win_rate_pct": 40.0,
        "avg_step_return_pct": -0.25,
        "max_drawdown_in_regime_pct": -5.88,
        "trades": 5,
        "sharpe_in_regime": -1.10
      },
      "range": {
        "steps": 14,
        "step_pct": 35.0,
        "total_pnl": 850.56,
        "win_rate_pct": 77.8,
        "avg_step_return_pct": 0.61,
        "max_drawdown_in_regime_pct": -0.5,
        "trades": 9,
        "sharpe_in_regime": 3.40
      },
      "volatile": {
        "steps": 7,
        "step_pct": 17.5,
        "total_pnl": 84.00,
        "win_rate_pct": 50.0,
        "avg_step_return_pct": 0.12,
        "max_drawdown_in_regime_pct": -3.0,
        "trades": 3,
        "sharpe_in_regime": 0.30
      }
    },
    "transitions": {
      "count": 5,
      "details": [
        {"from": "bull", "to": "bear", "step": 10, "pnl_impact": -80.00},
        {"from": "bear", "to": "range", "step": 18, "pnl_impact": 45.00}
      ]
    }
  }
}
```

### 4.7 `counsel` — Counsel Boundary Analysis

```json
{
  "counsel": {
    "enabled": true,
    "adapter": "StrictDeltaCounsel",
    "caps_applied": {
      "max_abs_total_delta": 2.0,
      "max_abs_total_vega": null,
      "max_order_quantity": null
    },
    "effectiveness": {
      "total_orders_proposed": 60,
      "total_orders_accepted": 45,
      "total_orders_rejected": 15,
      "rejection_rate_pct": 25.0,
      "steps_with_rejections": 8,
      "cap_binding_events": 12,
      "cap_breach_events": 0
    },
    "comparison": {
      "with_counsel": {"total_pnl": 1234.56, "sharpe_ratio": 1.85, "max_drawdown_pct": -5.88},
      "without_counsel": null
    },
    "rejection_reasons": {
      "delta_cap": 15,
      "vega_cap": 0,
      "quantity_cap": 0
    }
  }
}
```

### 4.8 `trades` — Per-Trade Detail

```json
{
  "trades": {
    "count": 24,
    "entries": [
      {
        "trade_id": 1,
        "symbol": "SYNTH",
        "side": "buy",
        "quantity": 1.0,
        "entry_step": 5,
        "entry_price": 100.50,
        "exit_step": 10,
        "exit_price": 102.00,
        "pnl": 1.50,
        "pnl_pct": 1.49,
        "holding_steps": 5,
        "regime_at_entry": "bull",
        "regime_at_exit": "bull",
        "entry_delta": 0.55,
        "exit_delta": 0.72,
        "rejected_by_counsel": false
      }
    ]
  }
}
```

### 4.9 `warnings` — Data Quality Flags

```json
{
  "warnings": [
    {
      "level": "WARN",
      "message": "Only 40 steps; annualized metrics may be unreliable.",
      "field": "summary.annualized_return_pct"
    },
    {
      "level": "INFO",
      "message": "No benchmark configured; excess return metrics are null.",
      "field": "summary.benchmark_return_pct"
    }
  ]
}
```

**Warning levels:** `INFO`, `WARN`, `ERROR`

**Standard warnings:**
- Fewer than 252 steps → annualized metrics unreliable
- Fewer than 20 trades → Sharpe/Sortino unreliable
- No benchmark configured
- Counsel disabled → comparison section empty
- Zero losing trades → profit_factor is infinite (clamped to a sentinel)
- Single regime only → regime breakdown degenerate

## 5. Calculation Reference

### 5.1 Equity Curve
```
equity[t] = cash[t] + position[t] * close[t]
```
Where `cash[t]` and `position[t]` are cumulative after all fills at step `t`.

### 5.2 Step Returns
```
step_return[t] = (equity[t] / equity[t-1]) - 1
```

### 5.3 Sharpe Ratio
```
excess = step_returns - risk_free_rate_per_step
sharpe = mean(excess) / std(excess) * sqrt(steps_per_year)
```
Default `steps_per_year = 252`. Default `risk_free_rate = 0.02` annual → `0.02/252` per step.

### 5.4 Sortino Ratio
```
downside = [r for r in excess if r < 0]
sortino = mean(excess) / std(downside) * sqrt(steps_per_year)
```
If no downside returns, set to `null`.

### 5.5 Max Drawdown
```
peak = equity[0]
for each t:
    peak = max(peak, equity[t])
    dd[t] = (equity[t] / peak) - 1
mdd = min(dd)
```

### 5.6 Profit Factor
```
pf = sum(gross_profits) / sum(abs(gross_losses))
```
If no losses, set to `null` (not infinity).

### 5.7 Historical VaR
- Sort step returns ascending.
- VaR 95% = return at the 5th percentile.
- CVaR 95% = mean of all returns below the VaR threshold.

### 5.8 Calmar Ratio
```
calmar = annualized_return / abs(mdd_pct / 100)
```

### 5.9 Expectancy
```
expectancy = (win_rate * avg_win) - ((1-win_rate) * avg_loss)
```

### 5.10 Omega Ratio
```
omg = integral(L to inf, 1-F(x))dx / integral(-inf to L, F(x))dx
```
Where `L` is the threshold return (default: risk-free rate per step) and `F(x)` is the cumulative distribution of step returns.
Practically computed as:
```
omg = sum(max(r - L, 0) for r in returns) / sum(max(L - r, 0) for r in returns)
```
An Omega Ratio > 1 indicates the strategy's gains outweigh its losses relative to the threshold. Values below 1 suggest the strategy underperforms the threshold.

### 5.11 SQN (System Quality Number — Van Tharp, 2008)
```
R = [trade_pnl / abs(trade_entry_cost) for trade in trades]
sqn = sqrt(N) * mean(R) / std(R)
```
Where `N` is the number of trades and `R` is the R-multiple per trade. This metric combines expectancy, consistency, and opportunity frequency into a single number.

**Interpretation:**
| SQN | Rating |
|-----|--------|
| < 1.0 | Poor |
| 1.0–1.9 | Below average |
| 2.0–2.9 | Average |
| 3.0–4.9 | Good |
| 5.0–6.9 | Excellent |
| > 7.0 | Holy Grail (suspicious) |

**Note:** SQN is included as an optional metric in `performance.ratios` under the key `sqn`. It is `null` when fewer than 10 trades exist (low statistical significance).

### 5.12 K-Ratio (Lars Kestner, 1996)
```
k_ratio = slope(equity_curve) / (standard_error_of_slope * sqrt(N))
```
Where `slope` is the linear regression slope of the equity curve against time steps, and `standard_error_of_slope` is the regression's standard error. The K-Ratio measures consistency of returns: a higher value indicates a smoother, more reliable equity curve.

**Interpretation:** K-Ratio > 2.0 is considered strong consistency. Values near 0 indicate erratic performance. This metric is optional under `performance.ratios.k_ratio`.

## 6. Implementation Notes

### 6.1 Python Module
The evaluation report generator should live at `trading_bot/evaluation_report.py` with:
- `class EvaluationReportGenerator` — takes `BacktestResult` + step-level data and produces the report dict
- `def generate_report(...) -> dict` — main entry point
- `def write_report(report: dict, path: str) -> None` — serialize to JSON

### 6.2 Dependencies
Zero external packages. Use only `math`, `json`, `dataclasses`, `statistics`, `uuid`, `datetime`.

### 6.3 Integration Point
The report should be callable from `BacktestRunner.run()` or as a post-processing step. The runner must capture per-step data (equity, greeks, regime, counsel decisions) that current `BacktestResult` does not store.

### 6.4 What This Spec Does NOT Cover
- Multi-asset / portfolio-level reporting
- Live trading P&L reconciliation
- Options combo (spreads, straddles, etc.) attribution
- Real market data ingestion
- Visualization/charting (the report is JSON; charts are a separate concern)
- HTML/PDF rendering (the JSON is the source of truth; rendering is downstream)

## 7. Schema Versioning

The `meta.report_version` follows SemVer:
- **Major:** Breaking schema changes (field removal, type change)
- **Minor:** New optional fields added
- **Patch:** Documentation fixes, warning message changes

Consumers should reject reports with an incompatible major version.

---

## References

- QuantConnect Backtest Results: https://www.quantconnect.com/docs/v2/cloud-platform/backtesting/results
- LuxAlgo "Top 7 Metrics for Backtesting Results" (Jul 2025): https://www.luxalgo.com/blog/top-7-metrics-for-backtesting-results/
- Investopedia Sharpe Ratio: https://www.investopedia.com/terms/s/sharperatio.asp
- Global Investment Performance Standards (GIPS): https://www.gipsstandards.org/
- Black-Scholes (1973), "The Pricing of Options and Corporate Liabilities"
