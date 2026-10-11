"""M1契約の利用側検証。草案の変更はここへ集約し、旧形式へ自動退避しない。"""

import re
import math
import time
import unicodedata
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

from .domain import ACCOUNT, instant
from .meal_results import validate_daily_response, validate_meal_response
from .preparation import fingerprint


CONTRACT = 'mina-write-v1'
STAGES = frozenset(('received', 'reserved', 'analyzed', 'saved', 'ogp_preparing', 'ogp_failed',
                    'ogp_source_check', 'completed', 'legacy', 'failed', 'precheck', 'save_check',
                    'save', 'ogp', 'target', 'quality'))
REASONS = frozenset(('state_conflict', 'slot_conflict', 'day_composition_invalid', 'source_changed',
                     'duplicate_request_same_input', 'duplicate_request_different_input', 'duplicate_request_legacy',
                     'og_image_preparation_failed', 'commit_outcome_unknown', 'request_not_found',
                     'quality_review_required', 'quota_exceeded', 'account_not_ready', 'account_unavailable',
                     'meal_not_found', 'share_missing', 'source_check_failed', 'state_record_invalid',
                     'response_too_large', 'internal_error', 'legacy_request_untracked'))


def safe_write_state(payload):
    """エラー原文や識別子を捨て、固定の状態分類だけを保存する。"""
    if not isinstance(payload, dict):
        payload = {}
    def classified(key, values, fallback):
        value = payload.get(key)
        return value if isinstance(value, str) and value in values else fallback
    return {'persistence': classified('persistence', ('not_saved', 'saved', 'unknown'), 'unknown'),
            'stage': classified('stage', STAGES, 'unrecognized'),
            'reason': None if payload.get('reason') is None else classified('reason', REASONS, 'unrecognized')}


