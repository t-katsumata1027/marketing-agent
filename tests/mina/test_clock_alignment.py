"""小さな時計差の待機と、未来・期限切れ応答の停止を検証する。"""
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

from src.mina.clients import await_checked_at
from src.mina.m1_contract import validate_state


class ClockAlignmentTests(unittest.TestCase):
    def test_small_future_response_waits_without_relaxing_freshness(self):
        now = datetime(2026, 10, 16, 10, tzinfo=UTC)
        for seconds in (0.053, 0.166, 1):
            with self.subTest(seconds=seconds):
                checked = now + timedelta(seconds=seconds)
                state = {'checked_at': checked.isoformat(), 'account': 'EatsLog88161',
                         'contract_version': 'mina-write-v1', 'complete': True,
                         'date': '2026-10-16', 'meal_count': 0, 'meals': [], 'state_version': 'sv1_' + 'a' * 64}
                values = iter((now, checked))
                sleep = Mock()
                self.assertIs(await_checked_at(state, lambda: next(values), sleep), state)
                sleep.assert_called_once_with(seconds)
                validate_state(state, '2026-10-16', checked)
                with self.assertRaises(ValueError):
                    validate_state(state, '2026-10-16', now)

    def test_large_clock_difference_does_not_wait(self):
        now = datetime(2026, 10, 16, 10, tzinfo=UTC)
        sleep = Mock()
        with self.assertRaises(ValueError):
            await_checked_at({'checked_at': (now + timedelta(seconds=1.001)).isoformat()}, lambda: now, sleep)
        sleep.assert_not_called()

    def test_clock_not_advancing_stops_after_one_wait(self):
        now = datetime(2026, 10, 16, 10, tzinfo=UTC)
        sleep = Mock()
        with self.assertRaises(ValueError):
            await_checked_at({'checked_at': (now + timedelta(milliseconds=50)).isoformat()}, lambda: now, sleep)
        sleep.assert_called_once()

    def test_old_response_is_never_retimed(self):
        now = datetime(2026, 10, 16, 10, tzinfo=UTC)
        state = {'checked_at': (now - timedelta(seconds=121)).isoformat()}
        sleep = Mock()
        self.assertIs(await_checked_at(state, lambda: now, sleep), state)
        sleep.assert_not_called()
        self.assertEqual(state['checked_at'], (now - timedelta(seconds=121)).isoformat())
