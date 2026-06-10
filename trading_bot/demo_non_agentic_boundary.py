from __future__ import annotations

# When executed as a script (e.g. `python trading_bot/demo_non_agentic_boundary.py`),
# sys.path[0] becomes `.../trading_bot`, which would shadow the stdlib `types` module
# with `./trading_bot/types.py`. Fix sys.path early.
import sys
import os

if __package__ is None or __package__ == "":
    # Derive paths without importing pathlib (which triggers stdlib imports
    # that would fail if stdlib `types` is shadowed).
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)

    # Remove the script directory so `import types` resolves to the stdlib.
    real_script_dir = os.path.realpath(script_dir)
    sys.path = [p for p in sys.path if os.path.realpath(p) != real_script_dir]
    sys.path.insert(0, repo_root)

import argparse
import json
import math
import os
from dataclasses import dataclass
from typing import Any, Optional

from trading_bot.envs import StaggeredInputEnv
from trading_bot.execution import BacktestExecutionEngine
from trading_bot.llm_counsel import LLMCounsel
from trading_bot.option_pricing import black_scholes_greeks
from trading_bot.regime_detection import SimpleTrendVolRegimeDetector
from trading_bot.types import Advice, Order, Side
from trading_bot.strategies import Strategy


@dataclass(frozen=True)
class OptionContract:
    underlying: str
    option_type: str  # "call"/"put"
    strike: float
    expiry_years: float
    risk_free_rate: float
    implied_vol: float


def _encode_option_symbol(underlying: str, option_type: str, strike: float, expiry_years: float) -> str:
    # Keep it simple and parseable for the core boundary.
    return f"{underlying}__{option_type}__K{strike}__T{expiry_years}"


def _decode_option_symbol(symbol: str) -> dict[str, Any]:
    # Very small parser for demo artifacts.
    parts = symbol.split("__")
    if len(parts) != 4:
        raise ValueError(f"Unexpected option symbol format: {symbol}")
    underlying = parts[0]
    opt_type = parts[1]
    k_part = parts[2]
    t_part = parts[3]
    if not k_part.startswith("K") or not t_part.startswith("T"):
        raise ValueError(f"Unexpected K/T fields: {symbol}")
    strike = float(k_part[1:])
    expiry_years = float(t_part[1:])
    return {"underlying": underlying, "option_type": opt_type, "strike": strike, "expiry_years": expiry_years}


class RegimeAwareOptionStrategy(Strategy):
    def __init__(self, *, underlying: str, strike: float, expiry_years: float, quantity: float):
        self._underlying = underlying
        self._strike = float(strike)
        self._expiry_years = float(expiry_years)
        self._quantity = float(quantity)

    @property
    def name(self) -> str:
        return "regime_aware_option_strategy"

    def propose_orders(self, observation: Any) -> list[Order]:
        regime = observation["regime"]
        if regime == "bear":
            opt_type = "put"
        elif regime == "volatile":
            opt_type = "put"
        else:
            opt_type = "call"  # bull/range -> calls

        symbol = _encode_option_symbol(self._underlying, opt_type, self._strike, self._expiry_years)
        return [Order(symbol=symbol, side="buy", quantity=self._quantity)]


class StrictDeltaCounsel(LLMCounsel):
    def __init__(self, *, max_abs_total_delta: float):
        self._max_abs_total_delta = float(max_abs_total_delta)

    def advise(self, *, proposed_orders: Any, market_state: Any) -> Advice:
        # In a real system, counsel might incorporate richer reasoning.
        # Here we only clamp risk so the core boundary logic is testable.
        return Advice(
            max_order_quantity=None,
            max_abs_total_delta=self._max_abs_total_delta,
            max_abs_total_vega=None,
        )


