# Trading Bot — LLM Counsel

## Non-agentic core boundary demo with regime detection, options greeks, and optional LLM risk caps

A minimal, inspectable MVP that demonstrates a **non-agentic core boundary** pattern:

1. **Strategy proposes orders** — what the strategy *wants* to do
2. **Core computes options greeks** — delta, gamma, vega, theta via Black-Scholes
3. **Optional LLM counsel provides risk caps** — numeric constraints only, never free-form actions
4. **Core filters orders** — enforces counsel caps (and its own hard caps), rejecting orders that breach
5. **Execution fills accepted orders** — mock backtest engine
6. **Artifacts written to disk** — full per-step JSON audit trail

---

## Quickstart

```bash
# 1. Clone and enter repo
git clone git@github.com:pranavkannepalli/trading-bot.git
cd trading-bot

# 2. Run the boundary demo (zero dependencies, pure Python stdlib)
python3 trading_bot/demo_non_agentic_boundary.py \
  --out ./artifacts \
  --case counsel_strict_delta \
  --steps 40 \
  --quantity 10 \
  --core-max-abs-total-delta 1000 \
  --max-abs-total-delta-from-counsel 2.0

# 3. Inspect the output
cat artifacts/summary.json
python3 -m json.tool artifacts/non_agentic_core_boundary_demo.json | head -80

# 4. Run the tests
python3 -m unittest discover -s tests -p 'test_*.py'
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Non-Agentic Core Boundary                 │
│                                                              │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────────┐   │
│  │ Strategy  │───▶│              │    │  LLM Counsel     │   │
│  │ proposes  │    │  Core        │◀───│  (optional)      │   │
│  │ orders    │    │  Boundary    │    │  returns Advice  │   │
│  └──────────┘    │              │    │  (numeric caps)  │   │
│                  │  1. Compute  │    └──────────────────┘   │
│  ┌──────────┐    │     greeks   │                           │
│  │ Regime   │───▶│  2. Apply    │    ┌──────────────────┐   │
│  │ Detector │    │     counsel  │───▶│  Execution       │   │
│  │ (bull/   │    │     caps     │    │  Engine          │   │
│  │  bear/   │    │  3. Filter   │    │  (mock fills)    │   │
│  │  range/  │    │     orders   │    └──────────────────┘   │
│  │  volatile│    └──────────────┘                           │
│  └──────────┘             │                                 │
│                           ▼                                 │
│                  ┌──────────────────┐                       │
│                  │  JSON Artifacts  │                       │
│                  │  (per-step       │                       │
│                  │   audit trail)   │                       │
│                  └──────────────────┘                       │
└──────────────────────────────────────────────────────────────┘
```

**The key property:** The LLM counsel only provides *numeric risk caps* (`max_abs_total_delta`, `max_abs_total_vega`, `max_order_quantity`). It never produces free-form text, tool calls, or open-ended instructions. The core boundary *enforces* these caps — the counsel cannot bypass the boundary, and the strategy cannot exceed the caps.

---

## Prerequisites

- **Python:** 3.11+ (tested with 3.12)
- **Dependencies:** Zero external packages — pure Python stdlib only (`math`, `argparse`, `json`, `unittest`, `dataclasses`)
- **Network:** Not required — all data is synthetic/deterministic, no live market feeds or API keys
- **OS:** Linux, macOS, or WSL (anywhere Python runs)

---

## Assumptions

- **Deterministic toy data:** Demo uses a fixed sine-wave price series inside `demo_non_agentic_boundary.py` — no real market data.
- **Black-Scholes greeks:** Options greeks (delta, gamma, vega, theta) use standard Black-Scholes with synthetic IV, not market-observed surfaces. Assumes European-style, no dividends, constant risk-free rate.
- **Mock-safe execution:** `BacktestExecutionEngine` fills at the market close price — no slippage, no liquidity constraints, no partial fills.
- **Regime detection:** `SimpleTrendVolRegimeDetector` uses only a window of closing prices (bull/bear/range/volatile) — not a production regime model.
- **LLM counsel is simulated:** Counsel adapter returns a simple `Advice` dataclass (e.g., `max_abs_total_delta=2.0`) — there is no live LLM call. The pattern is the contract, not the implementation.
- **Single-asset, single-leg options:** The demo operates on one underlying symbol with synthetic call options. No spreads, combos, or portfolio-level risk.

---

## What this is NOT

- **NOT a live trading system** — no broker integration, no order routing, no real money
- **NOT an agentic trading bot** — the "counsel" only provides numeric risk caps, never free-form actions or tool calls
- **NOT a production risk engine** — greeks are textbook Black-Scholes, no dividends, no early exercise (American), no rate curves
- **NOT a portfolio optimizer** — single asset, single option leg at a time

---

## CLI Reference

