# Reporting Runbook — Trading Council (Non-Agentic Core Boundary)

A companion document to `README.md` focused on **reporting**: how to produce, interpret, and audit backtest artifacts with the trading-bot's non-agentic core boundary.

---

## Quick Start

### Run a backtest and produce report artifacts

```bash
# From repo root: /home/claw/.openclaw/repos/trading-bot
python3 trading_bot/demo_non_agentic_boundary.py \
  --out ./artifacts \
  --case counsel_strict_delta \
  --steps 60 \
  --quantity 10 \
  --core-max-abs-total-delta 1000 \
  --max-abs-total-delta-from-counsel 2.0
```

### Run tests to validate the pipeline

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

### View the summary

```bash
cat artifacts/summary.json | python3 -m json.tool
```

---

## 1. Backtesting Data Pipeline

### Overview

The pipeline is **end-to-end deterministic**: produce a price series → run the environment → get artifacts. No network calls, no randomness, no live data.

### Components

| Component | Module | Source |
|-----------|--------|--------|
| **Price Series** | `demo_non_agentic_boundary.py::_make_closes()` | Deterministic trend + sine wave |
| **Environment** | `envs.py::StaggeredInputEnv` | Staggered historical windows |
| **Regime Detection** | `regime_detection.py::SimpleTrendVolRegimeDetector` | Slope + volatility heuristic |
| **Strategy** | `strategies.py::Strategy` (ABC) → `demo_non_agentic_boundary.py::RegimeAwareOptionStrategy` | Regime → option type |
| **Option Pricing** | `option_pricing.py::black_scholes_greeks()` | Black-Scholes (price, delta, gamma, vega, theta) |
| **LLM Counsel** | `llm_counsel.py::LLMCounsel` (ABC) → `demo_non_agentic_boundary.py::StrictDeltaCounsel` | Risk cap provider |
| **Core Boundary** | `demo_non_agentic_boundary.py::NonAgenticOptionsBacktestRunner.run()` | Risk filter loop |
| **Execution** | `execution.py::BacktestExecutionEngine` | Fill at market close |
| **Artifact Writing** | `demo_non_agentic_boundary.py::main()` | JSON output |

### Data Flow Diagram

```
_make_closes(N)          ← deterministic mock prices
       │
       ▼
StaggeredInputEnv        ← window_size=5, lag_steps=2
  └─ StaggeredObservation (time_index, staggered_closes)
       │
       ├──► SimpleTrendVolRegimeDetector.detect(window) → "bull"|"bear"|"range"|"volatile"
       │
       ├──► black_scholes_greeks(S, K, T, r, iv, opt_type) → Greeks
       │
       ├──► RegimeAwareOptionStrategy.propose_orders(obs) → [Order]
       │
       ├──► [Optional] StrictDeltaCounsel.advise(...) → Advice(caps)
       │
       ├──► Core Boundary Filter:
       │       for each order:
       │         risk = abs(greeks.delta * qty), abs(greeks.vega * qty)
       │         allowed = risk.delta ≤ cap_delta AND risk.vega ≤ cap_vega
       │
       ├──► BacktestExecutionEngine.execute(allowed_orders, {close: premium}) → [Fill]
       │
       └──► Cash/Position P&L tracking → artifacts dict
```

### Key Pipeline Assumptions

1. **Price series is mock:** `_make_closes()` produces a deterministic series with mild trend + sine components. No real market data.
2. **Window-based observations:** The env produces `staggered_closes` using past prices at `lag_steps` intervals — never future data.
3. **Start offset:** The env starts at `t = window_size * lag_steps` (default: `t=10`). The first 10 prices are "burn in."
4. **Linear time decay:** `remaining_T = expiry_years * (1 - t/N)`. Real options use actual calendar days.
5. **Regime-implied vol:** Each regime maps to a fixed IV: `bull=0.20, bear=0.30, range=0.25, volatile=0.40`.
6. **Single contract per step:** One option order per iteration. No multi-leg, no spreads, no hedging.

---

## 2. Options Surfaces