class NonAgenticOptionsBacktestRunner:
    """Mock-safe non-agentic core boundary with toy regime detection + options risk.

    - Strategy proposes options orders.
    - Core boundary computes greeks (risk) and filters orders to satisfy caps.
    - Optional LLMCounsel can provide risk clamps.
    - Execution fills using a simplified option premium model.

    Artifacts are written by the CLI runner below.
    """

    def __init__(
        self,
        *,
        window_size: int,
        lag_steps: int,
        underlying: str = "MOCK",
        strike: float = 100.0,
        expiry_years: float = 0.25,
        risk_free_rate: float = 0.01,
        base_implied_vol: float = 0.25,
        iv_by_regime: Optional[dict[str, float]] = None,
        core_max_abs_total_delta: float = 1_000_000.0,
        core_max_abs_total_vega: float = 1_000_000.0,
        execution_engine: Optional[BacktestExecutionEngine] = None,
        counsel: Optional[LLMCounsel] = None,
    ):
        self.window_size = int(window_size)
        self.lag_steps = int(lag_steps)
        self.underlying = underlying
        self.strike = float(strike)
        self.expiry_years = float(expiry_years)
        self.risk_free_rate = float(risk_free_rate)
        self.base_implied_vol = float(base_implied_vol)
        self.iv_by_regime = iv_by_regime or {
            "bull": 0.20,
            "bear": 0.30,
            "range": self.base_implied_vol,
            "volatile": 0.40,
        }
        self.core_max_abs_total_delta = float(core_max_abs_total_delta)
        self.core_max_abs_total_vega = float(core_max_abs_total_vega)
        self.execution_engine = execution_engine or BacktestExecutionEngine()
        self.counsel = counsel

    def run(self, *, closes: list[float], strategy: Strategy) -> dict[str, Any]:
        detector = SimpleTrendVolRegimeDetector()
        env = StaggeredInputEnv(closes, window_size=self.window_size, lag_steps=self.lag_steps)

        obs = env.reset()
        fills = []

        cash = 0.0
        position = 0.0

        artifacts: dict[str, Any] = {
            "meta": {
                "window_size": self.window_size,
                "lag_steps": self.lag_steps,
                "strike": self.strike,
                "expiry_years": self.expiry_years,
                "risk_free_rate": self.risk_free_rate,
                "base_implied_vol": self.base_implied_vol,
                "iv_by_regime": self.iv_by_regime,
                "strategy": getattr(strategy, "name", strategy.__class__.__name__),
            },
            "steps": [],
            "final": {},
        }

        while True:
            t = obs.time_index
            underlying_price = float(closes[t])
            window = obs.staggered_closes
            regime = detector.detect(window).name

            # Pick a fixed contract for the entire demo; only the option type changes.
            # Greeks + premium are recomputed each step from the (mock) implied vol.
            option_type = "put" if regime in {"bear", "volatile"} else "call"
            implied_vol = float(self.iv_by_regime.get(regime, self.base_implied_vol))

            # Time-decay is a toy: T shrinks linearly with index.
            remaining_T = max(self.expiry_years * (1.0 - (t / (len(closes) - 1))), 1e-6)

            greeks = black_scholes_greeks(
                S=underlying_price,
                K=self.strike,
                T=remaining_T,
                r=self.risk_free_rate,
                iv=implied_vol,
                option_type=option_type,
            )

            # Provide the regime to the strategy via an observation envelope.
            strategy_obs = {"staggered_closes": window, "time_index": t, "regime": regime}
            proposed_orders = list(strategy.propose_orders(strategy_obs))

            # Compute risk contributions for the proposed orders.
            def _risk_for_order(o: Order) -> dict[str, float]:
                meta = _decode_option_symbol(o.symbol)
                greeks_local = black_scholes_greeks(
                    S=underlying_price,
                    K=meta["strike"],
                    T=remaining_T,
                    r=self.risk_free_rate,
                    iv=implied_vol,
                    option_type=meta["option_type"],
                )
                qty_signed = float(o.quantity) * (1.0 if o.side == "buy" else -1.0)
                return {
                    "delta_total_abs": abs(greeks_local.delta * qty_signed),
                    "vega_total_abs": abs(greeks_local.vega * qty_signed),
                    "delta_total": greeks_local.delta * qty_signed,
                    "vega_total": greeks_local.vega * qty_signed,
                }

            # Apply optional counsel.
            advice = None
            orders_after_counsel = proposed_orders
            if self.counsel is not None and proposed_orders:
                advice = self.counsel.advise(
                    proposed_orders=proposed_orders,
                    market_state={
                        "time_index": t,
                        "regime": regime,
                        "underlying_close": underlying_price,
                        "implied_vol": implied_vol,
                        "remaining_T": remaining_T,
                    },
                )

                # Counsel quantity clamp (if set).
                if advice.max_order_quantity is not None:
                    orders_after_counsel = [
                        Order(
                            symbol=o.symbol,
                            side=o.side,
                            quantity=min(float(o.quantity), float(advice.max_order_quantity)),
                            limit_price=o.limit_price,
                        )
                        for o in orders_after_counsel
                    ]

            # Core boundary filtering: enforce risk caps on the (possibly counsel-modified) orders.
            effective_delta_cap = advice.max_abs_total_delta if advice and advice.max_abs_total_delta is not None else self.core_max_abs_total_delta
            effective_vega_cap = advice.max_abs_total_vega if advice and advice.max_abs_total_vega is not None else self.core_max_abs_total_vega

            orders_to_execute: list[Order] = []
            decisions = []
            for o in orders_after_counsel:
                risk = _risk_for_order(o)
                ok = (risk["delta_total_abs"] <= effective_delta_cap) and (risk["vega_total_abs"] <= effective_vega_cap)
                decisions.append({
                    "symbol": o.symbol,
                    "side": o.side,
                    "quantity": float(o.quantity),
                    "risk": risk,
                    "allowed": ok,
                    "caps": {"max_abs_total_delta": effective_delta_cap, "max_abs_total_vega": effective_vega_cap},
                })
                if ok:
                    orders_to_execute.append(o)

            option_premium = greeks.price
            market_slice = type("M", (), {"close": option_premium})()

            step_fills = self.execution_engine.execute(orders_to_execute, market_slice, timestamp=t)
            for f in step_fills:
                fills.append(f)
                # For demo PnL, treat each option contract fill as priced at premium.
                if f.side == "buy":
                    cash -= f.quantity * f.price
                    position += f.quantity
                else:
                    cash += f.quantity * f.price
                    position -= f.quantity

            artifacts["steps"].append({
                "time_index": t,
                "underlying_close": underlying_price,
                "regime": regime,
                "implied_vol": implied_vol,
                "remaining_T": remaining_T,
                "option": {
                    "option_type": option_type,
                    "strike": self.strike,
                    "premium": option_premium,
                    "greeks": {
                        "delta": greeks.delta,
                        "gamma": greeks.gamma,
                        "vega": greeks.vega,
                        "theta": greeks.theta,
                    },
                },
                "proposed_orders": [o.__dict__ for o in proposed_orders],
                "advice": None if advice is None else {
                    "max_order_quantity": advice.max_order_quantity,
                    "max_abs_total_delta": advice.max_abs_total_delta,
                    "max_abs_total_vega": advice.max_abs_total_vega,
                },
                "orders_to_execute": [o.__dict__ for o in orders_to_execute],
                "decisions": decisions,
                "fills": [f.__dict__ for f in step_fills],
            })

            obs, _, done = env.step()
            if done:
                break

        final_price = float(option_premium) if "option_premium" in locals() else 0.0
        total_pnl = cash + position * final_price
        artifacts["final"] = {
            "fills_count": len(fills),
            "cash": cash,
            "position": position,
            "final_option_premium": final_price,
            "total_pnl": total_pnl,
        }
        return artifacts


