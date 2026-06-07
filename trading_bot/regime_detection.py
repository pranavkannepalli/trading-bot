from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Regime:
    name: str


class RegimeDetector:
    def detect(self, window_closes: Sequence[float]) -> Regime:
        raise NotImplementedError


class SimpleTrendVolRegimeDetector(RegimeDetector):
    """Toy regime detector (deterministic, mock-safe).

    Uses only a window of closes and outputs one of:
      - bull, bear, range, volatile
    """

    def detect(self, window_closes: Sequence[float]) -> Regime:
        if len(window_closes) < 2:
            raise ValueError("window_closes too short")

        first = float(window_closes[0])
        last = float(window_closes[-1])
        if first == 0:
            slope = 0.0
        else:
            slope = (last / first) - 1.0

        # Volatility proxy: std dev of log returns across the window.
        rets: list[float] = []
        for a, b in zip(window_closes[:-1], window_closes[1:]):
            if a <= 0 or b <= 0:
                continue
            rets.append(math.log(b / a))

        vol = _stdev(rets) if len(rets) >= 2 else 0.0

        # Heuristic thresholds tuned to keep the demo interesting but stable.
        abs_slope = abs(slope)
        if vol >= 0.10:
            return Regime("volatile")
        if abs_slope <= 0.01:
            return Regime("range")
        if slope > 0:
            return Regime("bull")
        return Regime("bear")


def _stdev(xs: Sequence[float]) -> float:
    xs2 = [float(x) for x in xs]
    if len(xs2) < 2:
        return 0.0
    mu = sum(xs2) / len(xs2)
    var = sum((x - mu) ** 2 for x in xs2) / (len(xs2) - 1)
    return math.sqrt(max(var, 0.0))
