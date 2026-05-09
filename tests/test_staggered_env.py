import unittest

from trading_bot.envs import StaggeredInputEnv


class TestStaggeredEnv(unittest.TestCase):
    def test_observation_has_expected_length_and_values(self):
        closes = [1, 2, 3, 4, 5, 6, 7, 8]
        env = StaggeredInputEnv(closes, window_size=3, lag_steps=2)
        obs = env.reset()  # start_t = 3*2=6
        # t=6 => indices: 6, 4, 2
        self.assertEqual(obs.time_index, 6)
        self.assertEqual(obs.staggered_closes, [closes[6], closes[4], closes[2]])

        obs2, _, _ = env.step()
        self.assertEqual(obs2.time_index, 7)
        self.assertEqual(obs2.staggered_closes, [closes[7], closes[5], closes[3]])
