"""置換後の実解析から短い日次本文を作る。旧献立・旧概算を流用しない。"""
import math
from decimal import Decimal
from datetime import date
from .domain import FIXED_MARKERS
from .x_text import COMMENT_WEIGHT_LIMIT, weighted_length


def daily_comment(state):
    meals=state['meals']
    if len(meals)!=3 or {m['meal']['meal_type'] for m in meals}!={'breakfast','lunch','dinner'}:
        raise ValueError('replacement_daily_composition_invalid')
    total=sum(Decimal(str(m['analysis']['summary']['totalCalories'])) for m in meals)
    if not total.is_finite() or not Decimal('1800')<=total<=Decimal('2000'):
        raise ValueError('nutrition_budget_failed')
    target=date.fromisoformat(state['date']).isoformat()
    comment=f'{target}の3食献立で{total.normalize():f}kcal。食事の内容と栄養バランスを振り返ります。'
    summary={k:sum(m['analysis']['summary'][k] for m in meals) for k in ('totalCalories','totalProtein','totalFat','totalCarbs')}
    n={k:math.floor(v+.5) for k,v in summary.items()}
    # URLが未作成でも標準の最大短縮URLを含む本文を送信前に検査する。
    text=(comment+'\n\n今日1日の食事まとめ 🔥\n3食 '+f"{n['totalCalories']}kcal (P:{n['totalProtein']}g F:{n['totalFat']}g C:{n['totalCarbs']}g)\n"+
          ' '.join(FIXED_MARKERS)+'\nhttps://www.log-eats.com/s/'+'a'*32)
    if len(comment)>120 or weighted_length(comment)>COMMENT_WEIGHT_LIMIT or weighted_length(text)>280:
        raise ValueError('post_text_too_long')
    return comment
