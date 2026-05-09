from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable

from trading_bot.types import Advice, Order


class LLMCounsel(ABC):
    """LLM counsel adapter contract.

    The strategy proposes orders; counsel can revise risk constraints by returning Advice.
    """

    @abstractmethod
    def advise(self, *, proposed_orders: Iterable[Order], market_state: Any) -> Advice:
        raise NotImplementedError
