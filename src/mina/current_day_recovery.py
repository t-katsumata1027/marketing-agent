"""10/29の未登録3食を、別承認の一度の窓で準備カードまで進める。"""
import hashlib,json
from contextlib import contextmanager
from pathlib import Path
from .current_day_origin import TARGET,stored
from .current_day_gate import Operations
from .preparation import PreparationJournal,fingerprint,save_images_to_drive
from .menu_budget import MenuBudget,POLICY,foods


@contextmanager
def bounded_drive(drive,gate):
    """OAuth更新・metadata・upload・downloadの各HTTP直前にも期限を検査する。"""
    previous=getattr(drive,'request_guard',None)
    def check(url,data,method,operation):
        gate.refresh_external();gate.check(sending=True)
        return previous(url,data,method,operation) if previous is not None else operation()
    drive.request_guard=check
    try:yield
    finally:drive.request_guard=previous


def seed_legacy(gate,draft):
    """旧三枠の完了原本への参照だけを追加し、call/結果/費用を複製しない。"""
    budget=MenuBudget(gate.store,TARGET,PreparationJournal(gate.store))
    with gate.store.transaction() as db:
        gate.check(db,sending=True);origin=stored(db)
        if fingerprint(draft)!=origin['draft_digest']:raise ValueError('旧計画の実体が変わっています')
        source=db.execute('SELECT * FROM preparation_steps WHERE target_date=? AND step=?',(TARGET,origin['food_step'])).fetchone()
        old=next(r for r in origin['steps'] if r['step']==origin['food_step'])
        if dict(source)!=old:raise ValueError('旧食品別完了原本が変わっています')
        for slot,fs in foods(draft).items():
            db.execute('INSERT INTO menu_nutrition_bases VALUES(?,?,?,?,?,?,?)',
                (TARGET,slot,fingerprint(fs),POLICY,origin['food_step'],json.dumps(origin['food_inputs'],sort_keys=True),origin['food_result_digest']))
    return budget


def execute(gate,base,ai,drive,meal,host,evidence,capture,discord,commit):
    """本体救済/PDC/予約公開を使わない。失敗後は同jobの照合だけに戻る。"""
    from .readiness import read_json,check_evidence
    from .m1_contract import validate_state,verify_source
    from .automatic_repair import AutomaticImageRepair
    from .registration import register_day
    from .daily_pipeline import write_artifact
    from .current_day_cards import make,deliver
    from .domain import Candidate
    base=Path(base)
    if commit!=gate.approval['code_commit']:raise ValueError('当日実装版の転用は禁止です')
    if base.is_symlink():raise ValueError('原本領域のリンクを拒否します')
    original=base/'draft.json'
    if original.is_symlink():raise ValueError('原本計画のリンクを拒否します')
    raw=original.read_bytes();draft=json.loads(raw)
    check_code=gate.check_code
    def check_original():
        check_code()
        if original.is_symlink() or original.read_bytes()!=raw:raise ValueError('旧計画の実体が変更されています')
    gate.check_code=check_original
    check_evidence(evidence,TARGET,gate.clock())
    with gate.running(),bounded_drive(drive,gate):
        ops=Operations(gate)
        ai=ops.client(ai,'ai',{'structured','image','review_image','review_meal','review_ogp'})
        drive=ops.client(drive,'drive',{'upload','verify'})
        meal=ops.client(meal,'m1',{'day_state','request_state','register','json_write'})
        reader=lambda url,host:ops.invoke('ogp:read',{'url':url},lambda:capture(url,host),lookup=True)
        state=meal.day_state(TARGET)
        if validate_state(state,TARGET,gate.clock())!=gate.approval['initial_state_version'] or state['meal_count']!=0:raise ValueError('権限発行後に当日正本が変わりました')
        out=base/('current-day-'+gate.approval['recovery_uuid'])
        out.mkdir(mode=0o700)
        path=out/'draft.json'
        with path.open('xb') as f:f.write(raw)
        seed_legacy(gate,draft)
        repair=AutomaticImageRepair(gate.store,draft,path,out/'images',ai,drive,clock=gate.clock)
        images=repair.images();write_artifact(out/'images-reviewed.json',images)
        # 初回の通常保存。旧日の画像・Drive参照は変更しない。
        saved=save_images_to_drive(gate.store,images,drive);write_artifact(out/'drive.json',saved)
        # 品質不合格の訂正は既存契約内で一回。未保存拒否や保存画像保留から再登録しない。
        reviewed=register_day(gate.store,draft,saved,out/'ogp',meal,ai,host,capture=reader,allow_correction=True)
        write_artifact(out/'posts-reviewed.json',reviewed)
        cards=make(gate,reviewed,saved,evidence,commit,host,meal,reader,out/'cards')
        write_artifact(out/'prepared-cards.json',cards)
        def verify(payload):
            candidate=Candidate(**payload['source']['candidate'])
            candidate.validate_content(host)
            if payload['platform']=='x' and payload['body']!=candidate.content:raise ValueError('X準備本文が点検版と一致しません')
            current=meal.day_state(TARGET);validate_state(current,TARGET,gate.clock())
            if current.get('nutrition_targets')!=payload['source']['nutrition_targets']:raise ValueError('栄養目標がカード合成後に変わりました')
            verify_source(candidate,meal,host,reader,clock=gate.clock)
        deliver(gate,discord,out/'cards',verify)
        if original.read_bytes()!=raw:raise ValueError('旧原本が別処理で変更されています')
        return {'target_date':TARGET,'state':'preparation_cards_delivered','x_cards':4,'instagram_cards':4,'scheduled_at':None,'publication_forbidden':True}
