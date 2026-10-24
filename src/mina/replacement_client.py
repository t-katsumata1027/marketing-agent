"""mina-replace-v1の読取専用接続部。確定Schemaの検証器なしでは通信しない。"""

import base64
from dataclasses import dataclass
from datetime import UTC, date, datetime
import hashlib
import hmac
from io import BytesIO
import json
import math
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from PIL import Image

from .clients import ClockSkewError, MealClient, await_checked_at, urlopen


class ReplacementReadFailure(ValueError):
    def __init__(self, reason, http_status=None):
        self.reason, self.http_status = reason, http_status
        super().__init__(reason)


@dataclass(frozen=True)
class ImageDescriptor:
    """確定Schema検証器が取り出す内部形式。提供側のJSON形式とは区別する。"""
    candidate_version: str
    sha256: str
    byte_length: int
    mime_type: str

    def validate(self):
        if (not re.fullmatch(r'cv1_[0-9a-f]{64}', self.candidate_version)
                or not re.fullmatch(r'[0-9a-f]{64}', self.sha256)
                or type(self.byte_length) is not int or not 0 < self.byte_length <= 25*1024*1024
                or self.mime_type not in ('image/jpeg', 'image/png', 'image/webp')):
            raise ReplacementReadFailure('replacement_image_descriptor_invalid')


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z', value):
        raise ReplacementReadFailure('replacement_timestamp_invalid')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('重複キーは受け付けません')
        result[key] = value
    return result


def json_number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('有限の数値が必要です')
    return result