### How options are modeled

The demo uses a **single option contract** with fixed parameters, changing only the option type (`call`/`put`) per regime:

```
MOCK__{call|put}__K{strike}__T{expiry_years}
```

Example: `MOCK__put__K100.0__T0.25` = Mock underlying, put option, strike $100, 0.25 years to expiry.

### Greeks reported

Every step in the artifact includes:

| Greek | Formula | Unit |
|-------|---------|------|
| **delta** | ∂V/∂S | $ change per $1 underlying move |
| **gamma** | ∂²V/∂S² | Change in delta per $1 underlying move |
| **vega** | ∂V/∂σ | $ change per 1% IV change |
| **theta** | ∂V/∂t | $ change per year of time decay |

### Reading the options surface in artifacts

```bash
# Extract every step's greeks into a quick table
cat artifacts/non_agentic_core_boundary_demo.json \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('step,regime,opt_type,underlying,premium,delta,gamma,vega,theta,remaining_T')
for s in d['steps']:
    o = s['option']
    g = o['greeks']
    print(f\"{s['time_index']},{s['regime']},{o['option_type']},{s['underlying_close']:.2f},{o['premium']:.4f},{g['delta']:.4f},{g['gamma']:.4f},{g['vega']:.2f},{g['theta']:.2f},{s['remaining_T']:.4f}\")
"
```

### Implied volatility surface (regime-based)

```
Regime      IV    Strategy Action
──────     ────   ───────────────
bull       0.20   Buy calls
bear       0.30   Buy puts
range      0.25   Buy calls
volatile   0.40   Buy puts
```

The IV skew (volatile-regime puts commanding 0.40 IV vs bull-regime calls at 0.20) creates a realistic premium differential that affects P&L.

---

## 3. Regime Labels

### Detection algorithm

`SimpleTrendVolRegimeDetector` uses **two signals** on a rolling window of closes:

```
SLOPE = (last/first) - 1       ← trend direction & magnitude
VOL   = stdev(log returns)     ← volatility proxy
```

### Classification rules

```
if VOL >= 0.10           → "volatile"
elif |SLOPE| <= 0.01     → "range"      (flat / sideways)
elif SLOPE > 0           → "bull"        (uptrend)
else                     → "bear"        (downtrend)
```

### How regimes drive strategy

```python
# From RegimeAwareOptionStrategy.propose_orders()
if regime in ("bear", "volatile"):
    opt_type = "put"
else:
    opt_type = "call"  # bull, range
```

### Where regime labels appear in artifacts

Each step in `non_agentic_core_boundary_demo.json` records:

```json
{
  "time_index": 15,
  "regime": "bear",
  "implied_vol": 0.30,
  ...
}
```

### Caveats for regime labels

- **Toy detector only:** The heuristics are threshold-based and not robust to different price series.
- **Window dependency:** The regime label at step `t` uses only `window_size` (5) historical closes — short memory.
- **No regime transitions analysis:** The artifacts contain per-step labels but no transition matrix, regime duration, or persistence statistics. These can be computed post-hoc from the artifact JSON.

---

## 4. Strategy Interfaces

### Strategy ABC

```python
class Strategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def propose_orders(self, observation: Any) -> list[Order]:
        raise NotImplementedError
```

The strategy **only proposes orders**. It never executes, never manages risk, never touches the core.

### Observation contract

The demo passes a dict to strategies:

```python
{
    "staggered_closes": [108.7, 107.2, 105.1, 103.8, 102.4],  # window_size=5
    "time_index": 15,
    "regime": "bear"
}
```

In the generic `BacktestRunner` (non-option path via `backtest.py`), observations are raw `StaggeredObservation` objects with `.time_index` and `.staggered_closes`.

### Order contract

```python
@dataclass(frozen=True)
class Order:
    symbol: str          # e.g., "MOCK__put__K100.0__T0.25"
    side: Side           # "buy" | "sell"
    quantity: float      # Contract count
    limit_price: Optional[float] = None  # None = market order
```

### Wiring a new strategy

