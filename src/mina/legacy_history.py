"""実X文面を改変せず、初回履歴確認の資料へ変換する。"""

import hashlib
import json
from datetime import datetime

from .domain import ACCOUNT, JST, instant
from .x_history import canonical_text


def bootstrap_draft(snapshot):
    """種別は提案だけ、献立は空欄。確認済みとは判定しない。"""
    if snapshot.get('account') != ACCOUNT or snapshot.get('timeline_pages_complete') is not True or not isinstance(snapshot.get('posts'), list):
        raise ValueError('検査済みのX履歴ページが必要です')
    entries, seen = [], set()
    for post in snapshot['posts']:
        key = post['id']
        if not isinstance(key, str) or not key.isdigit() or key in seen:
            raise ValueError('X投稿IDの対応が不正です')
        seen.add(key)
        text = canonical_text(post)
        suggested = ('daily' if '今日1日の食事まとめ' in text else
                     'meal' if '今日の食事解析結果' in text else 'other')
        entries.append({'x_id': key, 'date': instant(datetime.fromisoformat(post['created_at'])).astimezone(JST).date().isoformat(),
                        'kind': 'pending', 'suggested_kind': suggested, 'menu': '',
                        'comment': text.split('\n\n', 1)[0].strip() if suggested != 'other' else '',
                        'publish_text': text,
                        'source_text_sha256': hashlib.sha256(text.encode()).hexdigest()})
    return {'account': ACCOUNT, 'reviewed': False, 'reviewer': '', 'entries': entries,
            'source_observed_until': snapshot.get('read_until'),
            'review_required': ['各投稿の種別を確認してkindを確定する',
                                '食事・日次は献立説明と先頭コメントを原資料で確認する',
                                '交流投稿に架空の献立を追加しない',
                                '確認者と確認完了を記録する'],
            'production_start_allowed': False}


def confirm_legacy_history(snapshot, plan, results, reviewed_other_ids, reviewer):
    """旧献立表・保存応答・実公開本文の三者一致だけで初回対応を確定する。"""
    from .domain import FIXED_MARKERS
    draft = bootstrap_draft(snapshot)
    if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(plan, list) or not isinstance(results, list):
        raise ValueError('旧資料と確認者が必要です')
    menus, sources, lookup = {}, {}, {}
    for row in plan:
        key = (row['date'], row['meal'])
        if key in menus or key[1] not in ('breakfast','lunch','dinner') or any(not isinstance(row.get(k), str) or not row[k].strip() for k in ('menu','comment')):
            raise ValueError('旧献立表の対応が不正です')
        menus[key] = row
    for source in results:
        key = (source['date'], source['slot'])
        payload = source['payload']
        text, url = payload['x']['text'], payload['share']['url']
        if key in sources or key[1] not in ('breakfast','lunch','dinner','daily') or not isinstance(text, str) or not isinstance(url, str) or not text or not url.startswith('https://'):
            raise ValueError('旧応答の対応が不正です')
        published = text+'\n'+url
        if (key[0], published) in lookup:
            raise ValueError('旧応答の本文対応が重複しています')
        sources[key] = payload
        lookup[(key[0],published)] = key
    others = set(reviewed_other_ids)
    seen_other, used, entries = set(), set(), []
    for entry in draft['entries']:
        key = lookup.get((entry['date'], entry['publish_text']))
        if key is None:
            if entry['x_id'] not in others or entry['suggested_kind'] != 'other' or any(marker in entry['publish_text'] for marker in FIXED_MARKERS):
                raise ValueError('実投稿に一致する旧資料がありません')
            seen_other.add(entry['x_id'])
            entries.append({k: entry[k] for k in ('x_id','date','publish_text')} | {'kind':'other'})
            continue
        if key in used:
            raise ValueError('同じ旧結果に複数の実投稿が対応しています')
        used.add(key)
        comment = entry['publish_text'].split('\n\n',1)[0]
        if key[1] == 'daily':
            rows = [menus.get((key[0],slot)) for slot in ('breakfast','lunch','dinner')]
            if any(row is None for row in rows) or entry['suggested_kind'] != 'daily':
                raise ValueError('旧日次と3食の対応が不正です')
            menu = '1日まとめ：'+'／'.join(row['menu'] for row in rows)
            kind = 'daily'
        else:
            row = menus.get(key)
            if row is None or row['comment'] != comment or entry['suggested_kind'] != 'meal':
                raise ValueError('旧献立と実コメントが不一致です')
            menu, kind = row['menu'], 'meal'
        entries.append({k: entry[k] for k in ('x_id','date','publish_text')} |
                       {'kind':kind,'menu':menu,'comment':comment,
                        'source_result_sha256':hashlib.sha256(json.dumps(sources[key],sort_keys=True,ensure_ascii=False).encode()).hexdigest()})
    if seen_other != others:
        raise ValueError('交流投稿の確認対象が不一致です')
    return {'account':ACCOUNT,'reviewed':True,'reviewer':reviewer,'entries':entries,
            'source_plan_sha256':hashlib.sha256(json.dumps(plan,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
            'source_observed_until':snapshot.get('read_until'),'production_start_allowed':False}
