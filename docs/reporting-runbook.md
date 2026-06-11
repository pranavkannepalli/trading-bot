# Reporting Runbook
## Trading Bot — Backtesting, Options Surfaces, Regime Labels & Leakage-Safe Evaluation

**Target audience:** Strategy authors, reviewers, and anyone running backtests or reading evaluation reports.
**Last updated:** 2026-06-10

---

## 1. Overview

This runbook covers the end-to-end reporting pipeline for the non-agentic core boundary trading engine. After a backtest, there are two layers of output:

| Layer | File(s) | Generator | Purpose |
|-------|---------|-----------|---------|
| **Raw artifacts** | `artifacts/non_agentic_core_boundary_demo.json`, `artifacts/summary.json` | `demo_non_agentic_boundary.py` or `BacktestRunner` | Per-step audit trail; boundary proof |
| **Evaluation report** | `artifacts/evaluation_report.json` | `trading_bot/evaluation_report.py` (RFC, pending implementation per `docs/evaluation-report-spec.md`) | Aggregated metrics: Sharpe, Sortino, Calmar, drawdown, regime breakdown, counsel effectiveness |

This runbook focuses on what exists today, what commands produce it, where artifacts land, and how to avoid look-ahead leakage.

---

## 2. Quickstart: Generate Everything

```bash
# From the repo root:
cd /home/claw/.openclaw/repos/trading-bot

# 1. Run the full boundary demo (40 steps, strict delta counsel)
python3 trading_bot/demo_non_agentic_boundary.py \
  --out ./artifacts \
  --case counsel_strict_delta \
  --steps 40 \
  --quantity 10 \
  --core-max-abs-total-delta 1000 \
  --max-abs-total-delta-from-counsel 2.0

# 2. Inspect results
cat artifacts/summary.json
python3 -m json.tool artifacts/non_agentic_core_boundary_demo.json | head -120

# 3. Run tests (validates boundary enforcement, artifact writing, env correctness)
python3 -m unittest discover -s tests -p 'test_*.py'
```

---

## 3. The Backtesting Data Pipeline

### 3.1 Data Flow

```
Synthetic closes (sine-wave) ──┐
                               ▼
                    StaggeredInputEnv
                    (lagged windows,
                     configurable lag)
                               │
                    ┌──────────┼──────────┐
                    ▼          ▼          ▼
              Regime        Strategy    Greeks
              Detection     proposes    (Black-
              (bull/bear/   orders      Scholes
               range/                   delta/
               volatile)                vega/
                                        gamma/
                                        theta)
                    │          │          │
                    └──────────┼──────────┘
                               ▼
                        Core Boundary
                        (filter orders
                         by risk caps)
                               │
                    ┌──────────┼──────────┐
                    ▼                     ▼
              LLM Counsel          Execution Engine
              (optional,           (mock fills at
               numeric caps         close price)
               only)
                    │                     │
                    └──────────┼──────────┘
                               ▼
                        JSON Artifacts
                        (per-step audit)
```

### 3.2 Key Pipeline Components

| Component | Module | What It Produces |
|-----------|--------|-----------------|
| Price series | `_make_closes()` in demo | 100 + trend + sine wave, deterministic |
| Staggered env | `trading_bot/envs.py` | `StaggeredObservation` with `staggered_closes[i] = closes[t - i*lag_steps]` |
| Regime detection | `trading_bot/regime_detection.py` | `bull`, `bear`, `range`, or `volatile` per step |
| Options greeks | `trading_bot/option_pricing.py` | `BlackScholesGreeks` (price, delta, gamma, vega, theta) |
| Strategy proposals | `trading_bot/strategies.py` | `list[Order]` per step |
| Counsel caps | `trading_bot/llm_counsel.py` | `Advice` (numeric caps only) |
| Core filter | `demo_non_agentic_boundary.py` | `allowed: true/false` per order |
| Execution fills | `trading_bot/execution.py` | `list[Fill]` per step |

### 3.3 Environment "Stagger" — How Lag Works

