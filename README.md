# trading-bot

A non-agentic core boundary demo: an LLM supplies numeric risk caps, never free-form actions, and a deterministic core enforces them over an options backtest.

## About

`trading-bot` demonstrates a **non-agentic core boundary** pattern for putting an LLM in the loop without handing it the controls. The strategy proposes orders and the core computes Black-Scholes greeks; the optional LLM counsel returns only an `Advice` struct of numeric caps (`max_abs_total_delta`, `max_abs_total_vega`, `max_order_quantity`) — no text, no tool calls, no open-ended instructions. The core is the sole authority that filters and enforces those caps, so the counsel can never bypass the boundary and the strategy can never exceed it. That containment is the point: the LLM advises on risk numbers, deterministic code decides what executes.

## Install

No dependencies. Pure Python standard library (`math`, `argparse`, `json`, `unittest`, `dataclasses`). Requires Python 3.11+.

```bash
git clone git@github.com:pranavkannepalli/trading-bot.git
cd trading-bot
```

## Usage / Quickstart

Run the boundary demo with a strict counsel delta cap, then inspect the audit trail:

```bash
python3 trading_bot/demo_non_agentic_boundary.py \
  --out ./artifacts \
  --case counsel_strict_delta \
  --steps 40 \
  --quantity 10 \
  --core-max-abs-total-delta 1000 \
  --max-abs-total-delta-from-counsel 2.0

cat artifacts/summary.json
python3 -m json.tool artifacts/non_agentic_core_boundary_demo.json | head -80
```

Run the tests from the repo root:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

### CLI reference — `demo_non_agentic_boundary.py`

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--out` | path | *(required)* | Directory to write artifacts into (created if missing) |
| `--case` | string | `default` | `default` or `counsel_strict_delta` |
| `--steps` | int | `60` | Number of environment iterations |
| `--quantity` | float | `10.0` | Option contract quantity proposed by the strategy |
| `--core-max-abs-total-delta` | float | `1000.0` | Core hard cap on absolute total delta |
| `--core-max-abs-total-vega` | float | `1e9` | Core hard cap on absolute total vega |
| `--max-abs-total-delta-from-counsel` | float | `2.0` | Counsel delta cap (used with `counsel_strict_delta`) |

With `--case default` the core enforces only its own (wide) hard caps, so all orders pass. With `--case counsel_strict_delta`, `StrictDeltaCounsel` sets a tight delta cap and orders that breach it are marked `allowed: false`.

### Second entrypoint — spot backtest CLI

A simpler spot/equity backtest runner is also shipped:

```bash
python3 -m trading_bot.backtest --out ./artifacts --data mock --steps 120
python3 -m trading_bot.backtest --out ./artifacts --data prices.csv --quantity 5
```

`--data mock` generates a deterministic price series; passing a path loads a CSV with a `close` column. It writes `backtest_artifacts.json` and `backtest_summary.json`.

## How it works

1. **Strategy proposes orders.** `RegimeAwareOptionStrategy` reads a regime label off the observation and proposes a single option `Order` (calls in bull/range, puts in bear/volatile).
2. **Core computes greeks.** For each proposed order the core prices the option and computes delta, gamma, vega, and theta via Black-Scholes (`option_pricing.black_scholes_greeks`, normal CDF via `math.erf`).
3. **Optional LLM counsel supplies caps.** If a counsel is configured, `advise()` returns an `Advice` dataclass with numeric caps only. `StrictDeltaCounsel` returns a fixed `max_abs_total_delta`.
4. **Core filters and enforces caps.** The core resolves the effective delta/vega caps (counsel cap if provided, else the core hard cap), computes each order's absolute risk contribution, and rejects any order that breaches a cap. Rejected orders never reach execution.
5. **Mock backtest execution.** `BacktestExecutionEngine` fills accepted orders at the option premium (market close), with no slippage, liquidity limits, or partial fills.
6. **Per-step JSON audit trail.** Every step records proposed orders, greeks, counsel `advice`, per-order `decisions` (risk + `allowed`), fills, and running P&L to `non_agentic_core_boundary_demo.json`, plus an aggregated `summary.json`.

## Features

- ✅ Non-agentic core boundary: numeric-cap `Advice` contract enforced by the core
- ✅ Black-Scholes greeks (price, delta, gamma, vega, theta), zero external deps
- ✅ Toy regime detection (`bull` / `bear` / `range` / `volatile`) from a window of closes
- ✅ Options boundary demo CLI (`demo_non_agentic_boundary.py`) with per-step JSON artifacts
- ✅ Spot/equity backtest CLI (`python -m trading_bot.backtest`) with mock or CSV data
- ✅ Mock execution engine with limit-price fill logic
- ✅ Extensible ABCs: `Strategy`, `LLMCounsel`, `ExecutionEngine`
- ✅ Unit tests for the boundary demo, backtest runner, and staggered env
- 🚧 Live LLM counsel adapter — the `LLMCounsel` ABC is stable, but only the toy `StrictDeltaCounsel` ships; no real model call is wired
- 📋 Evaluation report generator (`trading_bot/evaluation_report.py`) — spec'd in `docs/evaluation-report-spec.md`; the backtest CLI imports it optionally and skips it when absent
- 📋 Additional counsel cases (e.g. a vega cap) beyond `default` and `counsel_strict_delta`

## Roadmap

- Implement `trading_bot/evaluation_report.py` against the RFC in `docs/evaluation-report-spec.md` (risk-adjusted metrics, per-regime attribution, counsel-effectiveness).
- Ship a real `LLMCounsel` adapter that parses model output into the `Advice` numeric-cap contract.