```python
class MyCustomStrategy(Strategy):
    @property
    def name(self) -> str:
        return "my_custom"

    def propose_orders(self, observation):
        # Access regime, closes, etc.
        regime = observation["regime"]
        closes = observation["staggered_closes"]
        # ... your logic ...
        return [Order(symbol="MOCK__call__K110.0__T0.25", side="buy", quantity=5.0)]
```

Then reference it in the runner. If using the generic `BacktestRunner`, your observation comes as `StaggeredObservation`.

### LLM Counsel interface

```python
class LLMCounsel(ABC):
    @abstractmethod
    def advise(self, *, proposed_orders: Iterable[Order], market_state: Any) -> Advice:
        raise NotImplementedError
```

**Safety constraint:** The counsel returns `Advice` with risk caps only. It can **never** place orders, pick symbols, or override execution.

```python
@dataclass(frozen=True)
class Advice:
    max_order_quantity: Optional[float] = None
    max_abs_total_delta: Optional[float] = None
    max_abs_total_vega: Optional[float] = None
```

---

## 5. Leakage-Safe Evaluation

### What makes it leakage-safe

The `StaggeredInputEnv` is designed to prevent **look-ahead bias**:

1. **Observation at time `t`** uses closes at indices `[t, t-lag, t-2*lag, ..., t-(window-1)*lag]` — all in the **past**.
2. **Execution at time `t`** fills at `closes[t]` (the "market close" for that step).
3. **The env never exposes `closes[t+1]` or beyond.**

### The lag_steps parameter

```python
env = StaggeredInputEnv(closes, window_size=5, lag_steps=2)
# t=10 → staggered_closes = [closes[10], closes[8], closes[6], closes[4], closes[2]]
```

With `lag_steps=2`, the observation skips every other close. This simulates **signal cadence mismatch**: your strategy's indicators might update at a different frequency than prices.

### How to audit for leakage

```bash
# Verify that no step references a future price
python3 -c "
import json
with open('artifacts/non_agentic_core_boundary_demo.json') as f:
    d = json.load(f)
max_t = max(s['time_index'] for s in d['steps'])
for s in d['steps']:
    t = s['time_index']
    if t > max_t:
        print(f'LEAKAGE: step {t} beyond max {max_t}')
print(f'Safe: {len(d[\"steps\"])} steps, max time_index={max_t}')
"
```

### What is NOT leakage-safe (by design)

- The **demo** uses a pre-computed full `closes` list. In production, you'd stream these from a live source. The env pattern (`self._closes` is the full list) trusts the caller. For a streaming backtest, replace `_make_closes()` with a generator/iterator source.
- **Regime detection** at time `t` uses only `staggered_closes` (past data) — verified leakage-safe.
- **Option pricing** at time `t` uses `underlying_price = closes[t]` (current price) — standard for mark-to-market at close.

### The burn-in period

The env starts at `t = window_size * lag_steps`. All earlier prices are "burn in" — they exist to build the first observation window but no strategy decisions are made on them.

---

## Artifacts: Complete Reference

### Location

```
artifacts/
├── non_agentic_core_boundary_demo.json   ← Full per-step trace
└── summary.json                          ← Condensed output
```

**Important:** `artifacts/` is **gitignored**. Artifacts should never be committed. They are transient and reproducible.

### `non_agentic_core_boundary_demo.json` structure

