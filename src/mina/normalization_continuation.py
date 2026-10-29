"""正規化補修の採用だけを記録し、未開始工程へ一度だけ進む。"""
import json
from pathlib import Path
from .tolerance_case import ToleranceGate,ReviewedAI
from .normalization_checkpoint import VERSION,verify
from .preparation import fingerprint
from .current_day_origin import TARGET
from .tolerance_review import policy_sha256

class NormalizationGate(ToleranceGate):
    TABLE='current_day_normalization_continuations';FLAG='MINA_CURRENT_DAY_NORMALIZATION_ENABLED'
    CONTRACT=VERSION;ID_FIELD='normalization_continuation_uuid'
    def issue(self,state,receipt):
        deadline=super().issue(state,receipt)
        self.cache_inventory()
        return deadline
    def cache_inventory(self):
        if self.latest_inventory is None:return
        with self.store.transaction() as db:
            self.authority(db,('issued','running'),True);self.receipt(db)
            db.execute('CREATE TABLE IF NOT EXISTS normalization_inventory_cache(case_uuid TEXT PRIMARY KEY,approval_digest TEXT NOT NULL,channels_digest TEXT NOT NULL,owner TEXT NOT NULL,epoch INTEGER NOT NULL,receipt TEXT NOT NULL)')
            db.execute('INSERT OR REPLACE INTO normalization_inventory_cache VALUES(?,?,?,?,?,?)',(self.approval[self.ID_FIELD],fingerprint(self.approval),fingerprint(self.inventory.channels),self.approval['recovery_uuid'],1,json.dumps(self.latest_inventory)))
    def refresh_external(self):
        if self.latest_inventory is None:
            from .replacement_client import timestamp
            with self.store.transaction() as db:
                self.authority(db,('issued','running'),True)
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='normalization_inventory_cache'").fetchone():
                    row=db.execute('SELECT * FROM normalization_inventory_cache WHERE case_uuid=?',(self.approval[self.ID_FIELD],)).fetchone()
                    if row and (row['approval_digest'],row['channels_digest'],row['owner'],row['epoch'])==(fingerprint(self.approval),fingerprint(self.inventory.channels),self.approval['recovery_uuid'],1):
                        receipt=json.loads(row['receipt'])
                        if 0<=(self.clock()-timestamp(receipt['read_started_at'])).total_seconds()<90:
                            self.latest_inventory=receipt;self.receipt(db)
        super().refresh_external();self.cache_inventory()
    def verify_source(self,db):
        verify(db,self.snapshot)
        from .clients import MealClient
        MealClient.request_key(self.approval[self.ID_FIELD])
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='tolerance_case_calls'").fetchone():
            old={r['id'] for r in self.snapshot['calls']}
            linked={r[0] for r in db.execute('SELECT call_id FROM tolerance_case_calls WHERE case_uuid=?',(self.approval[self.ID_FIELD],))}
            actual={r[0] for r in db.execute('SELECT id FROM current_day_calls WHERE recovery_uuid=?',(self.approval['recovery_uuid'],))}-old
            if actual!=linked:raise ValueError('限定継続以外の操作が混入しました')
    def validate_scope(self,a):
        if a['slot']!='dinner' or a['selected_image_sha256']!=self.snapshot['adoption']['image_sha256']:raise ValueError('元夕画像の補修適用以外へ転用しません')
    def record_call(self,db,key,name):
        from .generation_receipts import CURRENT
        current=CURRENT.get();step=current['step'] if current else None
        if name in ('ai:image','ai:review_image'):raise ValueError('画像生成/画像再点検は追加しません')
        allowed={'meal_review:'+s+suffix for s in ('breakfast','lunch','dinner') for suffix in ('',':corrected')}|{'correction_plan:'+s for s in ('breakfast','lunch','dinner')}
        if name=='ai:structured' and step not in allowed:raise ValueError('未開始の食品解析点検/既存訂正だけを実行します')
        return super().record_call(db,key,name)

def execute(gate,ai,drive,meal,host,evidence,capture,discord,commit):
    from .current_day_gate import Operations
    from .current_day_recovery import bounded_drive,finish
    from .readiness import check_evidence
    from .m1_contract import validate_state
    from .daily_pipeline import write_artifact
    source=Path(gate.snapshot['output']);draft=json.loads((source/'draft.json').read_bytes())
    if commit!=gate.approval['code_commit'] or fingerprint(draft)!=gate.snapshot['draft_digest']:raise ValueError('限定継続の実装/計画が不一致です')
    check_evidence(evidence,TARGET,gate.clock())
    with gate.running(),bounded_drive(drive,gate):
        ops=Operations(gate);ai=ReviewedAI(gate,ops.client(ai,'ai',{'structured','review_ogp'}))
        drive=ops.client(drive,'drive',{'upload','verify'});meal=ops.client(meal,'m1',{'day_state','request_state','register','json_write'})
        reader=lambda url,host:ops.invoke('ogp:read',{'url':url},lambda:capture(url,host),lookup=True)
        state=meal.day_state(TARGET)
        if validate_state(state,TARGET,gate.clock())!=gate.approval['initial_state_version'] or state['meal_count']!=0:raise ValueError('限定継続発行後に正本が変わりました')
        with gate.store.transaction() as db:
            gate.check(db,sending=True)
            db.execute('CREATE TABLE IF NOT EXISTS normalization_adoptions(case_uuid TEXT PRIMARY KEY,source_commit TEXT NOT NULL,adoption TEXT NOT NULL,digest TEXT NOT NULL)')
            db.execute('INSERT INTO normalization_adoptions VALUES(?,?,?,?)',(gate.approval[gate.ID_FIELD],commit,json.dumps(gate.snapshot['adoption'],ensure_ascii=False),fingerprint(gate.snapshot['adoption'])))
        packet={'target_date':TARGET,'state':'images_reviewed','images':gate.snapshot['approved_images']}
        out=source/('normalization-'+gate.approval[gate.ID_FIELD]);out.mkdir(mode=0o700)
        write_artifact(out/'images-reviewed.json',packet)
        return finish(gate,draft,packet,out,drive,meal,ai,host,evidence,reader,discord,commit,meal_policy_sha256=policy_sha256())
