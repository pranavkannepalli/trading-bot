# trading-bot

## Non-agentic core boundary demo (local/mock-safe)

A minimal, inspectable MVP that demonstrates a “non-agentic core boundary” pattern:
- a strategy proposes orders
- the core computes options greeks (risk)
- an optional “LLM counsel” provides *risk caps* (not free-form actions)
- the core filters orders to stay within caps
- artifacts are written to disk as JSON

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

This writes:
- `artifacts/non_agentic_core_boundary_demo.json` (full decisions, greeks, and fills)
- `artifacts/summary.json` (small summary)

### What to look for (boundary proof)
- In `non_agentic_core_boundary_demo.json`, each step has:
  - `proposed_orders` (strategy output)
  - `decisions` with `risk` (core-computed deltas/vegas) and `allowed`
  - when `--case counsel_strict_delta` is used, `advice.max_abs_total_delta` acts as the counsel-provided risk cap (orders exceeding the cap become `allowed: false`).

### Run tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```
