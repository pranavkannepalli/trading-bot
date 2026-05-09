from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable

from trading_bot.types import Fill, Order


class ExecutionEngine(ABC):
    @abstractmethod
    def execute(self, orders: Iterable[Order], market_slice: Any, *, timestamp: int) -> list[Fill]:
        """Execute orders against a provided market slice and return fills."""
        raise NotImplementedError


class BacktestExecutionEngine(ExecutionEngine):
    """Simple fill model for backtesting.

    - If order.limit_price is None: fill at market_slice.close
    - If limit_price is provided: fill only if side is compatible with limit
      (buy fills when close <= limit_price; sell fills when close >= limit_price)
    """

    def execute(self, orders: Iterable[Order], market_slice: Any, *, timestamp: int) -> list[Fill]:
        fills: list[Fill] = []
        price = float(getattr(market_slice, "close"))

        for order in orders:
            if order.limit_price is None:
                fills.append(
                    Fill(
                        symbol=order.symbol,
                        side=order.side,
                        quantity=order.quantity,
                        price=price,
                        timestamp=timestamp,
                    )
                )
                continue

            lp = float(order.limit_price)
            can_fill = (order.side == "buy" and price <= lp) or (order.side == "sell" and price >= lp)
            if can_fill:
                fills.append(
                    Fill(
                        symbol=order.symbol,
                        side=order.side,
                        quantity=order.quantity,
                        price=price,
                        timestamp=timestamp,
                    )
                )

        return fills
