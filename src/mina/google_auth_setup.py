"""Drive専用OAuthの初回同意を、SSH転送したループバックで受け取る。"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, build_opener

from .drive import NoRedirect


SCOPE = 'https://www.googleapis.com/auth/drive.file'


class ConsentServer(ThreadingHTTPServer):
    """ブラウザーの事前接続が、実際の認証リクエストを塞がないようにする。"""

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address


class OAuthFlow:
    """stateとPKCEを検査し、認証コード・トークンを出力しない。"""

    def __init__(self, client_id, client_secret, port):
        if not client_id.endswith('.apps.googleusercontent.com') or not client_secret or not 1024 <= port <= 65535:
            raise ValueError('Google OAuthクライアントの設定が必要です')
        self.client_id, self.client_secret = client_id, client_secret
        self.redirect = f'http://127.0.0.1:{port}/oauth/callback'
        self.state = secrets.token_urlsafe(32)
        self.verifier = secrets.token_urlsafe(48)
        self.used = False

    def authorization_url(self):
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b'=').decode()
        return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
            'client_id': self.client_id, 'redirect_uri': self.redirect, 'response_type': 'code',
            'scope': SCOPE, 'state': self.state, 'code_challenge': challenge, 'code_challenge_method': 'S256',
            'access_type': 'offline', 'prompt': 'consent select_account'})

    def callback_code(self, query):
        values = parse_qs(query, keep_blank_values=True)
        if self.used or len(values.get('state', [])) != 1 or not secrets.compare_digest(values['state'][0], self.state):
            raise ValueError('oauth_state_invalid')
        if 'error' in values:
            self.used = True
            raise RuntimeError('oauth_consent_denied')
        if len(values.get('code', [])) != 1 or not values['code'][0] or len(values['code'][0]) > 4096:
            raise ValueError('oauth_code_invalid')
        self.used = True
        return values['code'][0]

    def exchange(self, code):
        body = urlencode({'client_id': self.client_id, 'client_secret': self.client_secret,
                          'code': code, 'code_verifier': self.verifier, 'redirect_uri': self.redirect,
                          'grant_type': 'authorization_code'}).encode()
        try:
            request = Request('https://oauth2.googleapis.com/token', data=body,
                              headers={'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
            with build_opener(NoRedirect()).open(request, timeout=30) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError()
            result = json.loads(raw)
            if not isinstance(result.get('refresh_token'), str) or not result['refresh_token'] or result.get('token_type', '').lower() != 'bearer':
                raise ValueError()
            # 返された権限があれば、今回の必要スコープを含むことを照合する。
            if 'scope' in result and SCOPE not in result['scope'].split():
                raise ValueError()
            return result['refresh_token']
        except Exception:
            raise RuntimeError('oauth_exchange_failed') from None


def save_refresh(token, config_dir, recovery_directory):
    """秘密値は標準入力でDopplerへ渡し、失敗時だけ保護した実体を保持する。"""
    directory = Path(recovery_directory)
    recovery = directory / 'refresh-pending.json'
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with recovery.open('x', encoding='utf-8') as file:
            os.chmod(recovery, 0o600)
            json.dump({'MINA_GOOGLE_REFRESH_TOKEN': token}, file)
    except Exception:
        raise RuntimeError('oauth_recovery_save_failed') from None
    command = ['doppler', 'secrets', 'set', 'MINA_GOOGLE_REFRESH_TOKEN', '--no-interactive', '--silent',
               '--no-check-version', '--attempts', '1', '--project', 'marketing-agent', '--config', 'dev',
               '--config-dir', str(config_dir)]
    try:
        result = subprocess.run(command, input=token, text=True, encoding='utf-8',
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if result.returncode != 0:
            raise RuntimeError()
    except Exception:
        raise RuntimeError('oauth_doppler_save_unconfirmed') from None
    try:
        recovery.unlink()
    except OSError:
        raise RuntimeError('oauth_recovery_cleanup_required') from None


def serve_flow(flow, port, config_dir, recovery_directory):
    result = {'finished': False, 'success': False, 'error': None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            # callbackのクエリには認証コードがあるためHTTP原文を保存しない。
            return

        def page(self, message, status=200):
            raw = ('<!doctype html><meta charset="utf-8"><title>みな Drive認証</title><p>' + message + '</p>').encode()
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.connection.settimeout(5)
            if self.headers.get('Host') != f'127.0.0.1:{port}':
                return self.page('接続先が一致しません。', 400)
            path = urlsplit(self.path)
            if path.path == '/done':
                return self.page('Googleの同意とDopplerへの保存が完了しました。この画面を閉じて構いません。' if result['success'] else '認証を完了できませんでした。チャットの案内を確認してください。')
            if path.path == '/' and not result['finished']:
                self.send_response(302)
                self.send_header('Location', flow.authorization_url())
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                return
            if path.path != '/oauth/callback' or result['finished']:
                return self.page('対象外のリクエストです。', 404)
            try:
                code = flow.callback_code(path.query)
            except ValueError:
                return self.page('認証の照合に失敗しました。', 400)
            except RuntimeError:
                result.update(finished=True, error='oauth_consent_denied')
            else:
                try:
                    save_refresh(flow.exchange(code), config_dir, recovery_directory)
                    result.update(finished=True, success=True)
                except RuntimeError as error:
                    result.update(finished=True, error=str(error))
            self.send_response(303)
            self.send_header('Location', '/done')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()

    with ConsentServer(('127.0.0.1', port), Handler) as server:
        server.timeout = 1
        deadline = time.monotonic() + 900
        print(json.dumps({'event': 'mina_google_consent_waiting', 'url': f'http://127.0.0.1:{port}/'}), flush=True)
        while not result['finished'] and time.monotonic() < deadline:
            server.handle_request()
        # ブラウザーの完了ページへの転送を受け取ってから待機を終了する。
        finish_deadline = time.monotonic() + 5
        while result['finished'] and time.monotonic() < finish_deadline:
            server.handle_request()
    print(json.dumps({'event': 'mina_google_consent_finished', 'success': result['success'],
                      'error': result['error'] or (None if result['success'] else 'oauth_timeout')}), flush=True)
    return result['success']


def main():
    parser = argparse.ArgumentParser(description='みな専用Google Drive認証の初期設定')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--config-dir', type=Path, required=True)
    parser.add_argument('--recovery-directory', type=Path, default=Path('runtime/google-oauth'))
    args = parser.parse_args()
    if os.getenv('MINA_GOOGLE_REFRESH_TOKEN') or (args.recovery_directory / 'refresh-pending.json').exists():
        raise SystemExit('既存の認証結果を照合してから再認証してください')
    flow = OAuthFlow(os.getenv('MINA_GOOGLE_CLIENT_ID', ''), os.getenv('MINA_GOOGLE_CLIENT_SECRET', ''), args.port)
    raise SystemExit(0 if serve_flow(flow, args.port, args.config_dir, args.recovery_directory) else 1)


if __name__ == '__main__':
    main()