The `StaggeredInputEnv` produces observations at configurable lag to simulate realistic data cadence:

```
Time:    t=0  t=1  t=2  ...  t=10  t=12  t=14  t=16  t=18  t=20
Close:   c0   c1   c2   ...  c10   c12   c14   c16   c18   c20

With window_size=5, lag_steps=2:
  obs at t=10: staggered_closes = [c10, c8, c6, c4, c2]
  obs at t=12: staggered_closes = [c12, c10, c8, c6, c4]
```

**This is the key leakage-safe property:** The observation at time `t` only contains closes at or before `t`. No future data leaks into the observation window.

---

## 4. Options Surfaces

### 4.1 How They're Used

The demo computes **per-step Black-Scholes greeks** for a single synthetic option contract:

```
Inputs per step:
  S = underlying price (from closes[t])
  K = fixed strike (default 100.0)
  T = remaining time (decays linearly: expiry_years * (1 - t / total_steps))
  r = risk-free rate (default 0.01)
  iv = implied vol, varies by regime:
       bull     → 0.20
       bear     → 0.30
       range    → 0.25 (base)
       volatile → 0.40
```

### 4.2 Where Greek Outputs Land

In `artifacts/non_agentic_core_boundary_demo.json`, each step contains:

```json
{
  "time_index": 10,
  "option": {
    "option_type": "call",
    "strike": 100.0,
    "premium": 2.34,
    "greeks": {
      "delta": 0.55,
      "gamma": 0.045,
      "vega": 12.30,
      "theta": -3.20
    }
  }
}
```

### 4.3 What the Surface Does NOT Include

- **No volatility smile/skew** — IV is flat per regime, no strike dependence
- **No term structure** — T decays linearly, no forward curve
- **No dividends** — Black-Scholes assumes European, no dividends
- **No early exercise** — American-style exercise not modeled
- **Single contract only** — one strike, one expiry per run

---

## 5. Regime Labels

### 5.1 Detector Logic

`SimpleTrendVolRegimeDetector` classifies each window of staggered closes into one of four labels:

```
1. Compute slope = (last_close / first_close) - 1
2. Compute vol = stddev(log returns) across the window
3. Classify:
   - vol >= 0.10          → "volatile"
   - |slope| <= 0.01      → "range"
   - slope > 0            → "bull"
   - otherwise            → "bear"
```

### 5.2 Thresholds (Hardcoded, Tunable)

| Threshold | Value | Effect |
|-----------|-------|--------|
| Volatility floor for "volatile" | 0.10 | Log-return stddev exceeds this → volatile |
| Slope floor for "range" | 0.01 | Near-zero trend → range |
| Window size | 5 (configurable) | Number of staggered closes used |

### 5.3 Where Regime Labels Appear

In artifacts, every step records its regime:

```json
{
  "time_index": 15,
  "regime": "bear",
  "implied_vol": 0.30
}
```

The evaluation report spec (RFC) adds per-regime performance breakdowns:

```json
{
  "regime": {
    "breakdown": {
      "bull": {"steps": 10, "total_pnl": 500.00, "win_rate_pct": 70.0, ...},
      "bear": {"steps": 8, "total_pnl": -200.00, ...}
    },
    "transitions": [
      {"from": "bull", "to": "bear", "step": 10, "pnl_impact": -80.00}
    ]
  }
}
```

---

## 6. Strategy Interface (For Reporting)

### 6.1 The Contract

```python
from trading_bot.strategies import Strategy
from trading_bot.types import Order

class MyStrategy(Strategy):
    @property
    def name(self) -> str:
        return "my_strategy_name"

    def propose_orders(self, observation: dict) -> list[Order]:
        # observation keys:
        #   "staggered_closes" — list[float], window of lagged closes
        #   "time_index"       — int, current step
        #   "regime"           — str, one of {bull, bear, range, volatile}
        ...
```

### 6.2 Strategy Output → Report Pipeline