class ReplacementReadClient:
    """P/D/Cの送信メソッドを持たず、404や通信失敗を未保存へ変換しない。"""
    def __init__(self, base_url, token, decoder=None, opener=urlopen, clock=lambda: datetime.now(UTC), sleep=time.sleep):
        # 既存と同じHTTPS・Bearer設定検査だけを再利用し、M1契約は変更しない。
        config = MealClient(base_url, token)
        self.base, self.token = config.base, config.token
        if '\r' in token or '\n' in token:
            raise ReplacementReadFailure('replacement_credentials_invalid')
        self.decoder, self.opener, self.clock = decoder, opener, clock
        self.sleep = sleep
        if decoder is not None and not callable(decoder):
            raise ReplacementReadFailure('replacement_contract_unavailable')

    def route(self, path):
        """通常契約の経路。別契約は明示した専用clientでだけ差し替える。"""
        return path

    def _get(self, path, accept, headers=None):
        if self.decoder is None:
            raise ReplacementReadFailure('replacement_contract_unavailable')
        request = Request(self.base+self.route(path), method='GET', headers={
            'Authorization':'Bearer '+self.token, 'Accept':accept, 'Cache-Control':'no-store', **(headers or {})})
        return self.opener(request, timeout=15)

    def _json(self, path, kind):
        try:
            with self._get(path, 'application/json') as response:
                if response.status != 200:
                    raise ReplacementReadFailure('replacement_lookup_unknown', response.status)
                raw = response.read(131073)
            if len(raw) > 131072:
                raise ReplacementReadFailure('replacement_response_invalid')
            payload = json.loads(raw, object_pairs_hook=json_object, parse_float=json_number,
                                 parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if isinstance(payload,dict) and isinstance(payload.get('freshness'),dict):
                await_checked_at(payload['freshness'],clock=self.clock,sleep=self.sleep,max_ahead=5)
            # 鮮度・完全性・契約版・UUID対応は提供側Schemaの確定後に接続する検証器で確認する。
            return self.decoder(kind, payload)
        except ReplacementReadFailure:
            raise
        except HTTPError as error:
            raise ReplacementReadFailure('replacement_lookup_unknown', error.code) from None
        except (URLError, OSError, TimeoutError):
            raise ReplacementReadFailure('replacement_lookup_unknown') from None
        except (ValueError, KeyError, TypeError):
            raise ReplacementReadFailure('replacement_response_invalid') from None

    def preconditions(self, target):
        if date.fromisoformat(target).isoformat() != target:
            raise ReplacementReadFailure('replacement_date_invalid')
        return self._json('/api/internal/mina/meal-replacement-preconditions?'+urlencode({'date':target}), 'preconditions')

    def candidate(self, prepare_uuid):
        key = MealClient.request_key(prepare_uuid)
        return self._json('/api/internal/mina/meal-replacement-candidates/'+key, 'candidate')

    def request(self, key, operation):
        key = MealClient.request_key(key)
        if operation not in ('candidate_prepare', 'candidate_discard', 'meal_replace', 'replacement_resume'):
            raise ReplacementReadFailure('replacement_operation_invalid')
        if operation == 'replacement_resume':
            return self._json('/api/internal/mina/meal-replacement-resumes/'+key, operation)
        prefix = '/api/internal/mina/requests/' if operation == 'meal_replace' else '/api/internal/mina/meal-replacement-requests/'
        return self._json(prefix+key+'?'+urlencode({'operation':operation}), operation)

    def image(self, prepare_uuid, kind, expected):
        """受信完了・実SHA・形式・候補版を検査する。画像の独立点検合格とは扱わない。"""
        key = MealClient.request_key(prepare_uuid)
        if kind not in ('original', 'stored') or not isinstance(expected, ImageDescriptor):
            raise ReplacementReadFailure('replacement_image_kind_invalid')
        expected.validate()
        if kind == 'stored' and expected.mime_type != 'image/webp':
            raise ReplacementReadFailure('replacement_image_descriptor_invalid')
        try:
            path = '/api/internal/mina/meal-replacement-candidates/'+key+'/images/'+kind
            with self._get(path, expected.mime_type, {'Candidate-Version':expected.candidate_version}) as response:
                headers = response.headers
                encoded = base64.b64encode(bytes.fromhex(expected.sha256)).decode('ascii')
                if (response.status != 200 or headers.get('Content-Range') is not None
                        or headers.get('Content-Encoding', 'identity') != 'identity'
                        or headers.get('Content-Type') != expected.mime_type
                        or headers.get('Content-Length') != str(expected.byte_length)
                        or headers.get('Content-Digest') != 'sha-256=:'+encoded+':'
                        or headers.get('X-Mina-Candidate-Version') != expected.candidate_version
                        or headers.get('X-Mina-Image-SHA256') != expected.sha256
                        or headers.get('X-Mina-Complete') != 'true'):
                    raise ReplacementReadFailure('replacement_image_headers_invalid')
                checked, until = timestamp(headers.get('X-Mina-Checked-At')), timestamp(headers.get('X-Mina-Valid-Until'))
                chunks, size = [], 0
                while True:
                    chunk = response.read(min(65536, expected.byte_length+1-size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > expected.byte_length:
                        raise ReplacementReadFailure('replacement_image_length_invalid')
                raw = b''.join(chunks)
            # APIの僅かな時計先行は実時間で待つ。未来の時刻を現在として採用しない。
            await_checked_at({'checked_at':checked.isoformat()}, clock=self.clock, sleep=self.sleep, max_ahead=5)
            now = self.clock()
            if (now.tzinfo is None or not checked <= now < until or (now-checked).total_seconds() > 120
                    or len(raw) != expected.byte_length
                    or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected.sha256)):
                raise ReplacementReadFailure('replacement_image_integrity_invalid')
            with Image.open(BytesIO(raw)) as image:
                if Image.MIME.get(image.format) != expected.mime_type:
                    raise ReplacementReadFailure('replacement_image_format_invalid')
                image.verify()
            with Image.open(BytesIO(raw)) as image:
                image.load()
            return raw
        except ReplacementReadFailure:
            raise
        except ClockSkewError:
            raise ReplacementReadFailure('replacement_image_clock_unconfirmed') from None
        except HTTPError as error:
            raise ReplacementReadFailure('replacement_lookup_unknown', error.code) from None
        except (URLError, OSError, TimeoutError, ValueError):
            raise ReplacementReadFailure('replacement_image_read_unknown') from None


class ReplacementClient(ReplacementReadClient):
    """明示許可と検証器がある送信だけを実行。再送・fallbackは持たない。"""
    def __init__(self,*args,write_authorized=False,**kwargs):
        super().__init__(*args,**kwargs)
        self.write_authorized=write_authorized

    def authorize_write(self, operation, body):
        import os
        if (not self.write_authorized or os.getenv('MINA_MEAL_REPLACEMENT_ENABLED','false').lower()!='true'
                or os.getenv('MINA_LIVE_WRITES','false').lower()!='true' or self.decoder is None):
            raise ReplacementReadFailure('replacement_write_disabled')

    def send(self,operation,key,body,content_type,prepare_uuid=None):
        self.authorize_write(operation,body)
        MealClient.request_key(key)
        if operation=='candidate_prepare':path='/api/internal/mina/meal-replacement-candidates'
        elif operation=='candidate_discard':path='/api/internal/mina/meal-replacement-candidates/'+MealClient.request_key(prepare_uuid)+'/discard'
        elif operation=='meal_replace':path='/api/internal/mina/meal-replacements'
        elif operation=='replacement_resume':path='/api/internal/mina/meal-replacement-resumes'
        else:raise ReplacementReadFailure('replacement_operation_invalid')
        request=Request(self.base+self.route(path),data=body,method='POST',headers={'Authorization':'Bearer '+self.token,
            'Cache-Control':'no-store','Accept':'application/json','Content-Type':content_type,'Idempotency-Key':key})
        response=None
        try:
            try:response=self.opener(request,timeout=60)
            except HTTPError as error:response=error
            raw=response.read(131073)
            if len(raw)>131072:raise ValueError()
            payload=json.loads(raw,object_pairs_hook=json_object,parse_float=json_number,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if isinstance(payload.get('freshness'),dict):await_checked_at(payload['freshness'],clock=self.clock,sleep=self.sleep,max_ahead=5)
            # HTTPだけで既存の成否を変更せず、元要求のenvelopeを検証する。
            return self.decoder(operation,payload)
        except ReplacementReadFailure:
            raise
        except (URLError,OSError,TimeoutError,ValueError,KeyError,TypeError):
            raise ReplacementReadFailure('replacement_write_lookup_required') from None
        finally:
            if response is not None:response.close()