```json
{
  "meta": {
    "window_size": 5,
    "lag_steps": 2,
    "strike": 100.0,
    "expiry_years": 0.25,
    "risk_free_rate": 0.01,
    "base_implied_vol": 0.25,
    "iv_by_regime": {"bull": 0.20, "bear": 0.30, "range": 0.25, "volatile": 0.40},
    "strategy": "regime_aware_option_strategy"
  },
  "steps": [
    {
      "time_index": 10,
      "underlying_close": 108.72,
      "regime": "bear",
      "implied_vol": 0.30,
      "remaining_T": 0.164,
      "option": {
        "option_type": "put",
        "strike": 100.0,
        "premium": 1.80,
        "greeks": {
          "delta": -0.22,
          "gamma": 0.023,
          "vega": 13.12,
          "theta": -11.75
        }
      },
      "proposed_orders": [{...}],
      "advice": {"max_abs_total_delta": 2.0},
      "orders_to_execute": [{...}],
      "decisions": [{
        "symbol": "MOCK__put__K100.0__T0.25",
        "risk": {
          "delta_total_abs": 1.11,
          "vega_total_abs": 65.60
        },
        "allowed": true,
        "caps": {
          "max_abs_total_delta": 2.0,
          "max_abs_total_vega": 1e9
        }
      }],
      "fills": [{...}]
    }
  ],
  "final": {
    "fills_count": 10,
    "cash": -27.93,
    "position": 25.0,
    "final_option_premium": 0.0,
    "total_pnl": -27.93
  }
}
```

### `summary.json` structure

```json
{
  "case": "counsel_strict_delta",
  "steps": 40,
  "fills_count": 20,
  "total_pnl": -27.93
}
```

---

## Reporting Queries (Copy-Paste)

### P&L summary

```bash
cat artifacts/summary.json | python3 -m json.tool
```

### Regime distribution across the backtest

```bash
cat artifacts/non_agentic_core_boundary_demo.json \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
from collections import Counter
regimes = Counter(s['regime'] for s in d['steps'])
for r, c in regimes.most_common():
    print(f'{r}: {c} steps')
"
```

### Orders blocked by the core boundary

```bash
cat artifacts/non_agentic_core_boundary_demo.json \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
blocked = [dec for s in d['steps'] for dec in s['decisions'] if not dec['allowed']]
allowed = [dec for s in d['steps'] for dec in s['decisions'] if dec['allowed']]
print(f'Allowed: {len(allowed)}, Blocked: {len(blocked)}')
if blocked:
    print('Blocked orders (core boundary enforced):')
    for b in blocked[:10]:
        r = b['risk']
        c = b['caps']
        print(f'  {b[\"symbol\"]} | delta_abs={r[\"delta_total_abs\"]:.2f} (cap={c[\"max_abs_total_delta\"]}) | vega_abs={r[\"vega_total_abs\"]:.2f} (cap={c[\"max_abs_total_vega\"]})')
"
```

### Greek time series (for charting)

```bash
cat artifacts/non_agentic_core_boundary_demo.json \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('t,regime,underlying,premium,delta,gamma,vega,theta,T')
for s in d['steps']:
    o=s['option']; g=o['greeks']
    print(f\"{s['time_index']},{s['regime']},{s['underlying_close']:.3f},{o['premium']:.4f},{g['delta']:.4f},{g['gamma']:.4f},{g['vega']:.2f},{g['theta']:.2f},{s['remaining_T']:.4f}\")
" > /tmp/greeks.csv
```

### Fill-by-fill P&L decomposition

```bash
cat artifacts/non_agentic_core_boundary_demo.json \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
cash=0.0; pos=0.0
print('step,side,qty,price,cash,position')
for s in d['steps']:
    for f in s['fills']:
        if f['side']=='buy':
            cash -= f['quantity']*f['price']
            pos += f['quantity']
        else:
            cash += f['quantity']*f['price']
            pos -= f['quantity']
        print(f\"{s['time_index']},{f['side']},{f['quantity']},{f['price']:.4f},{cash:.2f},{pos:.2f}\")
"
```

### Counsel delta cap effectiveness

```bash
cat artifacts/non_agentic_core_boundary_demo.json \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
counsel_cap = None
for s in d['steps']:
    if s['advice'] and s['advice']['max_abs_total_delta'] is not None:
        counsel_cap = s['advice']['max_abs_total_delta']
        break
if counsel_cap:
    violations = [dec for s in d['steps'] for dec in s['decisions']
                  if dec['allowed'] and dec['risk']['delta_total_abs'] > counsel_cap + 1e-9]
    print(f'Counsel delta cap: {counsel_cap}')
    print(f'Allowed orders exceeding counsel cap: {len(violations)}  (should be 0)')
else:
    print('No counsel delta cap found in artifacts')
"
```

