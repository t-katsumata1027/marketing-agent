"""X履歴を全ページ確認し、公開台帳と照合する。予約不存在は推測しない。"""

import re
from datetime import date, datetime, timedelta

from .domain import ACCOUNT, JST, instant


def collect_timeline(fetch_page, account_id, target, *, max_pages=40):
    """注入された読取処理を使う。ページ欠落・重複・上限到達で停止する。"""
    day = date.fromisoformat(target)
    if day.isoformat() != target or not isinstance(account_id, str) or not account_id.isdigit():
        raise ValueError('履歴の対象が不正です')
    start = datetime.combine(day - timedelta(days=14), datetime.min.time(), JST)
    end = datetime.combine(day, datetime.min.time(), JST)
    params = {'start_time': instant(start).isoformat(), 'end_time': instant(end).isoformat(),
              'max_results': 100, 'tweet_fields': ['author_id', 'created_at', 'entities', 'note_tweet']}
    posts, identifiers, tokens = [], set(), set()
    for _ in range(max_pages):
        try:
            page = fetch_page(account_id, **params)
            if not isinstance(page, dict) or page.get('errors'):
                raise ValueError()
            data, meta = page.get('data', []), page['meta']
            if not isinstance(data, list) or not isinstance(meta, dict) or type(meta.get('result_count')) is not int or meta['result_count'] != len(data):
                raise ValueError()
            for post in data:
                key = post['id']
                created = instant(datetime.fromisoformat(post['created_at']))
                if not isinstance(key, str) or not key.isdigit() or key in identifiers or post['author_id'] != account_id or not instant(start) <= created < instant(end):
                    raise ValueError()
                canonical_text(post)
                identifiers.add(key)
                posts.append(post)
                # 取得可能件数の境界では、終端を全履歴の証明にしない。
                if len(posts) >= 3200:
                    raise ValueError()
            token = meta.get('next_token')
            if token is None:
                return {'account': ACCOUNT, 'target_date': target, 'posts': posts,
                        'timeline_pages_complete': True, 'legacy_schedule_verified': False}
            if not isinstance(token, str) or not token or token in tokens or not data:
                raise ValueError()
            tokens.add(token)
            params['pagination_token'] = token
        except Exception:
            # SDK例外には認証値や応答本文が含まれる可能性がある。
            raise ValueError('X履歴を完全に取得できません') from None
    raise ValueError('X履歴のページ上限に達しました')


def canonical_text(post):
    """短縮URLを明示された展開URLへ戻す。文字位置や省略本文を推測しない。"""
    text = post.get('text')
    if not isinstance(text, str) or not text or post.get('note_tweet'):
        raise ValueError('X本文を照合できません')
    entities = post.get('entities', {})
    if not isinstance(entities, dict) or not isinstance(entities.get('urls', []), list):
        raise ValueError('X URL情報が不正です')
    replacements = {}
    for item in entities.get('urls', []):
        short, expanded = item.get('url'), item.get('expanded_url')
        if not isinstance(short, str) or not re.fullmatch(r'https://t\.co/[A-Za-z0-9]+', short) or not isinstance(expanded, str) or not expanded.startswith('https://') or any(c.isspace() for c in expanded) or short in replacements or text.count(short) != 1:
            raise ValueError('X URLを照合できません')
        replacements[short] = expanded
    return re.sub(r'https://t\.co/[A-Za-z0-9]+', lambda match: replacements.get(match[0], match[0]), text)


def reconcile_timeline(snapshot, records):
    """公開IDと完成本文を突合し、外部投稿・消失・改変を保留理由へ分ける。"""
    if snapshot.get('account') != ACCOUNT or snapshot.get('timeline_pages_complete') is not True:
        raise ValueError('完全な履歴読取が必要です')
    expected = {}
    for row in records:
        key = row.get('x_id')
        if not isinstance(key, str) or not key.isdigit() or key in expected or not isinstance(row.get('publish_text'), str) or not row['publish_text']:
            raise ValueError('公開台帳の対応が不正です')
        expected[key] = row['publish_text']
    actual = {}
    for post in snapshot['posts']:
        key = post['id']
        if key in actual:
            raise ValueError('X履歴IDが重複しています')
        actual[key] = canonical_text(post)
    unknown = sorted(actual.keys() - expected.keys())
    missing = sorted(expected.keys() - actual.keys())
    changed = sorted(key for key in expected.keys() & actual.keys() if expected[key] != actual[key])
    return {'timeline_matches_ledger': not (unknown or missing or changed),
            'external_post_ids': unknown, 'missing_post_ids': missing, 'changed_post_ids': changed,
            'legacy_schedule_verified': False, 'production_start_allowed': False}