1. Strategy's `propose_orders()` returns `list[Order]`
2. Core boundary computes greeks for each order, checks risk caps
3. Execution engine fills eligible orders at option premium
4. Artifacts capture: `proposed_orders`, `decisions` (risk + allowed), `fills`
5. Evaluation report (RFC) aggregates: trade P&L, win rate, per-regime stats

### 6.3 Adding a New Strategy For Reporting

```bash
# 1. Subclass Strategy in a new file or in the demo
# 2. Wire it into demo_non_agentic_boundary.py's main() or BacktestRunner
# 3. Run the backtest
python3 trading_bot/demo_non_agentic_boundary.py --out ./artifacts --steps 60
# 4. Compare artifacts across strategy runs
diff <(jq '.final' artifacts/summary.json) <(jq '.final' artifacts_prev/summary.json)
```

---

## 7. Leakage-Safe Evaluation

### 7.1 What "Leakage-Safe" Means

**Look-ahead bias** occurs when a model uses information at time `t` that was only available at time `t+k`. This inflates backtest performance and makes live results worse.

### 7.2 How This Engine Prevents Leakage

| Mechanism | Where | Guarantee |
|-----------|-------|-----------|
| Staggered windows | `StaggeredInputEnv` | `staggered_closes[i] = closes[t - i*lag_steps]` — only past data, no future |
| Point-in-time regime | `SimpleTrendVolRegimeDetector` | Regime label computed from `staggered_closes` only (no future closes) |
| Point-in-time greeks | `black_scholes_greeks()` | Greeks use `closes[t]` (current close), not future closes |
| No benchmark leakage | — | No benchmark is injected; user must provide their own comparison |
| Deterministic data | `_make_closes()` | Fixed sine-wave eliminates data-mining bias from live feeds |

### 7.3 Manual Leakage Checks

After a backtest run, verify these properties:

```bash
# 1. Verify no future data in observations
python3 -c "
import json
with open('artifacts/non_agentic_core_boundary_demo.json') as f:
    data = json.load(f)
# Check that time_index never exceeds the observation's last close index
# (StaggeredInputEnv guarantees this by construction)
print(f'Steps: {len(data[\"steps\"])}')
print(f'First time_index: {data[\"steps\"][0][\"time_index\"]}')
print(f'Last time_index:  {data[\"steps\"][-1][\"time_index\"]}')
"

# 2. Verify counsel caps are enforced (no order with delta > cap is filled)
python3 -c "
import json
with open('artifacts/non_agentic_core_boundary_demo.json') as f:
    data = json.load(f)
violations = 0
for step in data['steps']:
    for d in step['decisions']:
        cap = d['caps']['max_abs_total_delta']
        risk = d['risk']['delta_total_abs']
        if risk > cap and d['allowed']:
            violations += 1
            print(f'LEAK: step {step[\"time_index\"]} allowed order with delta {risk} > cap {cap}')
print(f'Total violations: {violations}')
"

# 3. Verify no free-form counsel output
python3 -c "
import json
with open('artifacts/non_agentic_core_boundary_demo.json') as f:
    data = json.load(f)
for step in data['steps']:
    advice = step.get('advice')
    if advice:
        # Advice must only contain numeric fields, never strings
        for k, v in advice.items():
            if v is not None and not isinstance(v, (int, float)):
                print(f'BOUNDARY BREACH: step {step[\"time_index\"]} advice.{k} = {v!r}')
"
```

### 7.4 Known Leakage Risks (What to Watch For)

| Risk | Status | Mitigation |
|------|--------|-----------|
| Strategy accessing raw `closes[i]` for i > t | Not possible | Strategy only receives `staggered_closes` from the env |
| Regime computed from full series | Not possible | Detector only receives the current window |
| Counsel using future market state | Not possible | Counsel receives `market_state` with current values only |
| Benchmark leakage | Null by default | No benchmark is configured; user must supply their own |
| Multi-run data mining | User responsibility | Run on out-of-sample data; this engine is deterministic for reproducibility |

---

