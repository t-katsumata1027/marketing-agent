"""みな運用の外部接続。書き込みは既定で拒否する。"""

import json
import os
import re
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoWriteRedirect(HTTPRedirectHandler):
    """秘密値を含む外部書き込みは、転送先へ送らない。"""

    def redirect_request(self, *args, **kwargs):
        return None


def urlopen(request, timeout):
    return build_opener(NoWriteRedirect()).open(request, timeout=timeout)


def read_json_response(response):
    raw = response.read(131073)
    if len(raw) > 131072:
        raise ValueError('外部応答が大きすぎます')
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError('外部応答の形式が不正です')
    return result


class OutcomeUnknown(RuntimeError):
    """外部書き込みの成否が不明で、自動再試行できない。"""


class MealWriteFailure(OutcomeUnknown):
    """HTTPの成否と保存状態を分離し、原文を例外へ含めない。"""

    def __init__(self, code, payload):
        from .m1_contract import safe_write_state
        self.http_code, self.write_state = code, safe_write_state(payload)
        super().__init__(f'meal_http_{code}')


def meal_http_failure(error):
    try:
        payload = read_json_response(error)
    except (OSError, ValueError, TypeError, AttributeError):
        payload = {}
    return MealWriteFailure(error.code, payload)


def require_live():
    if os.getenv("MINA_LIVE_WRITES", "false").lower() != "true":
        raise RuntimeError("外部書き込みは無効です")


