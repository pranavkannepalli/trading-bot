from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from trading_bot.envs import StaggeredInputEnv
from trading_bot.execution import BacktestExecutionEngine, ExecutionEngine
from trading_bot.llm_counsel import LLMCounsel
from trading_bot.strategies import Strategy
from trading_bot.types import Advice, BacktestResult, Order


@dataclass(frozen=True)
class BacktestConfig:
    window_size: int = 5
    lag_steps: int = 2


@dataclass
class BacktestStep:
    """Per-step artifact record written during artifact-collecting runs."""
    time_index: int
    close: float
    proposed_orders: list[dict[str, Any]] = field(default_factory=list)
    orders_executed: list[dict[str, Any]] = field(default_factory=list)
    fills: list[dict[str, Any]] = field(default_factory=list)
    cash: float = 0.0
    position: float = 0.0
    equity: float = 0.0
    advice: Optional[dict[str, Any]] = None


class BacktestRunner:
    def __init__(
        self,
        *,
        execution_engine: Optional[ExecutionEngine] = None,
        counsel: Optional[LLMCounsel] = None,
        config: Optional[BacktestConfig] = None,
    ):
        self.execution_engine = execution_engine or BacktestExecutionEngine()
        self.counsel = counsel
        self.config = config or BacktestConfig()

    def run(
        self, *, strategy: Strategy, closes: list[float], record_steps: bool = False
    ) -> Union[BacktestResult, tuple[BacktestResult, list[BacktestStep]]]:
        """Run a strategy against a price series.

        Args:
            strategy: The strategy to backtest.
            closes: List of closing prices (one per time step).
            record_steps: If True, return per-step artifacts alongside the result.

        Returns:
            If record_steps=False: BacktestResult (fills + total_pnl).
            If record_steps=True: (BacktestResult, list[BacktestStep]).
        """
        env = StaggeredInputEnv(
            closes,
            window_size=self.config.window_size,
            lag_steps=self.config.lag_steps,
        )

        obs = env.reset()
        fills: list[Any] = []
        steps: list[BacktestStep] = []

        cash = 0.0
        position = 0.0

        while True:
            market_slice = env.market_slice()
            current_close = float(market_slice.close)

            proposed_orders = list(strategy.propose_orders(obs))
            orders_to_execute = proposed_orders
            advice = None

            if self.counsel is not None and proposed_orders:
                advice = self.counsel.advise(
                    proposed_orders=proposed_orders,
                    market_state={"time_index": obs.time_index, "market_close": current_close},
                )
                orders_to_execute = self._apply_advice(orders_to_execute, advice)

            step_fills = self.execution_engine.execute(
                orders_to_execute,
                market_slice,
                timestamp=obs.time_index,
            )

            for f in step_fills:
                fills.append(f)
                if f.side == "buy":
                    cash -= f.quantity * f.price
                    position += f.quantity
                else:
                    cash += f.quantity * f.price
                    position -= f.quantity

            if record_steps:
                equity = cash + position * current_close
                step_advice = None
                if advice is not None:
                    step_advice = {
                        "max_order_quantity": advice.max_order_quantity,
                        "max_abs_total_delta": advice.max_abs_total_delta,
                        "max_abs_total_vega": advice.max_abs_total_vega,
                    }
                steps.append(BacktestStep(
                    time_index=obs.time_index,
                    close=current_close,
                    proposed_orders=[o.__dict__ for o in proposed_orders],
                    orders_executed=[o.__dict__ for o in orders_to_execute],
                    fills=[f.__dict__ for f in step_fills],
                    cash=cash,
                    position=position,
                    equity=equity,
                    advice=step_advice,
                ))

            obs, _, done = env.step()
            if done:
                break

        final_price = float(closes[-1])
        total_pnl = cash + position * final_price
        result = BacktestResult(fills=fills, total_pnl=total_pnl)

        if record_steps:
            return result, steps
        return result

    def _apply_advice(self, orders: list[Order], advice: Advice) -> list[Order]:
        if advice.max_order_quantity is None:
            return orders

        out: list[Order] = []
        for o in orders:
            q = float(o.quantity)
            out.append(
                Order(
                    symbol=o.symbol,
                    side=o.side,
                    quantity=min(q, float(advice.max_order_quantity)),
                    limit_price=o.limit_price,
                )
            )
        return out


# ---------------------------------------------------------------------------
# Mock data generator
# ---------------------------------------------------------------------------

def generate_mock_closes(n: int, *, seed: float = 0.0) -> list[float]:
    """Generate deterministic mock price series for backtesting.

    Produces a mild trend + wave + oscillation pattern that is reproducible.
    No network calls, no randomness — fully deterministic from seed.
    """
    out: list[float] = []
    for i in range(n):
        trend = 0.12 * (i / max(1, n - 1))
        wave = 0.08 * math.sin(i / 4.0 + seed)
        micro = 0.02 * math.sin(i / 1.7 + 0.3 + seed)
        pct = trend + wave + micro
        out.append(100.0 * (1.0 + pct))
    return out


