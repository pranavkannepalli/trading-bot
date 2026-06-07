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

### Run tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```
