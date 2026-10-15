"""公開直前の共有ページとOGPの版照合。"""

import hashlib
import io
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit, parse_qsl
from urllib.request import HTTPRedirectHandler, Request, build_opener


class MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            attrs = dict(attrs)
            key = attrs.get("property") or attrs.get("name")
            if key in ("og:title", "og:description", "og:image"):
                self.meta[key] = attrs.get("content", "")


class SameHostRedirect(HTTPRedirectHandler):
    def __init__(self, host):
        self.host = host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname != self.host or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError("共有ページが許可外へ転送されました")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def capture_ogp(url: str, host: str) -> dict:
    """X向けHTMLとOGP画像の画素を取得し、同じURLの画像差し替えも検出する。"""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != host or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("共有ページの接続先が不正です")
    opener = build_opener(SameHostRedirect(host))
    with opener.open(Request(url, headers={"User-Agent": "Twitterbot/1.0 LogEats-Mina-Review/1.0"}), timeout=15) as response:
        if "text/html" not in response.headers.get("Content-Type", ""):
            raise ValueError("共有ページがHTMLではありません")
        html = response.read(1024 * 1024 + 1)
        page_url = response.geturl()
        if not isinstance(page_url, str):
            page_url = url
        if len(html) > 1024 * 1024:
            raise ValueError("共有ページが大きすぎます")
    parser = MetaParser()
    parser.feed(html.decode("utf-8"))
    if any(not parser.meta.get(k) for k in ("og:title", "og:description", "og:image")):
        raise ValueError("OGPの必須項目がありません")
    image = urlsplit(parser.meta["og:image"])
    if (image.scheme != "https" or image.hostname != host or image.username or image.password or
            image.port not in (None, 443) or image.fragment):
        raise ValueError("OGP画像URLが不正です")
    with opener.open(Request(parser.meta['og:image'], headers={'User-Agent': 'LogEats-Mina-Review/1.0'}), timeout=30) as response:
        mime = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
        if mime not in ('image/jpeg', 'image/png', 'image/webp'):
            raise ValueError('OGP応答が画像ではありません')
        raw = response.read(4 * 1024 * 1024 + 1)
    if not raw or len(raw) > 4 * 1024 * 1024:
        raise ValueError('OGP画像の容量が不正です')
    from PIL import Image
    expected = {'image/jpeg': 'JPEG', 'image/png': 'PNG', 'image/webp': 'WEBP'}[mime]
    try:
        with Image.open(io.BytesIO(raw)) as pixels:
            width, height = pixels.size
            if pixels.format != expected or not 600 <= width <= 2400 or not 300 <= height <= 2400 or width * height > 5760000:
                raise ValueError()
            pixels.verify()
        with Image.open(io.BytesIO(raw)) as pixels:
            preview = io.BytesIO()
            pixels.convert('RGB').save(preview, format='JPEG', quality=90)
    except Exception:
        raise ValueError('OGP画像の画素を確認できません') from None
    meta = {**parser.meta, 'page_url': page_url, 'image_sha256': hashlib.sha256(raw).hexdigest(),
            'image_size': len(raw), 'image_width': width, 'image_height': height}
    digest = hashlib.sha256(json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {'meta': meta, 'digest': digest, 'review_image': preview.getvalue()}


def verify_image_reference(snapshot, ogp, share_url, host):
    """短縮IDと長い共有IDの正規別名は、両実体の同一SHAを確認して採用する。"""
    actual, expected = snapshot['meta']['og:image'], ogp['image_url']
    if actual == expected:
        return
    short = urlsplit(share_url)
    api, html, page = map(urlsplit, (expected, actual, snapshot['meta'].get('page_url', '')))
    for parsed in (short, api, html, page):
        if parsed.scheme != 'https' or parsed.hostname != host or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment:
            raise ValueError('OGP別名の接続先が不正です')
    if (not re.fullmatch(r'/s/[a-z0-9_-]{4,32}', short.path) or short.query or
            api.path != '/share/' + short.path.rsplit('/', 1)[-1] + '/og-image' or
            not re.fullmatch(r'/share/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', page.path) or page.query or
            html.path != page.path + '/og-image' or
            parse_qsl(api.query) != [('v', ogp['revision'])] or
            parse_qsl(html.query) != [('v', ogp['revision'])]):
        raise ValueError('OGPの正規別名・内容版が一致しません')
    # 文字列の別名だけで同一と推測せず、API側URLのPNGも毎回再取得する。
    with build_opener(SameHostRedirect(host)).open(Request(expected, headers={'User-Agent':'LogEats-Mina-Review/1.0'}), timeout=30) as response:
        if response.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'image/png':
            raise ValueError('API側OGPがPNGではありません')
        raw = response.read(4 * 1024 * 1024 + 1)
    if (not raw or len(raw) > 4 * 1024 * 1024 or
            hashlib.sha256(raw).hexdigest() != ogp['image_sha256'] or
            snapshot['meta']['image_sha256'] != ogp['image_sha256']):
        raise ValueError('OGP別名の実PNGが一致しません')


def ogp_snapshot(url: str, host: str) -> tuple[dict, str]:
    """公開前の版検査も実画像を含む同じ方式で行う。"""
    result = capture_ogp(url, host)
    return result['meta'], result['digest']


def verify(candidate, host: str):
    _, digest = ogp_snapshot(candidate.share_url, host)
    if digest != candidate.ogp_sha256:
        raise ValueError("承認後にOGPが変更されています")