def load_closes_from_csv(path: str) -> list[float]:
    """Load closing prices from a CSV file.

    Accepts CSVs with a 'close' column (header expected).
    Falls back to first numeric column if no 'close' header.
    """
    closes: list[float] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []

        # Prefer a 'close' column; otherwise use the first column.
        col = "close" if "close" in (fn.lower() for fn in fieldnames) else (fieldnames[0] if fieldnames else None)
        if col is None:
            raise ValueError(f"No columns found in {path}")

        # Handle case-insensitive match for 'close'.
        for fn in fieldnames:
            if fn.lower() == "close":
                col = fn
                break

        for row in reader:
            closes.append(float(row[col]))
    return closes


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class AlwaysBuyStrategy(Strategy):
    """Simple test strategy: buy a fixed quantity each step."""

    def __init__(self, symbol: str = "TEST", quantity: float = 10.0):
        self._symbol = symbol
        self._quantity = float(quantity)

    @property
    def name(self) -> str:
        return "always_buy"

    def propose_orders(self, observation: Any) -> list[Order]:
        return [Order(symbol=self._symbol, side="buy", quantity=self._quantity)]


def _serialize_step(step: BacktestStep) -> dict[str, Any]:
    """Convert a BacktestStep to a JSON-serializable dict."""
    return {
        "time_index": step.time_index,
        "close": step.close,
        "proposed_orders": step.proposed_orders,
        "orders_executed": step.orders_executed,
        "fills": step.fills,
        "cash": step.cash,
        "position": step.position,
        "equity": step.equity,
        "advice": step.advice,
    }


def main(argv: Optional[list[str]] = None) -> int:
    """One-command backtest CLI.

    Usage:
        python -m trading_bot.backtest --out ./artifacts
        python -m trading_bot.backtest --out ./artifacts --data mock --steps 120
        python -m trading_bot.backtest --out ./artifacts --data prices.csv --quantity 5
    """
    p = argparse.ArgumentParser(
        description="Run a local/mock-safe backtest and write inspectable artifacts to disk."
    )
    p.add_argument("--out", required=True, help="Directory to write artifacts into")
    p.add_argument(
        "--data", default="mock",
        help="'mock' for generated data, or path to a CSV file with a 'close' column",
    )
    p.add_argument("--steps", type=int, default=60, help="Number of steps (ignored when --data is a CSV)")
    p.add_argument("--symbol", default="TEST", help="Symbol traded by the always_buy strategy")
    p.add_argument("--quantity", type=float, default=10.0, help="Quantity per order")
    p.add_argument("--window-size", type=int, default=5, help="Staggered env window size")
    p.add_argument("--lag-steps", type=int, default=2, help="Staggered env lag steps")
    p.add_argument("--seed", type=float, default=0.0, help="Seed for mock data generator")
    p.add_argument(
        "--report", default=True, action=argparse.BooleanOptionalAction,
        help="Generate evaluation report after backtest",
    )
    args = p.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)

    # Load or generate price data.
    if args.data == "mock":
        # The env needs window_size * lag_steps extra lead-in closes.
        total_closes = args.window_size * args.lag_steps + args.steps
        closes = generate_mock_closes(total_closes, seed=args.seed)
        data_source = f"mock (seed={args.seed})"
    else:
        closes = load_closes_from_csv(args.data)
        args.steps = len(closes) - args.window_size * args.lag_steps
        if args.steps <= 0:
            print(f"Error: CSV has {len(closes)} rows, need at least "
                  f"{args.window_size * args.lag_steps + 1} for window_size={args.window_size} "
                  f"lag_steps={args.lag_steps}", file=sys.stderr)
            return 1
        data_source = os.path.basename(args.data)

    # Build runner.
    config = BacktestConfig(window_size=args.window_size, lag_steps=args.lag_steps)
    runner = BacktestRunner(execution_engine=BacktestExecutionEngine(), config=config)
    strategy = AlwaysBuyStrategy(symbol=args.symbol, quantity=args.quantity)

    # Run.
    result, steps = runner.run(strategy=strategy, closes=closes, record_steps=True)

    # Build artifacts dict.
    artifact_steps = [_serialize_step(s) for s in steps]
    artifacts: dict[str, Any] = {
        "meta": {
            "window_size": args.window_size,
            "lag_steps": args.lag_steps,
            "data_source": data_source,
            "total_closes": len(closes),
            "strategy": strategy.name,
            "symbol": args.symbol,
            "quantity": args.quantity,
        },
        "steps": artifact_steps,
        "final": {
            "fills_count": len(result.fills),
            "total_pnl": result.total_pnl,
            "last_close": closes[-1],
        },
    }

    # Write step-by-step artifact.
    artifact_path = os.path.join(args.out, "backtest_artifacts.json")
    with open(artifact_path, "w", encoding="utf-8") as f:
        json.dump(artifacts, f, indent=2)
    print(f"Artifacts written to {artifact_path}")

    # Write summary.
    summary = {
        "strategy": strategy.name,
        "steps": len(steps),
        "fills_count": len(result.fills),
        "total_pnl": result.total_pnl,
        "data_source": data_source,
    }
    summary_path = os.path.join(args.out, "backtest_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"Summary written to {summary_path}")

    # Optionally generate evaluation report.
    if args.report:
        try:
            from trading_bot.evaluation_report import build_evaluation_report  # type: ignore[import-untyped]
        except ImportError:
            print(
                "Note: evaluation_report module not available (requires evaluation-report branch). "
                "Skipping evaluation report.",
                file=sys.stderr,
            )
        else:
            strategy_config = {
                "symbol": args.symbol,
                "quantity_per_step": args.quantity,
            }
            run_config = {
                "core_max_abs_total_delta": 1_000_000.0,
                "core_max_abs_total_vega": 1_000_000.0,
            }

            report = build_evaluation_report(
                artifacts=artifacts,
                strategy_name=strategy.name,
                strategy_class=strategy.__class__.__name__,
                strategy_config=strategy_config,
                run_config=run_config,
                closes=closes,
            )
            report_path = os.path.join(args.out, "evaluation_report.json")
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
            print(f"Evaluation report written to {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
