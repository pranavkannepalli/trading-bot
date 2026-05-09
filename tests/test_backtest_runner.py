import unittest

from trading_bot.backtest import BacktestRunner
from trading_bot.execution import BacktestExecutionEngine
from trading_bot.llm_counsel import LLMCounsel
from trading_bot.strategies import Strategy
from trading_bot.types import Advice, Order


class AlwaysBuyStrategy(Strategy):
    @property
    def name(self) -> str:
        return "always_buy"

    def propose_orders(self, observation):
        # Keep it simple: buy 10 units each step.
        return [Order(symbol="TEST", side="buy", quantity=10.0)]


class ClampCounsel(LLMCounsel):
    def __init__(self, max_q: float):
        self._max_q = max_q
        self.calls = 0

    def advise(self, *, proposed_orders, market_state):
        self.calls += 1
        return Advice(max_order_quantity=self._max_q)


class TestBacktestRunner(unittest.TestCase):
    def test_runner_executes_and_clamps_with_counsel(self):
        closes = [float(i) for i in range(1, 30)]
        counsel = ClampCounsel(max_q=3.0)
        runner = BacktestRunner(execution_engine=BacktestExecutionEngine(), counsel=counsel)

        result = runner.run(strategy=AlwaysBuyStrategy(), closes=closes)
        self.assertGreater(len(result.fills), 0)
        # Every fill quantity should respect counsel clamp.
        self.assertTrue(all(f.quantity <= 3.0 + 1e-9 for f in result.fills))
        # Counsel should have been called at least once.
        self.assertGreater(counsel.calls, 0)


if __name__ == "__main__":
    unittest.main()
