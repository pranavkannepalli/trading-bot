from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from trading_bot.envs import StaggeredInputEnv
from trading_bot.execution import BacktestExecutionEngine, ExecutionEngine
from trading_bot.llm_counsel import LLMCounsel
from trading_bot.strategies import Strategy
from trading_bot.types import Advice, BacktestResult, Order


@dataclass(frozen=True)
class BacktestConfig:
    window_size: int = 5
    lag_steps: int = 2


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

    def run(self, *, strategy: Strategy, closes: list[float]) -> BacktestResult:
        env = StaggeredInputEnv(
            closes,
            window_size=self.config.window_size,
            lag_steps=self.config.lag_steps,
        )

        obs = env.reset()
        fills: list[Any] = []

        cash = 0.0
        position = 0.0

        while True:
            market_slice = env.market_slice()

            proposed_orders = list(strategy.propose_orders(obs))
            orders_to_execute = proposed_orders

            if self.counsel is not None and proposed_orders:
                advice = self.counsel.advise(
                    proposed_orders=proposed_orders,
                    market_state={"time_index": obs.time_index, "market_close": market_slice.close},
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

            obs, _, done = env.step()
            if done:
                break

        final_price = float(closes[-1])
        total_pnl = cash + position * final_price
        return BacktestResult(fills=fills, total_pnl=total_pnl)

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
