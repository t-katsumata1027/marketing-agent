"""OAuthの照合と、秘密値をコマンド引数へ出さない保存を確認する。"""

import tempfile
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch
from types import SimpleNamespace
from urllib.request import urlopen

from src.mina.google_auth_setup import ConsentServer, OAuthFlow, SCOPE, save_refresh


class GoogleAuthSetupTests(unittest.TestCase):
    def test_idle_browser_connection_does_not_block_request(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'ok')

        with ConsentServer(('127.0.0.1', 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with socket.create_connection(server.server_address, timeout=2):
                    with urlopen(f'http://127.0.0.1:{server.server_port}/', timeout=2) as response:
                        self.assertEqual(response.read(), b'ok')
            finally:
                server.shutdown()
                thread.join(timeout=2)

    def test_pkce_and_offline_scope(self):
        flow = OAuthFlow('example.apps.googleusercontent.com', 'secret', 8766)
        query = parse_qs(urlsplit(flow.authorization_url()).query)
        self.assertEqual(query['scope'], [SCOPE])
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertEqual(query['access_type'], ['offline'])
        self.assertNotIn(flow.verifier, flow.authorization_url())

    def test_invalid_state_and_replay(self):
        flow = OAuthFlow('example.apps.googleusercontent.com', 'secret', 8766)
        with self.assertRaises(ValueError):
            flow.callback_code('state=incorrect&code=one')
        query = urlencode({'state': flow.state, 'code': 'one'})
        self.assertEqual(flow.callback_code(query), 'one')
        with self.assertRaises(ValueError):
            flow.callback_code(query)

    def test_save_uses_stdin_and_removes_recovery(self):
        with tempfile.TemporaryDirectory() as temp, patch('src.mina.google_auth_setup.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
            save_refresh('private-token', '/config', temp)
            self.assertNotIn('private-token', run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs['input'], 'private-token')
            self.assertFalse((Path(temp) / 'refresh-pending.json').exists())

    def test_failed_save_keeps_recovery_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as temp, patch('src.mina.google_auth_setup.subprocess.run', return_value=SimpleNamespace(returncode=1)) as run:
            with self.assertRaisesRegex(RuntimeError, 'oauth_doppler_save_unconfirmed'):
                save_refresh('private-token', '/config', temp)
            self.assertTrue((Path(temp) / 'refresh-pending.json').exists())
            with self.assertRaisesRegex(RuntimeError, 'oauth_recovery_save_failed'):
                save_refresh('different-token', '/config', temp)
            self.assertEqual(run.call_count, 1)
