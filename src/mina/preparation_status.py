"""品質保留と成否不明を区別し、外部本文を含めない準備状況を作る。"""

from .domain import LABELS

STAGES = {'daily_draft':'献立生成','image':'画像生成','image_review':'画像点検',
          'drive_upload':'画像保存','meal_register':'食事登録','meal_review':'食品・栄養点検',
          'correction_plan':'訂正案作成','meal_correction':'解析訂正','ogp_capture':'共有画像取得',
          'ogp_review':'共有画像点検','daily_share':'日次共有','candidates':'承認候補の照合',
          'draft':'献立生成','images':'画像準備','drive':'画像保存','registration':'登録・共有'}
REASONS = {'image_analysis_mismatch':'食品・分量と解析結果の不一致',
           'nutrition_semantic_mismatch':'分量・栄養値の整合性を確認できない',
           'context_mismatch':'対象日・区分・本文の文脈を確認できない',
           'share_ogp_mismatch':'共有画像と正本の一致を確認できない',
           'experiential_claim':'実食経験の断定','health_claim':'健康効果の断定',
           'copy_repetition':'過去の文面との重複','daily_energy_out_of_range':'3食合計が設定範囲外'}


class QualityHold(ValueError):
    """応答を得た点検の保留。要求の保存成否不明とは区別する。"""
    def __init__(self,message,step,reason_codes=()):
        self.step=step
        self.reason_codes=tuple(code for code in reason_codes if code in REASONS)
        super().__init__(message)


class PreparationDeadline(ValueError):
    """外部処理を開始する前に準備期限へ到達した。"""


def describe_step(step):
    """工程名と食事区分だけを表示し、UUIDなどの付加情報を出さない。"""
    pieces=step.split(':')
    slot=pieces[1].split('-')[0] if len(pieces)>1 else None
    return (LABELS.get(slot,'全体')+'／'+STAGES.get(pieces[0],'準備状況の確認'))


def step_status(step):
    prefix=step.split(':')[0]
    if prefix in ('correction_plan','meal_correction'):
        return 'correcting'
    return 'reviewing' if prefix in ('image_review','meal_review','ogp_review') else 'running'