def version(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(prefix + r'_[0-9a-f]{64}', value):
        raise ValueError('正本の版・ハッシュ形式が不正です')
    return value


def normalized_comment(value):
    """契約草案のNFCとECMAScriptの前後空白除去に合わせる。"""
    if not isinstance(value, str):
        raise ValueError('コメントが不正です')
    whitespace = ' \t\n\r\v\f\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
    result = unicodedata.normalize('NFC', value.replace('\r\n', '\n').replace('\r', '\n').strip(whitespace))
    if '\n' in result or len(result) > 120:
        raise ValueError('正規化コメントが不正です')
    return result


def validate_state(state, target, now):
    """完全な新契約応答だけを採用し、取得不能・部分応答を空日と扱わない。"""
    try:
        age = (instant(now) - instant(datetime.fromisoformat(state['checked_at']))).total_seconds()
        account = state['account']
        if account != ACCOUNT:
            raise ValueError()
        if (state['contract_version'] != CONTRACT or state['complete'] is not True or state['date'] != target or
                date.fromisoformat(target).isoformat() != target or not 0 <= age <= 120 or
                not isinstance(state['meals'], list) or type(state['meal_count']) is not int or
                state['meal_count'] != len(state['meals']) or len(state['meals']) > 30):
            raise ValueError()
        return version(state['state_version'], 'sv1')
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError('M1正本照会の完全性・鮮度・対象を確認できません') from None


def validate_ogp(result, host, daily=False, now=None):
    try:
        ogp = result['ogp']
        url = urlsplit(ogp['image_url'])
        if (ogp['status'] != 'ready' or not isinstance(ogp['revision'], str) or not 1 <= len(ogp['revision']) <= 200 or
                url.scheme != 'https' or url.hostname != host or url.username or url.password or
                url.port not in (None, 443) or url.fragment):
            raise ValueError()
        if not re.fullmatch(r'[0-9a-f]{64}', ogp['image_sha256']):
            raise ValueError()
        source = result['source_state_version'] if daily else result['result_version']
        version(source, 'sv1' if daily else 'rv1')
        if ogp['source_state_version' if daily else 'source_result_version'] != source:
            raise ValueError()
        expires = instant(datetime.fromisoformat(ogp['expires_at']))
        if expires <= instant(now or datetime.now(UTC)):
            raise ValueError()
        return ogp
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError('M1のOGP実画像・生成元版・有効期限を確認できません') from None


def validate_result(result, slot, target, host, logged_at=None, input_comment=None, meals=None, now=None):
    """サーバーの完成本文を機械照合し、そのまま承認対象へ固定する。"""
    try:
        comment = result['normalized_comment']
        version(result['result_version'], 'rv1')
        if normalized_comment(comment) != comment or (input_comment is not None and normalized_comment(input_comment) != comment):
            raise ValueError()
        if slot == 'daily':
            normalized = validate_daily_response(result, target, comment, host, meals)
        else:
            if any(not isinstance(result.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', result[key])
                   for key in ('image_sha256', 'stored_image_sha256')):
                raise ValueError()
            normalized = validate_meal_response(result, slot, logged_at, comment, host)
        expected = result['x']['text'] + '\n' + normalized['share_url']
        if result['x']['fixed_text'] != normalized['api_text'] or result['x']['publish_text'] != expected:
            raise ValueError()
        ogp = validate_ogp(result, host, slot == 'daily', now)
        return {**normalized, 'publish_text': expected, 'result_sha256': fingerprint(result),
                'ogp': ogp, 'source_version': result['source_state_version'] if slot == 'daily' else result['result_version']}
    except (KeyError, TypeError, ValueError):
        raise ValueError('M1の正本結果・正規化コメント・完成本文が不整合です') from None


def validate_write(response, slot, target, host, **kwargs):
    """互換コピーが新resultと違う場合や、旧応答だけの場合は止める。"""
    try:
        result = response['result']
        keys = ('date', 'meal_count', 'summary', 'share', 'x') if slot == 'daily' else ('analysis', 'meal', 'quality', 'share', 'x')
        for key in keys:
            if key == 'x':
                if any(response[key][field] != result[key][field] for field in ('text', 'intent_url')):
                    raise ValueError()
            elif response[key] != result[key]:
                raise ValueError()
        return validate_result(result, slot, target, host, **kwargs)
    except (KeyError, TypeError, ValueError):
        raise ValueError('M1応答と既存互換フィールドの一致を確認できません') from None


def completed_request(response, request_key, operation, result):
    """現在正本と保存時の結果を混同せず、完了済み要求の証跡を照合する。"""
    request_report(response, request_key, operation)
    if (response['status'] != 'succeeded' or response['persistence'] != 'saved' or
            response.get('stage') != 'completed' or response['result_status'] != 'unchanged' or
            response.get('result_hold_reason', 'missing') is not None or
            not isinstance(response.get('current_result'), dict) or fingerprint(response['current_result']) != fingerprint(result)):
        raise ValueError('M1要求の現在正本と点検対象が一致しません')
    return version(response['input_hash'], 'ih1')


def request_report(response, request_key, operation):
    """成否照会の診断は許可した分類だけを返し、正本や識別子を表示しない。"""
    if (response.get('contract_version') != CONTRACT or response.get('request_uuid') != request_key or
            response.get('operation') != operation):
        raise ValueError('M1要求照会の対象が不一致です')
    allowed = {'status': {'processing', 'succeeded', 'failed', 'reconciliation_required'},
               'persistence': {'not_saved', 'saved', 'unknown'},
               'result_status': {'unchanged', 'modified', 'deleted', 'unresolved'}}
    report = {}
    for field, values in allowed.items():
        if response.get(field) not in values:
            raise ValueError('M1要求照会の状態分類が不正です')
        report[field] = response[field]
    return {**report, 'result_available': isinstance(response.get('result'), dict),
            'stage': safe_write_state(response)['stage'], 'reason': safe_write_state(response)['reason'],
            'automatic_retry_allowed': False, 'ledger_updated': False}


def verify_source(candidate, client, host, capture, now=None, clock=None, timer=time.monotonic):
    """公開直前に日全体・UUID正本・OGP実体を再照合する。照会だけを行う。"""
    binding = candidate.approval_source
    if not isinstance(binding, dict):
        raise ValueError('M1の承認元証跡がありません')
    # nowは固定時刻の試験用。実運用はGET完了後に時計を読み直す。
    clock = clock or ((lambda: now) if now is not None else (lambda: datetime.now(UTC)))
    started_monotonic = timer()
    started = instant(clock())
    state = client.day_state(candidate.target_date)
    received = instant(clock())
    current = validate_state(state, candidate.target_date, received)
    if current != binding['state_version'] or state['meal_count'] != 3:
        raise ValueError('承認後に日全体の正本が変更されています')
    operation = binding.get('operation', 'daily_share' if candidate.slot == 'daily' else 'meal_register')
    if operation not in (('daily_share',) if candidate.slot == 'daily' else ('meal_register', 'meal_correction')):
        raise ValueError('承認対象の処理種別が不正です')
    response = client.request_state(binding['request_key'], operation)
    if (response.get('contract_version') != CONTRACT or response.get('operation') != operation or
            response.get('request_uuid') != binding['request_key'] or response.get('status') != 'succeeded' or
            response.get('persistence') != 'saved' or response.get('result_status') != 'unchanged' or
            response.get('current_state_version') != current or response.get('input_hash') != binding['input_hash']):
        raise ValueError('要求の成否・正本変更を確認できません')
    result = response.get('current_result')
    completed_request(response, binding['request_key'], operation, result)
    if not isinstance(result, dict) or fingerprint(result) != binding['result_sha256']:
        raise ValueError('承認後に対象の正本結果が変更されています')
    if result['normalized_comment'] != candidate.comment or result['x']['fixed_text'] != candidate.api_text or result['x']['publish_text'] != candidate.content:
        raise ValueError('承認した完成本文が一致しません')
    if 'result_version' in binding:
        if binding['result_version'] != result['result_version'] or binding.get('operation') != operation:
            raise ValueError('承認した処理種別・個別版が不一致です')
        if candidate.slot != 'daily' and (binding.get('stored_image_sha256') != result.get('stored_image_sha256') or
                                          candidate.image_sha256 != result.get('image_sha256')):
            raise ValueError('承認した原本・保存画像が不一致です')
    ogp = validate_ogp(result, host, candidate.slot == 'daily', clock())
    if 'result_version' in binding and (binding.get('ogp_revision') != ogp['revision'] or
            binding.get('ogp_source_version') != ogp['source_state_version' if candidate.slot == 'daily' else 'source_result_version']):
        raise ValueError('承認したOGPの版・生成元版が不一致です')
    snapshot = capture(candidate.share_url, host)
    if (snapshot['digest'] != candidate.ogp_sha256 or snapshot['meta']['og:image'] != ogp['image_url'] or
            snapshot['meta']['image_sha256'] != ogp['image_sha256']):
        raise ValueError('承認したOGPと現在の実画像が一致しません')
    finished = instant(clock())
    finished_monotonic = timer()
    elapsed = finished_monotonic - started_monotonic
    if not math.isfinite(elapsed) or not 0 <= elapsed <= 120:
        raise ValueError('公開前照合の単調時計の時間窓を超えています')
    if not 0 <= (finished - started).total_seconds() <= 120:
        raise ValueError('公開前照合の時間窓を超えています')
    validate_state(state, candidate.target_date, finished)
    validate_ogp(result, host, candidate.slot == 'daily', finished)
    return {'started_at': started.isoformat(), 'checked_at': state['checked_at'],
            'observed_at': finished.isoformat(), 'expires_at': ogp['expires_at'],
            'started_monotonic': started_monotonic, 'observed_monotonic': finished_monotonic}
