"""外部APIの書き込み停止と成否不明時の非再送を検証する。"""

import os
import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from src.mina.clients import DiscordClient, MealClient, OutcomeUnknown, request_json


class ClientTest(unittest.TestCase):
    def test_preflight_omits_idempotency_and_image_and_accepts_specific_400(self):
        error = HTTPError('https://logeats.test', 400, 'bad request', {},
                          io.BytesIO(json.dumps({'error': 'Idempotency-KeyにはUUIDを指定してください。'}).encode()))
        opener = Mock()
        opener.open.side_effect = error
        with patch('urllib.request.build_opener', return_value=opener):
            result = MealClient('https://logeats.test', 'x' * 32).verify_credentials()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.data, b'')
        self.assertIsNone(request.get_header('Idempotency-key'))
        self.assertEqual(result, {'token_accepted': True, 'registration_verified': False})

    def test_preflight_does_not_treat_unrelated_400_as_valid_credentials(self):
        opener = Mock()
        opener.open.side_effect = HTTPError('https://logeats.test', 400, 'private', {}, io.BytesIO(b'{"error":"private"}'))
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(RuntimeError) as raised:
                MealClient('https://logeats.test', 'x' * 32).verify_credentials()
        self.assertNotIn('private', str(raised.exception))
        self.assertEqual(opener.open.call_count, 1)

    def test_discord_write_disabled_before_http(self):
        with patch.dict(os.environ, {"MINA_LIVE_WRITES": "false"}), patch("src.mina.clients.urlopen") as send:
            with self.assertRaises(RuntimeError):
                DiscordClient("private", "123").send({"content": "test"})
        send.assert_not_called()

    def test_meal_write_disabled_before_http(self):
        with patch.dict(os.environ, {"MINA_LIVE_WRITES": "false"}), patch("src.mina.clients.urlopen") as send:
            with self.assertRaises(RuntimeError):
                MealClient("https://logeats.test", "x" * 32).json_write("/api/internal/mina/daily-share", {"date": "2026-10-08"})
        send.assert_not_called()

    def test_timeout_never_retried_and_hides_error(self):
        with patch("src.mina.clients.urlopen", side_effect=URLError("private-token")) as send:
            with self.assertRaises(OutcomeUnknown) as raised:
                request_json("https://logeats.test", {}, {})
        self.assertEqual(send.call_count, 1)
        self.assertNotIn("private-token", str(raised.exception))

    def test_500_is_unknown_and_429_does_not_retry(self):
        for code in (500, 429):
            with self.subTest(code=code), patch("src.mina.clients.urlopen", side_effect=HTTPError("https://logeats.test", code, "private", {}, None)) as send:
                with self.assertRaises(RuntimeError):
                    request_json("https://logeats.test", {}, {})
                self.assertEqual(send.call_count, 1)

    def test_meal_rejects_unapproved_api_path(self):
        with patch.dict(os.environ, {"MINA_LIVE_WRITES": "true"}), patch("src.mina.clients.urlopen") as send:
            with self.assertRaises(ValueError):
                MealClient("https://logeats.test", "x" * 32).json_write("/api/admin/users", {})
        send.assert_not_called()

    def test_image_size_limit_before_registration(self):
        with patch.dict(os.environ, {"MINA_LIVE_WRITES": "true"}), patch("src.mina.clients.urlopen") as send:
            with self.assertRaises(ValueError):
                MealClient("https://logeats.test", "x" * 32).register(b"\x89PNG\r\n\x1a\n" + b"0" * (4 * 1024 * 1024), "meal.png", "breakfast", "2026-10-08T07:30:00+09:00", "候補")
        send.assert_not_called()
