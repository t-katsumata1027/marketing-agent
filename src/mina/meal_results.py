"""LogEatsの正本応答を検証し、固定文を改変せずに取り出す。"""

import math
import re
from datetime import date, datetime
from urllib.parse import urlsplit

from .domain import FIXED_MARKERS, instant


SUMMARY_FIELDS = {'calories': 'totalCalories', 'protein': 'totalProtein', 'fat': 'totalFat', 'carbs': 'totalCarbs'}


def nutrition(summary):
    if not isinstance(summary, dict):
        raise ValueError('栄養集計がありません')
    result = {name: summary.get(field) for name, field in SUMMARY_FIELDS.items()}
    if any(isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value)
           or not 0 <= value <= 10000 for value in result.values()):
        raise ValueError('栄養集計に不正な値があります')
    n = result
    gap = abs(n['calories'] - (4 * n['protein'] + 9 * n['fat'] + 4 * n['carbs']))
    if gap > 50 and gap > max(n['calories'], 1) * .2:
        raise ValueError('API集計のカロリーとPFCが不整合です')
    return result


def share_and_text(response, host, comment, n, daily=False):
    try:
        url, text = response['share']['url'], response['x']['text']
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname != host or parsed.username or parsed.password or
                parsed.port not in (None, 443) or parsed.query or parsed.fragment or
                not re.fullmatch(r'/s/[a-z0-9_-]{4,32}', parsed.path)):
            raise ValueError()
        if not isinstance(text, str) or not isinstance(comment, str) or '\n' in comment or '\r' in comment or len(comment) > 120:
            raise ValueError()
        # 一言を任意の文字列置換で削らない。APIが付けた既知の先頭だけを除く。
        prefix = comment + '\n\n' if comment else ''
        if not text.startswith(prefix):
            raise ValueError()
        fixed = text[len(prefix):]
        rounded = {key: math.floor(value + .5) for key, value in n.items()}
        header = '今日1日の食事まとめ 🔥' if daily else '今日の食事解析結果 🔥'
        values = (f"{rounded['calories']}kcal (P:{rounded['protein']}g F:{rounded['fat']}g C:{rounded['carbs']}g)")
        expected = header + '\n' + ('3食 ' if daily else '') + values + '\n' + ' '.join(FIXED_MARKERS)
        if fixed != expected:
            raise ValueError()
        return {'share_url': url, 'api_text': fixed, 'comment': comment, 'nutrition': n}
    except (KeyError, TypeError, ValueError):
        raise ValueError('共有URL・固定文・解析集計の対応が不正です') from None


def validate_meal_response(response, slot, logged_at, comment, host):
    """品質passだけで通さず、食品明細・日付・固定文を機械照合する。"""
    try:
        if slot not in ('breakfast', 'lunch', 'dinner') or response['meal']['meal_type'] != slot:
            raise ValueError()
        if instant(datetime.fromisoformat(response['meal']['logged_at'])) != instant(datetime.fromisoformat(logged_at)):
            raise ValueError()
        if response['quality']['status'] != 'pass' or response['quality']['warnings'] != [] or response['analysis']['is_ambiguous'] is not False:
            raise ValueError()
        n = nutrition(response['analysis']['summary'])
        foods = response['analysis']['foods']
        if not isinstance(foods, list) or not 1 <= len(foods) <= 20:
            raise ValueError()
        totals = dict.fromkeys(SUMMARY_FIELDS, 0)
        for food in foods:
            if not isinstance(food.get('name'), str) or not food['name'].strip():
                raise ValueError()
            if 'amount' in food and not isinstance(food['amount'], str):
                raise ValueError()
            for key in totals:
                value = food.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 10000:
                    raise ValueError()
                totals[key] += value
        if any(abs(totals[key] - n[key]) > 1 for key in totals):
            raise ValueError()
        return share_and_text(response, host, comment, n)
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError('食事API応答の品質・食品明細・対象時刻を確認してください') from None


def validate_daily_response(response, target, comment, host, meals):
    """確認した3食と日次正本の集計を照合する。"""
    try:
        if date.fromisoformat(target).isoformat() != target or response['date'] != target or type(response['meal_count']) is not int or response['meal_count'] != 3:
            raise ValueError()
        if set(meals) != {'breakfast', 'lunch', 'dinner'}:
            raise ValueError()
        n = nutrition(response['summary'])
        if any(abs(sum(meal['nutrition'][key] for meal in meals.values()) - n[key]) > 1 for key in n):
            raise ValueError()
        return share_and_text(response, host, comment, n, daily=True)
    except (KeyError, TypeError, ValueError):
        raise ValueError('日次API応答と確認済み3食の対応が不正です') from None
