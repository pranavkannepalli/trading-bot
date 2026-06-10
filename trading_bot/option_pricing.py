from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


OptionType = Literal["call", "put"]


def _norm_cdf(x: float) -> float:
    # Standard normal CDF via erf.
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


@dataclass(frozen=True)
class BlackScholesGreeks:
    price: float
    delta: float
    gamma: float
    vega: float
    theta: float


def black_scholes_price(S: float, K: float, T: float, r: float, iv: float, option_type: OptionType) -> float:
    return black_scholes_greeks(S, K, T, r, iv, option_type).price


def black_scholes_greeks(S: float, K: float, T: float, r: float, iv: float, option_type: OptionType) -> BlackScholesGreeks:
    if T <= 0:
        # Option expires: price is intrinsic.
        if option_type == "call":
            price = max(S - K, 0.0)
            delta = 1.0 if S > K else 0.0
        else:
            price = max(K - S, 0.0)
            delta = -1.0 if S < K else 0.0
        return BlackScholesGreeks(price=price, delta=delta, gamma=0.0, vega=0.0, theta=0.0)

    if iv <= 0:
        # Degenerate volatility: behave like a deterministic forward.
        # Keep it simple for a mock demo.
        return BlackScholesGreeks(price=max((S - K) if option_type == "call" else (K - S), 0.0), delta=0.0, gamma=0.0, vega=0.0, theta=0.0)

    sig = float(iv)
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sig * sig) * T) / (sig * sqrtT)
    d2 = d1 - sig * sqrtT

    n_d1 = _norm_pdf(d1)
    n_d2 = _norm_cdf(d2)
    n_d1_cdf = _norm_cdf(d1)

    if option_type == "call":
        price = S * n_d1_cdf - K * math.exp(-r * T) * n_d2
        delta = n_d1_cdf
    else:
        price = K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
        delta = n_d1_cdf - 1.0

    gamma = n_d1 / (S * sig * sqrtT)
    vega = S * n_d1 * sqrtT

    # Theta (per year). This is a standard approximation.
    # Sign conventions vary; for a mock-risk demo we only need a stable magnitude.
    common = -S * n_d1 * sig / (2.0 * sqrtT)
    if option_type == "call":
        theta = common - r * K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        theta = common + r * K * math.exp(-r * T) * _norm_cdf(-d2)

    return BlackScholesGreeks(price=price, delta=delta, gamma=gamma, vega=vega, theta=theta)
