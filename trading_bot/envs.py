from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any


@dataclass(frozen=True)
class StaggeredObservation:
    time_index: int
    staggered_closes: list[float]


class StaggeredInputEnv:
    """Backtesting-friendly environment with staggered historical inputs.

    For a given time index t, observation includes:
      staggered_closes[i] = closes[t - i*lag_steps]

    This produces an input that is robust to different signal cadences.
    """

    def __init__(self, closes: list[float], *, window_size: int = 5, lag_steps: int = 2):
        if window_size <= 0:
            raise ValueError("window_size must be > 0")
        if lag_steps <= 0:
            raise ValueError("lag_steps must be > 0")
        if len(closes) < window_size * lag_steps + 1:
            raise ValueError("closes series too short for given window_size/lag_steps")

        self._closes = closes
        self.window_size = window_size
        self.lag_steps = lag_steps

        self._start_t = window_size * lag_steps
        self._t = None  # set on reset

    def reset(self) -> StaggeredObservation:
        self._t = self._start_t
        return self._observation(self._t)

    def observation(self) -> StaggeredObservation:
        if self._t is None:
            raise RuntimeError("env not reset")
        return self._observation(self._t)

    def market_slice(self) -> Any:
        if self._t is None:
            raise RuntimeError("env not reset")
        return SimpleNamespace(close=float(self._closes[self._t]))

    def step(self) -> tuple[StaggeredObservation, Any, bool]:
        if self._t is None:
            raise RuntimeError("env not reset")

        self._t += 1
        done = self._t >= len(self._closes)
        if done:
            # Return a terminal-ish observation; caller should ignore if done.
            terminal_t = len(self._closes) - 1
            return self._observation(terminal_t), self._market_slice_at(terminal_t), True

        return self._observation(self._t), self._market_slice_at(self._t), False

    def _observation(self, t: int) -> StaggeredObservation:
        closes = [float(self._closes[t - i * self.lag_steps]) for i in range(self.window_size)]
        return StaggeredObservation(time_index=t, staggered_closes=closes)

    def _market_slice_at(self, t: int) -> Any:
        return SimpleNamespace(close=float(self._closes[t]))
