# trading-bot

## Non-agentic core boundary demo (local/mock-safe)

A minimal, inspectable MVP that demonstrates a "non-agentic core boundary" pattern:
- a strategy proposes orders
- the core computes options greeks (risk)
- an optional "LLM counsel" provides *risk caps* (not free-form actions)
- the core filters orders to stay within caps
- artifacts are written to disk as JSON

### Prerequisites

- **Python:** 3.11+ (tested with 3.12)
- **Dependencies:** Zero external packages — pure Python stdlib only (`math`, `argparse`, `json`, `unittest`, `dataclasses`)
- **Network:** Not required — all data is synthetic/deterministic, no live market feeds or API keys

### Assumptions

- **Deterministic toy data:** Demo uses a fixed sine-wave price series inside `demo_non_agentic_boundary.py` — no real market data.
- **Black-Scholes greeks:** Options greeks (delta, gamma, vega, theta) use standard Black-Scholes with synthetic IV, not market-observed surfaces. Assumes European-style, no dividends, constant risk-free rate.
- **Mock-safe execution:** `BacktestExecutionEngine` fills at the market close price — no slippage, no liquidity constraints, no partial fills.
- **Regime detection:** `SimpleTrendVolRegimeDetector` uses only a window of closing prices (bull/bear/range/volatile) — not a production regime model.
- **LLM counsel is simulated:** Counsel adapter returns a simple `Advice` dataclass (e.g., `max_abs_total_delta=2.0`) — there is no live LLM call. The pattern is the contract, not the implementation.
- **Single-asset, single-leg options:** The demo operates on one underlying symbol with synthetic call options. No spreads, combos, or portfolio-level risk.

### What this is NOT

- **NOT a live trading system** — no broker integration, no order routing, no real money
- **NOT an agentic trading bot** — the "counsel" only provides numeric risk caps, never free-form actions or tool calls
- **NOT a production risk engine** — greeks are textbook Black-Scholes, no dividends, no early exercise (American), no rate curves

### One-command run

```bash
python3 trading_bot/demo_non_agentic_boundary.py \
  --out ./artifacts \
  --case counsel_strict_delta \
  --steps 40 \
  --quantity 10 \
  --core-max-abs-total-delta 1000 \
  --max-abs-total-delta-from-counsel 2.0
```

Artifacts written to `./artifacts/` (gitignored):
| File | Description |
|------|-------------|
| `non_agentic_core_boundary_demo.json` | Full per-step decisions, greeks, fills, and counsel advice |
| `summary.json` | Aggregated totals (steps, fills, delta sums) |

### Boundary proof — what to inspect manually

Open `artifacts/non_agentic_core_boundary_demo.json` and check:

1. **Each step** contains:
   - `proposed_orders` — strategy output (what it *wants* to do)
   - `decisions` — per-order `risk` (delta/vega) and `allowed` boolean
   - `advice` — counsel-provided risk cap (e.g., `max_abs_total_delta: 2.0`)

2. **Core enforces counsel caps:** When `--case counsel_strict_delta` is used, any proposed order whose delta would push the *total absolute delta* above the counsel's cap gets `allowed: false`.
   - Count how many orders are `allowed: true` vs `false` — the core should reject orders that would breach the cap.

3. **No free-form actions:** The counsel `Advice` dataclass is a plain struct with numeric fields — there are no strings, tool calls, or free-form instructions. This is the boundary.

### Run tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected output: all tests pass. Tests cover:
- `test_non_agentic_core_boundary_demo.py` — counsel caps enforcement + JSON artifact writing
- `test_backtest_runner.py` — backtest loop with/without counsel
- `test_staggered_env.py` — staggered input environment reset, observation, and edge cases

### Project structure

```
trading-bot/
├── README.md                          ← this file
├── .gitignore                         ← ignores __pycache__, *.pyc, artifacts/
├── trading_bot/
│   ├── __init__.py
│   ├── types.py                       # Order, Fill, Advice, BacktestResult dataclasses
│   ├── strategies.py                  # Strategy ABC + demo strategies
│   ├── envs.py                        # StaggeredInputEnv (backtesting environment)
│   ├── execution.py                   # ExecutionEngine ABC + BacktestExecutionEngine
│   ├── option_pricing.py              # Black-Scholes greeks (pure Python, zero deps)
│   ├── regime_detection.py            # SimpleTrendVolRegimeDetector
│   ├── llm_counsel.py                 # LLMCounsel ABC + StrictDeltaCounsel
│   ├── backtest.py                    # BacktestRunner (orchestrator)
│   └── demo_non_agentic_boundary.py   # CLI entrypoint for the boundary demo
└── tests/
    ├── test_non_agentic_core_boundary_demo.py
    ├── test_backtest_runner.py
    └── test_staggered_env.py
```

### Manual-only callouts

- **Artifact inspection is manual:** No automated pass/fail — the JSON output must be human-reviewed for the boundary proof sections above.
- **No P&L tracking:** The demo tracks fills and deltas but does not compute mark-to-market P&L. That is intentionally out of scope to keep the boundary clean.
- **Single demo case:** Currently only `counsel_strict_delta` is implemented. Adding `counsel_vega_cap` or `counsel_off` is a manual extension task.
- **No memory of prior runs:** Each CLI invocation overwrites `artifacts/`. Save interesting outputs manually before re-running.

### Evidence (local)
- Demo CLI created:
  - `artifacts/non_agentic_core_boundary_demo.json`
  - `artifacts/summary.json`
- Risk-caps-only counsel:
  - `StrictDeltaCounsel` returns `Advice(max_abs_total_delta=...)` and the core uses it as `max_abs_total_delta` while deciding `allowed`.
- Unit tests exercised the caps enforcement + JSON artifact writing.
