"""品質保留と成否不明を区別し、外部本文を含めない準備状況を作る。"""

from .domain import LABELS

STAGES = {'daily_draft':'献立生成','image':'画像生成','image_review':'画像点検',
          'drive_upload':'画像保存','meal_register':'食事登録','meal_review':'食品・栄養点検',
          'correction_plan':'訂正案作成','meal_correction':'解析訂正','ogp_capture':'共有画像取得',
          'ogp_review':'共有画像点検','daily_share':'日次共有','candidates':'承認候補の照合',
          'draft':'献立生成','images':'画像準備','drive':'画像保存','registration':'登録・共有',
          'source_state':'登録前の正本照合','source_request':'登録要求の照合','meal_replan':'メニューの再計画','menu_nutrition':'登録前の食品別概算',
          'replacement_preflight':'自動修復の事前照合'}
REASONS = {'post_text_too_long':'固定文・共有URLを含むX本文が280上限を超過',
           'image_analysis_mismatch':'食品・分量と解析結果の不一致',
           'nutrition_semantic_mismatch':'分量・栄養値の整合性を確認できない',
           'context_mismatch':'対象日・区分・本文の文脈を確認できない',
           'share_ogp_mismatch':'共有画像と正本の一致を確認できない',
           'experiential_claim':'実食経験の断定','health_claim':'健康効果の断定',
           'copy_repetition':'過去の文面との重複','daily_energy_out_of_range':'3食合計が設定範囲外',
           'api_quality_rejected_not_saved':'登録前の解析品質検査で拒否・未保存を確認',
           'clock_ahead':'正本APIの応答時刻の先行が待機上限を超過',
           'http_error':'外部APIのHTTPエラー','io_failed':'外部接続またはファイル読取の失敗',
           'timeout':'外部照会の時間切れ','response_invalid':'外部応答の形式不正',
           'validation_failed':'入力または照合条件の不一致','unclassified':'分類できない工程エラー',
           'external_dispatch_present':'外部に対象日または日時不明の投稿・下書きが存在',
           'external_dispatch_unverified':'外部アカウント・予約状態を照合できない',
           'external_inventory_incomplete':'外部予約の全状態・全ページを取得できない',
           'external_inventory_stale':'外部予約の照合証跡が有効時間を超過',
           'replacement_source_incomplete':'保存済み3食が揃わず置換を開始できない',
           'replacement_preflight_busy':'同じ公開停止の確認処理が実行中',
           'replacement_quality_hold_required':'置換移行元の品質保留記録の形式・版を照合できない'}


class QualityHold(ValueError):
    """応答を得た点検の保留。要求の保存成否不明とは区別する。"""
    def __init__(self,message,step,reason_codes=()):
        self.step=step
        self.reason_codes=tuple(code for code in reason_codes if code in REASONS)
        super().__init__(message)
    audit_reason = 'quality_hold'


class KnownQualityRejected(QualityHold):
    """UUID照合で未保存と判明した品質拒否。再登録の許可ではない。"""
    audit_reason = 'quality_rejected_not_saved'
    def __init__(self,slot):
        super().__init__('登録前の品質拒否・未保存を確認しました','meal_register:'+slot,('api_quality_rejected_not_saved',))


class PreparationDeadline(ValueError):
    """外部処理を開始する前に準備期限へ到達した。"""
    audit_reason = 'deadline_exceeded'


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
