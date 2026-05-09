from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from trading_bot.types import Order


class Strategy(ABC):
    """Strategy interface: take an observation, propose orders."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def propose_orders(self, observation: Any) -> list[Order]:
        """Return the orders you want to place for the next step."""
        raise NotImplementedError