```
python3 trading_bot/demo_non_agentic_boundary.py [OPTIONS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--out` | path | *(required)* | Directory to write artifacts into |
| `--case` | string | `default` | Demo case: `default` or `counsel_strict_delta` |
| `--steps` | int | `60` | Number of environment iterations |
| `--quantity` | float | `10.0` | Option contract quantity proposed by the strategy |
| `--core-max-abs-total-delta` | float | `1000.0` | Core boundary hard cap on absolute total delta |
| `--core-max-abs-total-vega` | float | `1e9` | Core boundary hard cap on absolute total vega |
| `--max-abs-total-delta-from-counsel` | float | `2.0` | Counsel delta cap (used with `counsel_strict_delta` case) |

### Demo Cases

| Case | Behavior |
|------|----------|
| `default` | No counsel — core enforces its own hard caps (very wide by default, so all orders pass) |
| `counsel_strict_delta` | `StrictDeltaCounsel` sets a tight delta cap (e.g., 2.0) — some orders will be rejected |

---

## Artifacts

After running the demo, `./artifacts/` (gitignored) contains:

| File | Description |
|------|-------------|
| `non_agentic_core_boundary_demo.json` | Full per-step decisions, greeks, fills, and counsel advice |
| `summary.json` | Aggregated totals (case, steps, fills_count, total_pnl) |

### Boundary Proof — What to Inspect Manually

Open `artifacts/non_agentic_core_boundary_demo.json` and check:

1. **Each step** contains:
   - `proposed_orders` — strategy output (what it *wants* to do)
   - `decisions` — per-order `risk` (delta/vega) and `allowed` boolean
   - `advice` — counsel-provided risk cap (e.g., `max_abs_total_delta: 2.0`)

2. **Core enforces counsel caps:** When `--case counsel_strict_delta` is used, any proposed order whose delta would exceed the counsel's cap gets `allowed: false`. Count how many orders are `allowed: true` vs `false` — the core should reject orders that would breach the cap.

3. **No free-form actions:** The counsel `Advice` dataclass is a plain struct with numeric fields — there are no strings, tool calls, or free-form instructions. This is the boundary.

---

## Project Structure

```
trading-bot/
├── README.md                              ← this file
├── .gitignore                             ← ignores __pycache__, *.pyc, artifacts/
├── docs/
│   └── evaluation-report-spec.md          ← RFC: evaluation report JSON schema & metrics spec
├── trading_bot/
│   ├── __init__.py                        ← package init
│   ├── types.py                           ← Order, Fill, Advice, BacktestResult dataclasses
│   ├── strategies.py                      ← Strategy ABC (propose orders from observations)
│   ├── envs.py                            ← StaggeredInputEnv (backtesting env with lagged windows)
│   ├── execution.py                       ← ExecutionEngine ABC + BacktestExecutionEngine (mock fills)
│   ├── option_pricing.py                  ← Black-Scholes greeks (pure Python, zero deps)
│   ├── regime_detection.py                ← SimpleTrendVolRegimeDetector (bull/bear/range/volatile)
│   ├── llm_counsel.py                     ← LLMCounsel ABC (advise with numeric risk caps)
│   ├── backtest.py                        ← BacktestRunner (orchestrator, simpler than demo runner)
│   └── demo_non_agentic_boundary.py       ← CLI entrypoint + NonAgenticOptionsBacktestRunner
└── tests/
    ├── test_non_agentic_core_boundary_demo.py  ← counsel caps enforcement + JSON artifact writing
    ├── test_backtest_runner.py                  ← backtest loop with/without counsel clamp
    └── test_staggered_env.py                    ← env reset, observation, edge cases
```

### Core Modules Explained

| Module | Role |
|--------|------|
| **`types.py`** | Canonical dataclasses: `Order`, `Fill`, `Advice`, `BacktestResult`. The `Advice` struct is the boundary contract — only numeric caps. |
| **`strategies.py`** | `Strategy` ABC with `propose_orders(observation) → list[Order]`. Implement your own strategy by subclassing. |
| **`envs.py`** | `StaggeredInputEnv` provides observation windows at configurable lag. Used by both `BacktestRunner` and the demo runner. |
| **`execution.py`** | `BacktestExecutionEngine` fills at market close (or respects limit prices). Replace with your own engine for different fill models. |
| **`option_pricing.py`** | `black_scholes_greeks(S, K, T, r, iv, option_type)` returns `BlackScholesGreeks` (price, delta, gamma, vega, theta). Uses `math.erf` for the normal CDF — zero external deps. |
| **`regime_detection.py`** | `SimpleTrendVolRegimeDetector` classifies a window of closes into one of four regimes based on slope and log-return volatility. |
| **`llm_counsel.py`** | `LLMCounsel` ABC with `advise(proposed_orders, market_state) → Advice`. The `StrictDeltaCounsel` in the demo is a toy — swap in a real LLM adapter that returns the same `Advice` struct. |
| **`backtest.py`** | `BacktestRunner` orchestrates a simpler backtest loop (no greeks, no regime — spot/equity focused). Good starting point for extending. |
| **`demo_non_agentic_boundary.py`** | The full demo runner with regime detection, option greeks, counsel integration, and boundary enforcement. The CLI entrypoint. |