---

## Manual-Only Callouts

> ⚠️ **These steps require human judgment — they cannot be automated safely.**

1. **Artifact directory cleanup:** `artifacts/` is gitignored, but disk accumulation can happen. Run `rm -rf artifacts/*.json` periodically — or better, use `--out /tmp/backtest-$(date +%s)` for one-off runs.

2. **Result interpretation:** A negative `total_pnl` does NOT mean the strategy is bad. This is a toy backtest with mock prices, no slippage, and no portfolio optimization. The P&L is for pipeline validation, not trading decisions.

3. **Regime thresholds require tuning:** The `SimpleTrendVolRegimeDetector` thresholds (vol ≥ 0.10, |slope| ≤ 0.01) were hand-tuned for the mock price series. Any change to the price generator requires re-tuning.

4. **No multi-contract risk:** The core boundary checks per-order delta/vega against caps. With multiple contracts, the *sum* of deltas could exceed caps even if each individually passes. Add aggregated risk checks before treating as production-ready.

5. **Greeks-only risk:** Only delta and vega are enforced in the boundary. Gamma (convexity), theta (time decay), and rho (rate sensitivity) are computed and stored in artifacts but **not** used for filtering. This is a deliberate simplification for the MVP.

6. **Demo counsel is static:** `StrictDeltaCounsel` returns a hardcoded cap. A real LLM counsel would be stateful and context-aware, but must still return only `Advice` (caps), never orders.

7. **Price feed is mock:** `_make_closes()` is deterministic and sinusoidal. For real backtesting, replace it with a data adapter that loads OHLCV from a CSV, database, or API — but maintain the same `list[float]` interface to `StaggeredInputEnv`.

---

## Assumptions Summary

| # | Assumption | Impact |
|---|-----------|--------|
| 1 | Mock deterministic price series | No external data dependency; reproducible |
| 2 | Toy regime detector (slope + volatility) | Not production-grade; thresholds need tuning |
| 3 | Single option contract per step | No multi-leg, no portfolio effects |
| 4 | Fill at market close (no slippage) | P&L is optimistic relative to real execution |
| 5 | Linear time decay | Differs from real calendar + trading-day conventions |
| 6 | No position sizing / portfolio risk | Only per-order risk caps enforced |
| 7 | Static counsel caps | Real LLM counsel would be dynamic |
| 8 | Python ≥ 3.10 | Uses `from __future__ import annotations`, Literal types |
| 9 | Greeks-only risk (delta, vega) | Gamma, theta, rho not filtered |

---

## Test Coverage

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

| Test | Validates |
|------|-----------|
| `test_staggered_env.py::test_observation_has_expected_length_and_values` | StaggeredInputEnv observation correctness (no look-ahead) |
| `test_backtest_runner.py::test_runner_executes_and_clamps_with_counsel` | Generic BacktestRunner with counsel quantity clamping |
| `test_non_agentic_core_boundary_demo.py::test_demo_writes_artifacts_and_applies_counsel_risk_cap` | Full boundary: artifact writing, counsel delta caps, greeks in output |
| `test_non_agentic_core_boundary_demo.py::test_demo_script_can_run_as_one_command` | CLI exit code, artifact file existence |

---

## Reproducibility Checklist

- [ ] Repo clean: `git status` shows no unexpected changes
- [ ] Python version: `python3 --version` → 3.10+
- [ ] Run tests: `python3 -m unittest discover -s tests -p 'test_*.py' -v` → all pass
- [ ] Run demo: `python3 trading_bot/demo_non_agentic_boundary.py --out ./artifacts --case counsel_strict_delta --steps 60`
- [ ] Artifacts exist: `ls artifacts/non_agentic_core_boundary_demo.json artifacts/summary.json`
- [ ] Verify boundary: blocked orders exist in artifact decisions
- [ ] Verify no leakage: no step references future price
- [ ] `artifacts/` is in `.gitignore` — artifacts not committed
