# Trading Bot — Non-Agentic Core Boundary

A local-first, mock-safe trading engine that demonstrates a **non-agentic core boundary** pattern: the strategy _proposes_ orders, the core _computes_ risk (option greeks), an optional LLM counsel provides _risk caps_ (not free-form actions), and the core _filters_ orders to stay within those caps. No live market data, no network calls, no agentic decision-making — everything is deterministic and reproducible from a single command.

---

## Architecture

```
StaggeredInputEnv ──► Strategy ──► Proposed Orders
                                      │
                              ┌───────▼──────────┐
                              │  LLM Counsel      │ (optional)
                              │  → Advice (caps)  │
                              └───────┬──────────┘
                                      │
                              ┌───────▼──────────┐
                              │  Core Boundary    │
                              │  Greeks + Filter  │
                              └───────┬──────────┘
                                      │
                              ┌───────▼──────────┐
                              │  Execution        │
                              │  → Fills          │
                              └───────┬──────────┘
                                      │
                              ┌───────▼──────────┐
                              │  Artifacts (JSON) │
                              └──────────────────┘
```

**Key invariant:** The LLM counsel can only _clamp_ risk (max delta, max vega, max quantity) — it cannot propose orders, pick symbols, or drive execution. The core always has the final filter.

---

## Modules

| Module | Path | Responsibility |
|--------|------|----------------|
| **Types** | `trading_bot/types.py` | `Order`, `Fill`, `Advice`, `BacktestResult` data classes |
| **Environments** | `trading_bot/envs.py` | `StaggeredInputEnv` — staggered historical input for backtesting |
| **Regime Detection** | `trading_bot/regime_detection.py` | `SimpleTrendVolRegimeDetector` — bull/bear/range/volatile classification |
| **Option Pricing** | `trading_bot/option_pricing.py` | `black_scholes_greeks()` — price, delta, gamma, vega, theta |
| **Strategies** | `trading_bot/strategies.py` | `Strategy` ABC — propose orders from observations |
| **LLM Counsel** | `trading_bot/llm_counsel.py` | `LLMCounsel` ABC — return risk caps as `Advice` |
| **Execution** | `trading_bot/execution.py` | `BacktestExecutionEngine` — fill orders at market close |
| **Backtest Runner** | `trading_bot/backtest.py` | Generic `BacktestRunner` with counsel integration |
| **Demo CLI** | `trading_bot/demo_non_agentic_boundary.py` | Full boundary demo with regime-aware option strategy + strict delta counsel |

---

## One-Command Demo

### Default case (core caps only, no counsel)

```bash
python3 trading_bot/demo_non_agentic_boundary.py \
  --out ./artifacts \
  --case default \
  --steps 40 \
  --quantity 10
```

### Counsel strict-delta case (counsel provides risk cap)

```bash
python3 trading_bot/demo_non_agentic_boundary.py \
  --out ./artifacts \
  --case counsel_strict_delta \
  --steps 40 \
  --quantity 10 \
  --core-max-abs-total-delta 1000 \
  --max-abs-total-delta-from-counsel 2.0
```