def _make_closes(n: int) -> list[float]:
    # Deterministic mock series: mild trend + wave + gentle oscillation.
    out: list[float] = []
    for i in range(n):
        trend = 0.12 * (i / max(1, n - 1))
        wave = 0.08 * math.sin(i / 4.0)
        micro = 0.02 * math.sin(i / 1.7 + 0.3)
        pct = trend + wave + micro
        out.append(100.0 * (1.0 + pct))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True, help="Directory to write artifacts into")
    p.add_argument("--case", default="default", choices=["default", "counsel_strict_delta"])
    p.add_argument("--quantity", type=float, default=10.0, help="Option contract quantity proposed by the strategy")
    p.add_argument("--core-max-abs-total-delta", type=float, default=1000.0)
    p.add_argument("--core-max-abs-total-vega", type=float, default=1e9)
    p.add_argument("--max-abs-total-delta-from-counsel", type=float, default=2.0)
    p.add_argument("--steps", type=int, default=60)
    args = p.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)

    window_size = 5
    lag_steps = 2

    # `--steps` should map to the number of environment iterations / artifact steps.
    # The env starts at t = window_size * lag_steps, so we need `start_t + args.steps` total closes.
    closes = _make_closes(window_size * lag_steps + args.steps)

    strategy = RegimeAwareOptionStrategy(
        underlying="MOCK",
        strike=100.0,
        expiry_years=0.25,
        quantity=float(args.quantity),
    )

    counsel: Optional[LLMCounsel] = None
    if args.case == "counsel_strict_delta":
        counsel = StrictDeltaCounsel(max_abs_total_delta=args.max_abs_total_delta_from_counsel)

    runner = NonAgenticOptionsBacktestRunner(
        window_size=window_size,
        lag_steps=lag_steps,
        underlying="MOCK",
        strike=100.0,
        expiry_years=0.25,
        risk_free_rate=0.01,
        base_implied_vol=0.25,
        core_max_abs_total_delta=float(args.core_max_abs_total_delta),
        core_max_abs_total_vega=float(args.core_max_abs_total_vega),
        counsel=counsel,
    )

    artifacts = runner.run(closes=closes, strategy=strategy)

    out_path = os.path.join(args.out, "non_agentic_core_boundary_demo.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(artifacts, f, indent=2)

    # Also write a tiny human-readable summary.
    summary = {
        "case": args.case,
        "steps": len(artifacts["steps"]),
        "fills_count": artifacts["final"]["fills_count"],
        "total_pnl": artifacts["final"]["total_pnl"],
    }
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