## 8. Artifact Locations & Formats

### 8.1 Files Written

| File | Format | Generated By | Gitignored? |
|------|--------|-------------|-------------|
| `artifacts/non_agentic_core_boundary_demo.json` | JSON array of step objects | `demo_non_agentic_boundary.py` | Yes |
| `artifacts/summary.json` | JSON (case, steps, fills_count, total_pnl) | `demo_non_agentic_boundary.py` | Yes |
| `artifacts/evaluation_report.json` | JSON (per spec) | `trading_bot/evaluation_report.py` (pending) | Yes |

### 8.2 Inspecting Artifacts

```bash
# Summary stats
cat artifacts/summary.json

# Per-step audit trail (first 2 steps)
python3 -c "
import json
with open('artifacts/non_agentic_core_boundary_demo.json') as f:
    data = json.load(f)
for step in data['steps'][:2]:
    print(json.dumps(step, indent=2))
"

# Count allowed vs rejected orders
python3 -c "
import json
with open('artifacts/non_agentic_core_boundary_demo.json') as f:
    data = json.load(f)
allowed = sum(1 for s in data['steps'] for d in s['decisions'] if d['allowed'])
rejected = sum(1 for s in data['steps'] for d in s['decisions'] if not d['allowed'])
print(f'Allowed: {allowed}, Rejected: {rejected}')
"
```

### 8.3 Artifact Retention Warning

**Each run overwrites `artifacts/`.** Save interesting outputs before re-running:

```bash
cp -r artifacts artifacts_$(date +%Y%m%d_%H%M%S)
```

---

## 9. CLI Reference (Reporting-Focused)

### 9.1 Demo Runner

```bash
python3 trading_bot/demo_non_agentic_boundary.py \
  --out ./artifacts \              # Required: output directory
  --case default \                 # "default" or "counsel_strict_delta"
  --steps 60 \                     # Number of environment iterations
  --quantity 10.0 \                # Contract quantity
  --core-max-abs-total-delta 1000 \  # Core hard cap on delta
  --core-max-abs-total-vega 1000000000 \  # Core hard cap on vega
  --max-abs-total-delta-from-counsel 2.0  # Counsel delta cap
```

### 9.2 BacktestRunner (Simpler, Spot-Focused)

```python
# In Python (not CLI — use from a script or test)
from trading_bot.backtest import BacktestRunner
from trading_bot.strategies import Strategy
from trading_bot.types import Order

class NaiveBuyHold(Strategy):
    name = "naive_buy_hold"
    def propose_orders(self, obs):
        return [Order(symbol="MOCK", side="buy", quantity=1.0)]

runner = BacktestRunner()
result = runner.run(strategy=NaiveBuyHold(), closes=[100, 101, 102, 103, 104])
print(f"Fills: {len(result.fills)}, PnL: {result.total_pnl}")
```

### 9.3 Test Suite

```bash
# All tests
python3 -m unittest discover -s tests -p 'test_*.py'

# Specific test
python3 -m unittest tests/test_non_agentic_core_boundary_demo.py -v
```

---

## 10. Evaluation Report Spec (RFC)

The evaluation report schema is defined in [`docs/evaluation-report-spec.md`](evaluation-report-spec.md). Key highlights:

- **9 sections:** meta, summary, time_series, performance, risk, regime, counsel, trades, warnings
- **Metrics:** Sharpe, Sortino, Calmar, Omega, SQN, K-Ratio, VaR/CVaR, profit factor, expectancy
- **Implementation status:** RFC completed, `trading_bot/evaluation_report.py` is **pending implementation**
- **Zero external deps:** Python stdlib only (math, json, statistics, uuid, datetime)

### What's available now vs. what the spec adds

