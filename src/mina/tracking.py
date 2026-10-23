"""正本URLを保持し、承認前に固定したX計測URLだけを公開する。"""
import os
import re
from datetime import date
from urllib.parse import urlencode, urlsplit, urlunsplit

POLICY = {'version': 'mina-x-utm-v1', 'source': 'x', 'medium': 'social', 'campaign': 'mina_daily'}


def configured_policy():
    """設定は新しい候補の作成時だけ参照し、既存候補の本文は変えない。"""
    value = os.getenv('MINA_X_UTM_ENABLED', 'false').lower()
    if value not in ('true', 'false'):
        raise ValueError('X計測URLの有効設定が不正です')
    return dict(POLICY) if value == 'true' else None


def published_url(candidate):
    source = candidate.approval_source or {}
    if 'url_tracking' not in source:
        return candidate.share_url
    if source['url_tracking'] != POLICY or candidate.publish_text is None:
        raise ValueError('承認されたX計測URLの設定が不正です')
    if date.fromisoformat(candidate.target_date).isoformat() != candidate.target_date or candidate.slot not in ('breakfast', 'lunch', 'dinner', 'daily'):
        raise ValueError('X計測URLの対象日・投稿枠が不正です')
    url = urlsplit(candidate.share_url)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password or url.port not in (None, 443)
            or url.query or url.fragment or not re.fullmatch(r'/s/[a-z0-9_-]{4,32}', url.path)):
        raise ValueError('X計測URLの正本が不正です')
    query = urlencode({'utm_source': 'x', 'utm_medium': 'social', 'utm_campaign': 'mina_daily',
                       'utm_content': candidate.target_date + '_' + candidate.slot})
    return urlunsplit((url.scheme, url.netloc, url.path, query, ''))


def render(candidate):
    """完成本文の末尾URLだけを置換し、API固定本文と正本は改変しない。"""
    canonical = candidate.source_content
    url = published_url(candidate)
    if url == candidate.share_url:
        return canonical
    expected = candidate.comment + '\n\n' + candidate.api_text + '\n' + candidate.share_url
    if canonical != expected or canonical.count(candidate.share_url) != 1:
        raise ValueError('X計測URLへ変更する完成本文が不正です')
    return canonical[:-len(candidate.share_url)] + url


def verify_tracked_ogp(candidate, host, capture, canonical):
    """計測URLでも共有先と実OGPが一致することを公開直前に確認する。"""
    if candidate.published_share_url == candidate.share_url:
        return
    tracked = capture(candidate.published_share_url, host)
    expected = canonical['meta']
    actual = tracked['meta']
    if {k: v for k, v in actual.items() if k != 'page_url'} != {k: v for k, v in expected.items() if k != 'page_url'}:
        raise ValueError('X計測URLのOGPが正本と一致しません')
    original, final = urlsplit(expected.get('page_url', '')), urlsplit(actual.get('page_url', ''))
    source = urlsplit(candidate.share_url)
    for parsed in (original, final):
        if (parsed.scheme != 'https' or parsed.hostname != host or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.fragment):
            raise ValueError('X計測URLの共有先を確認できません')
    from urllib.parse import parse_qsl
    allowed = parse_qsl(urlsplit(candidate.published_share_url).query, strict_parsing=True)
    if final.path not in (source.path, original.path) or parse_qsl(final.query, strict_parsing=True) not in ([], allowed):
        raise ValueError('X計測URLが別の共有先へ転送されました')