---

## Extension Guide

### Adding a New Demo Case

1. Add a new counsel class in `demo_non_agentic_boundary.py` (or import one):
   ```python
   class StrictVegaCounsel(LLMCounsel):
       def __init__(self, *, max_abs_total_vega: float):
           self._max_abs_total_vega = float(max_abs_total_vega)

       def advise(self, *, proposed_orders, market_state):
           return Advice(max_abs_total_vega=self._max_abs_total_vega)
   ```

2. Add the new case to the `argparse` `choices` and wire it in `main()`:
   ```python
   p.add_argument("--case", choices=["default", "counsel_strict_delta", "counsel_strict_vega"])
   # ...
   elif args.case == "counsel_strict_vega":
       counsel = StrictVegaCounsel(max_abs_total_vega=args.max_abs_total_vega_from_counsel)
   ```

### Adding a New Strategy

Subclass `Strategy` and implement `propose_orders()`:

```python
from trading_bot.strategies import Strategy
from trading_bot.types import Order

class MyStrategy(Strategy):
    @property
    def name(self) -> str:
        return "my_strategy"

    def propose_orders(self, observation) -> list[Order]:
        # observation has: staggered_closes, time_index, regime
        if observation["regime"] == "bull":
            return [Order(symbol="MOCK", side="buy", quantity=5.0)]
        return []
```

### Adding a Real LLM Counsel Adapter

The `LLMCounsel` ABC contract is deliberately narrow. To wire a real LLM:

```python
class OpenAICounsel(LLMCounsel):
    def advise(self, *, proposed_orders, market_state) -> Advice:
        # Call your LLM with proposed_orders + market_state context
        # Parse the response into numeric caps
        # Return an Advice dataclass — never return free-form text
        return Advice(
            max_abs_total_delta=parsed_delta_cap,
            max_abs_total_vega=parsed_vega_cap,
        )
```

The boundary guarantee: as long as your adapter returns `Advice` (numeric caps only), the core will never expose free-form LLM output to execution.

---

## Tests

```bash
# Run all tests
python3 -m unittest discover -s tests -p 'test_*.py'

# Run a specific test file
python3 -m unittest tests/test_non_agentic_core_boundary_demo.py
```

**Test coverage:**

| Test file | What it verifies |
|-----------|-----------------|
| `test_non_agentic_core_boundary_demo.py` | CLI runs successfully, artifacts are written, counsel delta cap is enforced (orders above cap are blocked, orders below cap pass), greeks appear in output |
| `test_backtest_runner.py` | BacktestRunner executes fills, counsel clamp is respected, counsel is called at least once |
| `test_staggered_env.py` | StaggeredInputEnv reset/step produce correct staggered observations at expected indices |

---

## Evaluation Report (RFC)

See [`docs/evaluation-report-spec.md`](docs/evaluation-report-spec.md) for the full specification of the evaluation report that will be generated post-backtest. The spec defines:

- **9-section JSON schema** (meta, summary, time_series, performance, risk, regime, counsel, trades, warnings)
- **Calculations:** Sharpe, Sortino, Calmar ratios, VaR/CVaR, max drawdown, profit factor, expectancy
- **Per-regime breakdowns** and counsel effectiveness metrics
- **Status:** Draft RFC — implementation pending (`trading_bot/evaluation_report.py`)

---

## Manual-Only Callouts

- **Artifact inspection is manual:** No automated pass/fail — the JSON output must be human-reviewed for the boundary proof sections above.
- **No P&L tracking in the simple backtest:** `BacktestRunner` tracks fills and a terminal P&L. The demo runner adds per-step P&L, but neither computes mark-to-market throughout.
- **Single demo case implemented:** Currently `default` and `counsel_strict_delta`. Adding `counsel_vega_cap` or `counsel_off` is an extension task (see Extension Guide above).
- **No memory of prior runs:** Each CLI invocation overwrites `artifacts/`. Save interesting outputs manually before re-running.
- **No benchmark comparison:** The evaluation report spec defines `benchmark_return_pct` fields, but no benchmark is configured in the current codebase.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `ModuleNotFoundError: No module named 'trading_bot'` | Run from the repo root (`cd trading-bot`). The demo script fixes `sys.path` but relative imports need the root. |
| `ValueError: closes series too short` | Increase `--steps` or decrease `window_size × lag_steps` in the runner. The env needs at least `window_size * lag_steps + 1` data points. |
| `PermissionError` writing artifacts | Ensure `--out` directory is writable, or let the script create it (it calls `os.makedirs`). |
| Tests fail on `ModuleNotFoundError` | Run `python3 -m unittest discover -s tests -p 'test_*.py'` from the repo root. |
