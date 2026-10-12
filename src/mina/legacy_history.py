"""実X文面を改変せず、初回履歴確認の資料へ変換する。"""

import hashlib
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
