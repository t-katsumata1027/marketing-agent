"""Instagram専用の承認対象。Xの投稿本文・承認とは分離する。"""

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
import math
import re
from urllib.parse import urlsplit

from .domain import SLOTS, instant, slot_time

HANDLE = 'log_eats'
LAYOUT_VERSION = 'mina-instagram-fixed-v3'
KEYS = ('calories', 'protein', 'fat', 'carbs')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def sha(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('画像・正本のハッシュが不正です')
    return value


def nutrition(value):
    if (not isinstance(value, dict) or set(value) != set(KEYS) or
            any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                or not 0 <= v <= 10000 for v in value.values())):
        raise ValueError('カロリー・PFCの正本が必要です')
    return value


def media_url(url, hosts):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in hosts or parsed.username or
            parsed.password or parsed.port not in (None, 443) or parsed.fragment):
        raise ValueError('画像配信先が許可済みHTTPSと一致しません')
    return url


@dataclass(frozen=True)
class InstagramCandidate:
    target_date: str
    slot: str
    channel_id: str
    account_id: str
    format: str
    caption: str
    assets: list
    source: dict
    is_ai_generated: bool = True
    handle: str = HANDLE
    layout_version: str = LAYOUT_VERSION

    @property
    def scheduled_at(self):
        return slot_time(date.fromisoformat(self.target_date), self.slot)

    @property
    def digest(self):
        return digest(asdict(self))

    def validate(self, now, hosts, require_future=True):
        if self.handle != HANDLE or self.slot not in SLOTS or not self.channel_id or not self.account_id:
            raise ValueError('Instagramの投稿先と枠を固定してください')
        if self.layout_version != LAYOUT_VERSION or type(self.is_ai_generated) is not bool:
            raise ValueError('レイアウト版と開示設定が不正です')
        if require_future and self.scheduled_at <= instant(now):
            raise ValueError('投稿時刻を過ぎています')
        expected = 'post' if self.slot == 'daily' else 'story'
        if self.format != expected or len(self.assets) != (5 if self.slot == 'daily' else 1):
            raise ValueError('投稿形式と画像の枚数が一致しません')
        if not isinstance(self.caption, str) or len(self.caption) > 2200:
            raise ValueError('Instagram本文が長すぎます')
        for asset in self.assets:
            sha(asset['sha256'])
            media_url(asset['url'], hosts)
            if (asset.get('mime_type') != 'image/jpeg' or asset.get('width') != 1080 or
                    asset.get('height') != (1350 if self.slot == 'daily' else 1920)):
                raise ValueError('Instagram画像の規格が不正です')
        sha(self.source['bundle_sha256'])
        if set(self.source.get('x_digests', {})) != set(SLOTS):
            raise ValueError('X用4枠と同じ正本の対応が必要です')
        for value in self.source['x_digests'].values():
            sha(value)
        if not re.fullmatch(r'sv1_[0-9a-f]{64}', self.source.get('state_version', '')):
            raise ValueError('Log-Eatsの日版が必要です')


def publication_url(value, format):
    parsed = urlsplit(value or '')
    if (parsed.scheme != 'https' or parsed.hostname not in ('instagram.com', 'www.instagram.com')
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.query or parsed.fragment):
        raise ValueError('Instagramの公開先URLを確認できません')
    pattern = r'/stories/' + HANDLE + r'/[0-9]+/?' if format == 'story' else r'/p/[A-Za-z0-9_-]+/?'
    if not re.fullmatch(pattern, parsed.path):
        raise ValueError('投稿形式とInstagramの公開先が一致しません')
    return value
