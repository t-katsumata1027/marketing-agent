"""10/29だけの別承認原本。旧unknown・初回・費用を更新せず参照する。"""
import ast
import json
import subprocess
from .preparation import fingerprint

TARGET='2026-10-29'
VERSION='mina-current-day-preparation-v1'
LEGACY_COMMIT='1c5224b6d0fe1670ee9b874b08688eeac42da12f'
UNKNOWN='meal_replan:dinner:1'


def policy_proof(root):
    """固定旧版を実行せず、点検指示のリテラルを現在版と比較する。"""
    from .menu_budget import INSTRUCTIONS
    result=subprocess.run(['git','-c','safe.directory='+str(root),'-C',str(root),'show',
        LEGACY_COMMIT+':src/mina/meal_replanning.py'],capture_output=True,check=True,timeout=10)
    matches=[]
    for node in ast.walk(ast.parse(result.stdout)):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='structured' and len(node.args)>=4:
            if isinstance(node.args[3],ast.Constant) and node.args[3].value=='mina_menu_nutrition_check':
                matches.append(ast.literal_eval(node.args[0]))
    if matches!=[INSTRUCTIONS]:raise ValueError('旧食品別点検policyの互換性を確認できません')
    import hashlib
    return {'legacy_commit':LEGACY_COMMIT,'module_sha256':hashlib.sha256(result.stdout).hexdigest(),
        'instructions_sha256':hashlib.sha256(INSTRUCTIONS.encode()).hexdigest()}


def capture(db,draft,policy):
    """別承認の対象となる三工程と入力・結果を、一つの読取版から固定する。"""
    from .menu_budget import foods,numbers,INSTRUCTIONS
    import hashlib
    if draft.get('target_date')!=TARGET or draft.get('state')!='draft_unreviewed':raise ValueError('29日未登録計画だけが対象です')
    if policy.get('legacy_commit')!=LEGACY_COMMIT or policy.get('instructions_sha256')!=hashlib.sha256(INSTRUCTIONS.encode()).hexdigest():
        raise ValueError('旧policy証拠が不正です')
    inputs={'foods':foods(draft)};key='menu_nutrition:'+fingerprint(inputs)
    rows=[dict(r) for r in db.execute('SELECT * FROM preparation_steps WHERE target_date=? ORDER BY step',(TARGET,))]
    by={r['step']:r for r in rows}
    if set(by)!={'daily_draft',key,UNKNOWN} or by['daily_draft']['state']!='complete' or by[key]['state']!='complete':
        raise ValueError('旧工程が固定三工程と一致しません')
    if json.loads(by['daily_draft']['result'])!={'target_date':TARGET,'state':'draft_unreviewed','meal_count':3}:raise ValueError('旧献立生成の完了範囲が不正です')
    old=by[UNKNOWN]
    expected={'draft_sha256':fingerprint(draft),'slot':'dinner','reason_codes':['daily_energy_out_of_range'],'attempt':1}
    if old['state']!='outcome_unknown' or old['result'] is not None or old['digest']!=fingerprint(expected):
        raise ValueError('旧結果不明を安全に一回消費として照合できません')
    if by[key]['digest']!=fingerprint(inputs):raise ValueError('旧食品入力が変わっています')
    values=json.loads(by[key]['result'])
    if set(values)!=set(inputs['foods']):raise ValueError('旧食品別結果が不完全です')
    for slot,fs in inputs['foods'].items():numbers(values[slot],len(fs))
    events=list(db.execute("SELECT * FROM operation_events WHERE target_date=? AND slot='dinner' AND stage='meal_replan' AND event='failed' AND reason='validation_failed'",(TARGET,)))
    frames=['src/mina/preparation.py:run:51','src/mina/meal_replanning.py:propose:86','src/mina/generation.py:validate_plan:262']
    evidence=[dict(r) for r in events if json.loads(r['details']).get('frames')==frames and r['occurred']>=old['started']]
    if not evidence:
        raise ValueError('生成応答後の純粋検査失敗の原本がありません')
    from .domain import approval_time,date
    preparation=dict(db.execute('SELECT * FROM preparation WHERE target_date=?',(TARGET,)).fetchone() or {}) if db.execute("SELECT 1 FROM sqlite_master WHERE name='preparation'").fetchone() else {}
    return {'spec':VERSION,'target_date':TARGET,'draft_digest':fingerprint(draft),'steps':rows,
        'food_inputs':inputs,'food_step':key,'food_result_digest':fingerprint(values),'policy':policy,'validation_events':evidence,
        'unknown_digest':fingerprint(old),'old_unknown_started':old['started'],'old_preparation_row':preparation,
        'old_first_replan_started':None,'old_preparation_deadline':None,
        'derived_policy_deadline':approval_time(date.fromisoformat(TARGET)).isoformat(),
        'old_cost_status':'unknown_consumed','used_ordinals':[1],'allowed_new_ordinal':2,'scope':'prepare_only'}


def stored(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='current_day_jobs'").fetchone():return None
    row=db.execute('SELECT origin,origin_digest FROM current_day_jobs WHERE target_date=?',(TARGET,)).fetchone()
    if not row:return None
    value=json.loads(row['origin'])
    if fingerprint(value)!=row['origin_digest'] or value.get('spec')!=VERSION or value.get('target_date')!=TARGET:
        raise ValueError('旧工程の別承認原本が変わっています')
    return value


def consumed_unknown(db,row):
    """保存/成功へ変換せず、原本一致の旧一回だけを計数する。"""
    if row['target_date']!=TARGET or row['step']!=UNKNOWN:return False
    proof=stored(db)
    return bool(proof and fingerprint(dict(row))==proof['unknown_digest'])


def legacy_source(db,target,key,inputs):
    proof=stored(db) if target==TARGET else None
    return bool(proof and proof['food_step']==key and proof['food_inputs']==inputs)


def verify_stored_rows(db,proof):
    """新工程は追加できるが、旧三工程・原因監査の一文字も書き換えない。"""
    for row in proof['steps']:
        current=db.execute('SELECT * FROM preparation_steps WHERE target_date=? AND step=?',(TARGET,row['step'])).fetchone()
        if not current or fingerprint(dict(current))!=fingerprint(row):raise ValueError('旧工程原本が変更されています')
    for row in proof['validation_events']:
        current=db.execute('SELECT * FROM operation_events WHERE id=?',(row['id'],)).fetchone()
        if not current or fingerprint(dict(current))!=fingerprint(row):raise ValueError('旧原因監査が変更されています')
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='preparation'").fetchone():
        current=db.execute('SELECT * FROM preparation WHERE target_date=?',(TARGET,)).fetchone()
        if dict(current or {})!=proof['old_preparation_row']:raise ValueError('旧準備の開始・停止行が変更されています')