**CLI flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--out` | _(required)_ | Directory to write artifacts into |
| `--case` | `default` | `default` or `counsel_strict_delta` |
| `--steps` | `60` | Number of environment iterations |
| `--quantity` | `10.0` | Option contracts proposed per step |
| `--core-max-abs-total-delta` | `1000.0` | Core delta cap (fallback if counsel not used) |
| `--core-max-abs-total-vega` | `1e9` | Core vega cap (fallback if counsel not used) |
| `--max-abs-total-delta-from-counsel` | `2.0` | Counsel delta cap (used with `counsel_strict_delta`) |

---

## Artifact Locations & Format

All output is written to `--out` (gitignored by default under `artifacts/`).

### `non_agentic_core_boundary_demo.json`

Full per-step trace. Each step contains:

```json
{
  "steps": [{
    "time_index": 10,
    "underlying_close": 108.72,
    "regime": "bear",
    "implied_vol": 0.30,
    "remaining_T": 0.164,
    "option": {
      "option_type": "put",
      "strike": 100.0,
      "premium": 1.80,
      "greeks": { "delta": -0.22, "gamma": 0.023, "vega": 13.12, "theta": -11.75 }
    },
    "proposed_orders": [{ "symbol": "MOCK__put__K100.0__T0.25", "side": "buy", "quantity": 5.0 }],
    "advice": { "max_abs_total_delta": 2.0 },
    "orders_to_execute": [...],
    "decisions": [{
      "symbol": "...",
      "risk": { "delta_total_abs": 1.11, "vega_total_abs": 65.60 },
      "allowed": true,
      "caps": { "max_abs_total_delta": 2.0, "max_abs_total_vega": 1e9 }
    }],
    "fills": [{ "symbol": "...", "side": "buy", "quantity": 5.0, "price": 1.80, "timestamp": 10 }]
  }],
  "final": {
    "fills_count": 10,
    "cash": -27.93,
    "position": 25.0,
    "final_option_premium": 0.0,
    "total_pnl": -27.93
  },
  "meta": { "window_size": 5, "lag_steps": 2, ... }
}
```

### `summary.json`

Condensed human-readable output:

```json
{
  "case": "counsel_strict_delta",
  "steps": 40,
  "fills_count": 20,
  "total_pnl": -27.93
}
```

### How to verify the boundary

In `non_agentic_core_boundary_demo.json`, search for decisions where `"allowed": false` — those are orders the core blocked because they exceeded the counsel-provided risk caps. Allowed orders always have `risk.delta_total_abs ≤ caps.max_abs_total_delta`.

---

## Assumptions

1. **Mock market data:** All price series are generated deterministically (`_make_closes()` in the demo). No real market data, no network calls.
2. **Toy regime detection:** `SimpleTrendVolRegimeDetector` uses only a rolling window of closes with naive slope + volatility heuristics. Not production-grade.
3. **Single contract per step:** The demo proposes one option order per step (call in bull/range, put in bear/volatile). Multi-leg or multi-strike strategies are not in scope for this MVP.
4. **Simplified execution:** `BacktestExecutionEngine` fills at market close. No slippage, no commission, no partial fills, no order book simulation.
5. **Time decay is linear:** `remaining_T` decreases linearly across steps. Real time decay follows actual calendar conventions.
6. **No position sizing / portfolio optimization:** The core only enforces absolute risk caps per-order. It does not manage overall portfolio risk, margin, or diversification.
7. **Deterministic counsel:** `StrictDeltaCounsel` is a static cap implementation. A real LLM counsel would receive market state and return dynamic caps — but still only `Advice`, never orders.
8. **Python ≥ 3.10 required:** Uses `from __future__ import annotations`, `dataclasses`, `abc.ABC`, and `Literal` types.

---

## Manual-Only Callouts

> ⚠️ **This system is not agentic.** The LLM counsel is a _constraint provider_, not a decision-maker. It cannot place orders, pick symbols, or drive execution. This is intentional: the core boundary ensures the trading engine remains deterministic and auditable regardless of what an LLM might return.

- **No live trading:** This is a backtesting demo. Connecting to a broker requires a separate execution adapter, position tracking, and risk management — none of which are in scope.
- **No real LLM integration:** The counsel is a mock (`StrictDeltaCounsel`). Wiring a real LLM requires implementing `LLMCounsel.advise()` with an API call, but the returned `Advice` struct must remain caps-only.
- **Greeks are the _only_ risk metric:** The core currently considers delta and vega. Gamma, theta, rho, and correlation risk are not filtered — they are computed and stored in artifacts but not used for boundary enforcement.
- **Artifacts directory is gitignored:** `artifacts/` is in `.gitignore`. Output files should never be committed.
- **Option symbol encoding is opaque:** The `MOCK__put__K100.0__T0.25` format is internal. A real system would use OCC symbols or a security master. The `_encode_option_symbol` / `_decode_option_symbol` helpers are for demo parsing only.

---

## Running Tests

```bash
# From repo root
python3 -m unittest discover -s tests -p 'test_*.py'
```

Test coverage:

| Test file | What it validates |
|-----------|-------------------|
| `test_staggered_env.py` | `StaggeredInputEnv` observation correctness (indices, values) |
| `test_backtest_runner.py` | Generic `BacktestRunner` with counsel quantity clamping |
| `test_non_agentic_core_boundary_demo.py` | Full boundary demo: artifact writing, counsel delta caps, greeks in output, CLI exit code |

---

## Extending

### Adding a new strategy

Implement `Strategy` and return `Order` objects from `propose_orders()`:

```python
class MyStrategy(Strategy):
    @property
    def name(self) -> str:
        return "my_strategy"

    def propose_orders(self, observation):
        # observation contains: staggered_closes, time_index, regime (demo)
        return [Order(symbol="MOCK__call__K105.0__T0.25", side="buy", quantity=5.0)]
```

### Adding a real LLM counsel

Implement `LLMCounsel` and return `Advice` with risk caps:

```python
class RealLLMCounsel(LLMCounsel):
    def advise(self, *, proposed_orders, market_state) -> Advice:
        # Call your LLM, parse response
        # MUST only return caps — never orders
        return Advice(max_abs_total_delta=3.0, max_abs_total_vega=500.0)
```

### Adding a new risk metric

1. Add the cap field to `Advice` in `types.py`
2. Compute the metric in the demo's `_risk_for_order()` function
3. Add enforcement in the core boundary filter loop
4. Update the `decisions[].risk` and `decisions[].caps` dictionaries

---

## Repo

```
trading-bot/
├── trading_bot/
│   ├── __init__.py
│   ├── types.py                 # Order, Fill, Advice, BacktestResult
│   ├── envs.py                  # StaggeredInputEnv
│   ├── regime_detection.py      # SimpleTrendVolRegimeDetector
│   ├── option_pricing.py        # Black-Scholes greeks
│   ├── strategies.py            # Strategy ABC
│   ├── llm_counsel.py           # LLMCounsel ABC
│   ├── execution.py             # BacktestExecutionEngine
│   ├── backtest.py              # Generic BacktestRunner
│   └── demo_non_agentic_boundary.py  # Full boundary demo CLI
├── tests/
│   ├── test_staggered_env.py
│   ├── test_backtest_runner.py
│   └── test_non_agentic_core_boundary_demo.py
├── artifacts/                   # gitignored — demo output
├── .gitignore
└── README.md
```