| Metric | Available in Raw Artifacts? | In Evaluation Spec? |
|--------|---------------------------|---------------------|
| Total P&L | ✅ `summary.json` | ✅ |
| Per-step fills | ✅ `demo.json` | ✅ (aggregated) |
| Greeks per step | ✅ `demo.json` | ✅ (snapshot + exceed counts) |
| Regime per step | ✅ `demo.json` | ✅ (breakdown + transitions) |
| Counsel rejections | ✅ `demo.json` (decisions) | ✅ (effectiveness metrics) |
| Sharpe / Sortino / Calmar | ❌ (compute manually) | ✅ |
| VaR / CVaR | ❌ | ✅ |
| Max drawdown + duration | ❌ (compute manually) | ✅ |
| SQN / K-Ratio / Omega | ❌ | ✅ |
| Profit factor / expectancy | ❌ (compute manually) | ✅ |

---

## 11. Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `ValueError: closes series too short` | `--steps` too small for `window_size * lag_steps` | Increase `--steps` or reduce window/lag (hardcoded at 5/2 in demo) |
| `ModuleNotFoundError: trading_bot` | Running from wrong directory | `cd` to repo root first |
| `PermissionError` on artifacts | Output dir not writable | Ensure `--out` directory exists or let `os.makedirs` create it |
| Artifacts missing from last run | Overwritten by new run | Save artifacts before re-running (see §8.3) |
| Annualized metrics are `null` in evaluation report | Fewer than 20 steps | Use `--steps 252` or more for reliable annualization |
| `ModuleNotFoundError: types` (stdlib shadow) | Running directly from `trading_bot/` subdir | Run from repo root; the demo script auto-fixes `sys.path` |

---

## 12. Manual-Only Callouts

| Item | Why Manual | Verification |
|------|-----------|-------------|
| **Boundary proof inspection** | No automated pass/fail — human must verify that counsel caps are enforced and no free-form actions leak through | See §7.3 leakage checks |
| **Artifact comparison across runs** | No built-in diff tool — save artifacts before overwriting | `cp -r artifacts artifacts_$(date +%Y%m%d_%H%M%S)` |
| **Regime threshold tuning** | Thresholds (vol ≥ 0.10, slope ≤ 0.01) are hardcoded heuristics — adjust per strategy/asset | Edit `SimpleTrendVolRegimeDetector` thresholds |
| **Benchmark selection** | No benchmark is configured — user must provide their own for excess return calculation | Add benchmark to eval report config |
| **Evaluation report implementation** | The spec exists but `evaluation_report.py` is not yet implemented — metrics like Sharpe, Sortino, VaR must be computed manually for now | See `docs/evaluation-report-spec.md` for formulas |
| **Single-asset, single-leg only** | No multi-asset portfolio or option combo reporting — verify that your use case fits before running | Read §2 of the evaluation spec |

---

## 13. Quick Reference Card

```
┌─────────────────────────────────────────────────────────────┐
│  TRADING BOT — REPORTING QUICK REFERENCE                    │
├─────────────────────────────────────────────────────────────┤
│  Repo:    ~/.openclaw/repos/trading-bot                     │
│  Python:  3.11+ (stdlib only, zero deps)                    │
│                                                             │
│  RUN DEMO:                                                  │
│    python3 trading_bot/demo_non_agentic_boundary.py \       │
│      --out ./artifacts --case counsel_strict_delta \        │
│      --steps 40                                             │
│                                                             │
│  RUN TESTS:                                                 │
│    python3 -m unittest discover -s tests -p 'test_*.py'     │
│                                                             │
│  INSPECT:                                                   │
│    cat artifacts/summary.json                               │
│    python3 -m json.tool artifacts/*.json | head             │
│                                                             │
│  LEAKAGE CHECK:                                             │
│    See §7.3 for manual verification commands                │
│                                                             │
│  KEY FILES:                                                 │
│    README.md                  — architecture + quickstart   │
│    docs/reporting-runbook.md  — THIS FILE                   │
│    docs/evaluation-report-spec.md — RFC report schema       │
│    trading_bot/demo_non_agentic_boundary.py — CLI entry     │
│    trading_bot/backtest.py    — simpler spot backtester     │
└─────────────────────────────────────────────────────────────┘
```