def request_json(url: str, body: dict, headers: dict, method="POST", meal=False) -> dict:
    """秘密を例外へ含めず、一回だけ送信する。"""
    data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    request = Request(url, data=data, headers={"Content-Type": "application/json", **headers}, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            return read_json_response(response)
    except HTTPError as error:
        if meal:
            raise meal_http_failure(error) from None
        # 5xxは応答前の副作用を否定できないため、例外本文も再送も扱わない。
        if error.code >= 500:
            raise OutcomeUnknown("external_write_unknown") from None
        raise RuntimeError(f"external_http_{error.code}") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise OutcomeUnknown("external_write_unknown") from None


class DiscordClient:
    """Botメッセージで署名付きInteractionを起動するカードを送る。"""

    def __init__(self, token: str, channel: str):
        if not token or not channel.isdigit():
            raise ValueError("Discord Botとチャンネルの設定が必要です")
        self.token, self.channel = token, channel

    def send(self, payload: dict):
        require_live()
        return request_json(f"https://discord.com/api/v10/channels/{self.channel}/messages",
                            payload, {"Authorization": f"Bot {self.token}"})


class XClient:
    """ブラウザのCookieを使わず、公式APIのユーザー認証で公開する。"""

    def __init__(self):
        import tweepy
        names = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
        if not all(os.getenv(name) for name in names):
            raise ValueError("X API設定が不足しています")
        self.client = tweepy.Client(consumer_key=os.environ[names[0]], consumer_secret=os.environ[names[1]],
                                   access_token=os.environ[names[2]], access_token_secret=os.environ[names[3]],
                                   wait_on_rate_limit=False)

    def verify_account(self, expected: str):
        result = self.client.get_me(user_auth=True)
        if not result.data or result.data.username.lower() != expected.lower():
            raise RuntimeError("公開先アカウントが一致しません")

    def publish(self, text: str) -> str:
        require_live()
        result = self.client.create_tweet(text=text, user_auth=True)
        if not result.data or not str(result.data.get("id", "")).isdigit():
            raise OutcomeUnknown("x_response_unknown")
        return str(result.data["id"])


class MealClient:
    """固定みな境界の内部API。失われた応答を新UUIDで再送しない。"""

    def __init__(self, base_url: str, token: str):
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/") or len(token) < 32:
            raise ValueError("LogEatsのHTTPS接続設定が必要です")
        self.base, self.token = base_url.rstrip("/"), token

    def day_state(self, target):
        """専用読取APIが未導入なら停止する。不存在を空履歴とみなさない。"""
        from datetime import date
        from urllib.request import build_opener
        from .drive import NoRedirect
        if date.fromisoformat(target).isoformat() != target:
            raise ValueError('対象日が不正です')
        request = Request(self.base + '/api/internal/mina/state?' + urlencode({'date': target}),
                          headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json'})
        try:
            with build_opener(NoRedirect()).open(request, timeout=15) as response:
                raw = response.read(131073)
            result = json.loads(raw) if len(raw) <= 131072 else None
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except HTTPError as error:
            raise RuntimeError(f'meal_state_http_{error.code}') from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise RuntimeError('meal_state_read_failed') from None

    def request_state(self, request_key, operation):
        """UUIDの照会で404でも未登録とは判定せず、自動再送しない。"""
        if not isinstance(request_key, str):
            raise ValueError('保存済みの要求識別子が必要です')
        key = self.request_key(request_key)
        if operation not in ('meal_register', 'meal_correction', 'daily_share'):
            raise ValueError('要求の処理種別が不正です')
        request = Request(self.base + '/api/internal/mina/requests/' + key + '?' + urlencode({'operation': operation}),
                          headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json'})
        try:
            with build_opener(NoWriteRedirect()).open(request, timeout=15) as response:
                return read_json_response(response)
        except HTTPError as error:
            raise RuntimeError(f'meal_request_http_{error.code}') from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise RuntimeError('meal_request_read_failed') from None

    def verify_credentials(self):
        """冪等性キー欠落で書き込み前に止まる経路を使い、認証だけ検査する。"""
        from .ogp import SameHostRedirect
        from urllib.request import build_opener
        request = Request(self.base + '/api/internal/mina/meals', data=b'', method='POST',
                          headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/octet-stream'})
        try:
            with build_opener(SameHostRedirect(urlsplit(self.base).hostname)).open(request, timeout=15):
                raise RuntimeError('meal_preflight_unexpected_success')
        except HTTPError as error:
            # この400は認証後・設定検査やDB書き込み前に返される。本文はログへ出さない。
            if error.code == 400:
                try:
                    raw = error.read(4097)
                    response = json.loads(raw) if len(raw) <= 4096 else {}
                except (ValueError, OSError):
                    response = {}
                if isinstance(response, dict) and response.get('error') == 'Idempotency-KeyにはUUIDを指定してください。':
                    return {'token_accepted': True, 'registration_verified': False}
            raise RuntimeError(f'meal_preflight_http_{error.code}') from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise RuntimeError('meal_preflight_failed') from None

    @staticmethod
    def request_key(value=None):
        if value is None:
            raise ValueError('送信前に永続保存したリクエスト識別子が必要です')
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError('正規形式のリクエスト識別子が必要です')
        return value

    def json_write(self, path: str, payload: dict, method="POST", request_key=None):
        require_live()
        allowed = {("/api/internal/mina/daily-share", "POST"), ("/api/internal/mina/meals", "PATCH")}
        if (path, method) not in allowed:
            raise ValueError("許可されていない内部API操作です")
        from .m1_contract import CONTRACT, version
        if not isinstance(payload, dict) or payload.get('contract_version') != CONTRACT:
            raise ValueError('M1契約の明示が必要です。旧形式へ退避しません')
        version(payload.get('expected_state_version'), 'sv1')
        return request_json(self.base + path, payload,
                            {"Authorization": f"Bearer {self.token}", "Idempotency-Key": self.request_key(request_key)}, method, meal=True)

    def register(self, image: bytes, filename: str, meal_type: str, logged_at: str, comment: str, request_key=None, expected_state_version=None):
        """UTF-8のmultipartを送信し、品質結果を呼出元へ返す。"""
        require_live()
        if not image or len(image) > 4 * 1024 * 1024 or not image.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")):
            raise ValueError("4MB以下のPNG/JPEGが必要です")
        if meal_type not in ("breakfast", "lunch", "dinner") or len(comment) > 120 or "\n" in comment or "\r" in comment:
            raise ValueError("食事区分または一言が不正です")
        from .m1_contract import CONTRACT, version
        version(expected_state_version, 'sv1')
        from .domain import instant
        from datetime import datetime
        instant(datetime.fromisoformat(logged_at))
        boundary = uuid.uuid4().hex
        pieces = []
        for name, value in {"meal_type": meal_type, "logged_at": logged_at, "comment": comment,
                            'contract_version': CONTRACT, 'expected_state_version': expected_state_version}.items():
            pieces.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8"))
        mime = "image/png" if image.startswith(b"\x89PNG") else "image/jpeg"
        if not isinstance(filename, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', filename):
            raise ValueError('画像ファイル名が不正です')
        pieces.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode(), image, f'\r\n--{boundary}--\r\n'.encode()])
        request = Request(self.base + "/api/internal/mina/meals", data=b"".join(pieces), headers={
            "Authorization": f"Bearer {self.token}", "Idempotency-Key": self.request_key(request_key),
            "Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
        try:
            with urlopen(request, timeout=120) as response:
                return read_json_response(response)
        except HTTPError as error:
            raise meal_http_failure(error) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise OutcomeUnknown("meal_write_unknown") from None
