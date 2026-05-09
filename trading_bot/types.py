from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class Order:
    symbol: str
    side: Side
    quantity: float
    limit_price: Optional[float] = None


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: Side
    quantity: float
    price: float
    timestamp: int


@dataclass(frozen=True)
class Advice:
    """Contract between the strategy proposed orders and the LLM-counsel adapter."""

    max_order_quantity: Optional[float] = None


@dataclass(frozen=True)
class BacktestResult:
    fills: list[Fill]
    total_pnl: float
