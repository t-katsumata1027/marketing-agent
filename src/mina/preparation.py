"""準備工程の実行記録と、生成画像の事前点検。"""

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from .clients import OutcomeUnknown
from .generation import MEAL_SLOTS, validate_plan, IMAGE_STYLE, meal_image_description, image_review_policy_sha256
from .preparation_status import QualityHold,PreparationDeadline,step_status


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class PreparationJournal:
    """外部処理の開始を先に確定し、途中で停止した処理を自動再実行しない。"""

    def __init__(self, store):
        self.store = store
        with store.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS preparation_steps (
                target_date TEXT NOT NULL, step TEXT NOT NULL, digest TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT, started REAL NOT NULL,
                PRIMARY KEY(target_date,step))""")

    def run(self, target, step, inputs, operation):
        from .audit import step_context, safe_failure
        stage,slot=step_context(step)
        started=time.monotonic()
        digest = fingerprint(inputs)
        with self.store.transaction() as db:
            previous = db.execute("SELECT * FROM preparation_steps WHERE target_date=? AND step=?", (target, step)).fetchone()
            if previous:
                if previous["digest"] != digest:
                    raise ValueError("準備対象の版が変わっています。既存の外部状態を照合してください")
                if previous["state"] == "complete":
                    return json.loads(previous["result"])
                if previous['state'] == 'known_rejected':
                    from .generation_receipts import rejection, GeneratedPlanRejected
                    raise GeneratedPlanRejected(rejection(previous))
                if previous['state']=='held':
                    held=json.loads(previous['result'])
                    raise QualityHold('保存済み工程の品質保留を保持しています',held['step'],held['reason_codes'])
                if previous['state']=='stopped':
                    raise PreparationDeadline('準備期限による工程停止を保持しています')
                raise OutcomeUnknown("preparation_reconciliation_required")
            from .current_day_gate import require_stage
            require_stage(self.store,target,db)
            db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'started',NULL,?)", (target, step, digest, datetime.now(UTC).timestamp()))
            self.store.audit.emit('creation',target,slot,None,stage,'started',details={'step_sha256':digest},db=db)
        self.store.record_preparation_progress(target,step,step_status(step))
        try:
            from .generation_receipts import operation as generation_operation
            with generation_operation(target, step, digest):
                result = operation()
            raw = json.dumps(result, ensure_ascii=False, allow_nan=False)
            if len(raw.encode("utf-8")) > 131072:
                raise ValueError("工程結果が大きすぎます")
            with self.store.transaction() as db:
                db.execute("UPDATE preparation_steps SET state='complete',result=? WHERE target_date=? AND step=? AND state='started'", (raw, target, step))
                self.store.audit.emit('creation',target,slot,None,stage,'completed',details={'step_sha256':digest,'elapsed_ms':round((time.monotonic()-started)*1000,3)},db=db)
            return result
        except PreparationDeadline:
            with self.store.transaction() as db:
                db.execute("UPDATE preparation_steps SET state='stopped',result=? WHERE target_date=? AND step=? AND state='started'",
                    (json.dumps({'classification':'deadline_exceeded','step':step}),target,step))
                self.store.audit.emit('creation',target,slot,None,stage,'stopped','deadline_exceeded',db=db)
            raise
        except QualityHold as error:
            from .generation_receipts import GeneratedPlanRejected, rejection
            if isinstance(error, GeneratedPlanRejected):
                receipt = error.receipt
                rejection({'state': 'known_rejected', 'target_date': target, 'step': step,
                    'digest': digest, 'result': json.dumps(receipt)})
                with self.store.transaction() as db:
                    db.execute("UPDATE preparation_steps SET state='known_rejected',result=? WHERE target_date=? AND step=? AND state='started'",
                        (json.dumps(receipt), target, step))
                    self.store.audit.emit('creation', target, slot, None, stage, 'held', error.audit_reason, db=db)
                raise
            with self.store.transaction() as db:
                db.execute("UPDATE preparation_steps SET state='held',result=? WHERE target_date=? AND step=? AND state='started'",
                    (json.dumps({'classification':'quality_hold','step':error.step,'reason_codes':error.reason_codes}),target,step))
                self.store.audit.emit('creation',target,slot,None,stage,'held',error.audit_reason,db=db)
            raise
        except Exception as error:
            reason,details=safe_failure(error)
            with self.store.transaction() as db:
                db.execute("UPDATE preparation_steps SET state='outcome_unknown' WHERE target_date=? AND step=? AND state='started'", (target, step))
                self.store.audit.emit('creation',target,slot,None,stage,'failed',reason,details,db=db)
            raise


def prepare_images(store, draft_path: Path, directory: Path, client, replacement_round=None, replace_slots=(),
                   reused_rounds=None, repair_instruction=None, only_slots=None):
    """画像3枚と事前点検結果を保存する。LogEats登録や承認候補化はまだ行わない。"""
    draft = json.loads(draft_path.read_text(encoding="utf-8-sig"))
    from .current_day_gate import require_stage
    require_stage(store,draft['target_date'])
    if draft.get("state") != "draft_unreviewed":
        raise ValueError("未レビューの献立下書きが必要です")
    from .menu_budget import validate_draft_plan
    validate_draft_plan(draft,store)
    from datetime import date
    date.fromisoformat(draft["target_date"])
    target = draft["target_date"]
    if replacement_round is not None:
        from .clients import MealClient
        MealClient.request_key(replacement_round)
    if (bool(replace_slots) != bool(replacement_round) or
            not set(replace_slots) <= set(MEAL_SLOTS)):
        raise ValueError('画像差替えには明示した枠と復旧要求が必要です')
    reused_rounds = reused_rounds or {}
    if not set(reused_rounds) <= set(MEAL_SLOTS) or set(reused_rounds) & set(replace_slots):
        raise ValueError('差替え対象と再利用対象の画像を重ねられません')
    for old_round in reused_rounds.values():
        from .clients import MealClient
        MealClient.request_key(old_round)
    if repair_instruction is not None and (not replacement_round or not isinstance(repair_instruction, str) or not 0 < len(repair_instruction) <= 2000):
        raise ValueError('修復指示には明示した差替え対象が必要です')
    journal = PreparationJournal(store)
    directory.mkdir(parents=True, exist_ok=True)
    selected=tuple(MEAL_SLOTS if only_slots is None else only_slots)
    if not selected or len(set(selected))!=len(selected) or not set(selected)<=set(MEAL_SLOTS):
        raise ValueError('画像準備の対象枠が不正です')
    outputs = {}
    for slot in selected:
        meal = draft["meals"][slot]
        replacing = slot in replace_slots
        image_round = replacement_round if replacing else reused_rounds.get(slot)
        slot_directory = directory / image_round if image_round else directory
        slot_directory.mkdir(parents=True, exist_ok=True)
        path = slot_directory / f"{target}-{slot}.jpg"
        description=meal_image_description(meal)
        if image_round:
            note = repair_instruction if replacing and repair_instruction is not None else ('画像の修復：元の献立・全食品・分量・品数を維持し、調理法と盛付けを説明どおり明確に描く。'
                '献立で指定された料理の構造と、指定された別添え小皿を省略しない。'
                'カメラを引き、右端の副菜の器を含む全器の外周を完全に画面内へ収める。右端にも十分な背景の余白を確保する。')
            description += '\n' + note
        prompt_sha256=hashlib.sha256((IMAGE_STYLE+'\n料理と量：'+description).encode()).hexdigest()

        def generate():
            if path.exists() or path.is_symlink():
                raise ValueError("既存の画像を自動上書きしません")
            raw = client.image(description)
            with path.open("xb") as file:
                file.write(raw)
            return {"path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}

        suffix = ':' + image_round if image_round else ''
        if slot in reused_rounds:
            # 指定した旧修復が完成・合格済みの場合だけ再利用し、新規生成へ進ませない。
            with store.transaction() as db:
                records = {r['step']: r for r in db.execute('SELECT step,state,result FROM preparation_steps WHERE target_date=? AND step IN (?,?)',
                    (target, 'image:' + slot + suffix, 'image_review:' + slot + suffix))}
            review_row = records.get('image_review:' + slot + suffix)
            if (len(records) != 2 or any(r['state'] != 'complete' for r in records.values()) or not review_row or
                    json.loads(review_row['result']) != {'status': 'image_acceptable', 'reason_codes': []}):
                raise ValueError('再利用する修復画像の完成・合格証跡がありません')
        image = journal.run(target, "image:" + slot + suffix, {"meal": meal, "directory": str(slot_directory.resolve()),
                                                     "prompt_sha256":prompt_sha256}, generate)
        saved = Path(image["path"])
        if saved.is_symlink():
            raise ValueError("画像実体の確認が必要です")
        raw = saved.read_bytes()
        if hashlib.sha256(raw).hexdigest() != image["sha256"] or len(raw) != image["size"]:
            raise ValueError("保存した画像の版が変わっています")
        review = journal.run(target, "image_review:" + slot + suffix,
                             {"sha256": image["sha256"], "meal": meal,
                              "review_policy_sha256": image_review_policy_sha256()},
                             lambda: client.review_image(raw, meal))
        from .review_diagnostics import record_findings,record_verdict
        record_findings(store,client,target,slot,'image_review',image['sha256'])
        record_verdict(store,target,slot,'image_review',review)
        if review["status"] != "image_acceptable" or review["reason_codes"]:
            raise QualityHold("画像の事前点検で保留になりました",'image_review:'+slot,review.get('reason_codes',()))
        outputs[slot] = {**image, "image_review": review}
    return {"target_date": target, "state": "images_reviewed", "images": outputs}


def save_images_to_drive(store, reviewed, drive, replacement_rounds=None):
    """点検済み画像の保存と再読取を別工程で記録する。登録はまだ行わない。"""
    if reviewed.get('state') != 'images_reviewed' or set(reviewed.get('images', {})) != set(MEAL_SLOTS):
        raise ValueError('点検済みの3画像が必要です')
    journal = PreparationJournal(store)
    target = reviewed['target_date']
    from .current_day_gate import require_stage
    require_stage(store,target)
    replacement_rounds = replacement_rounds or {}
    if not set(replacement_rounds) <= set(MEAL_SLOTS):
        raise ValueError('画像保存の差替え枠が不正です')
    for round_key in replacement_rounds.values():
        from .clients import MealClient
        MealClient.request_key(round_key)
    outputs = {}
    for slot in MEAL_SLOTS:
        image = reviewed['images'][slot]
        if image['image_review']['status'] != 'image_acceptable' or image['image_review']['reason_codes']:
            raise ValueError('画像の事前点検が完了していません')
        path = Path(image['path'])
        if path.is_symlink():
            raise ValueError('画像実体の確認が必要です')
        raw = path.read_bytes()
        if len(raw) != image['size'] or hashlib.sha256(raw).hexdigest() != image['sha256']:
            raise ValueError('事前点検した画像の版が変わっています')
        filename = f'{target}-{slot}.jpg'
        inputs = {'sha256': image['sha256'], 'filename': filename, 'folder_id': drive.folder_id}
        suffix = ':' + replacement_rounds[slot] if slot in replacement_rounds else ''
        if suffix:
            with store.transaction() as db:
                previous = db.execute('SELECT state,digest FROM preparation_steps WHERE target_date=? AND step=?',
                                      (target, 'drive_upload:' + slot)).fetchone()
            if not previous or previous['state'] != 'complete' or previous['digest'] == fingerprint(inputs):
                raise ValueError('保存完了した旧画像と異なる画像だけを別保存できます')
        uploaded = journal.run(target, 'drive_upload:' + slot + suffix, inputs, lambda: drive.upload(raw, filename))
        # 読取検証が失敗しても、完了済みuploadの新規実行はしない。
        with store.audit.operation('creation',target,slot,None,'drive_verify',{'image_sha256':image['sha256']}):
            verified = drive.verify(uploaded['id'], raw, filename)
        outputs[slot] = {**image, 'drive': verified}
    return {'target_date': target, 'state': 'images_drive_verified', 'images': outputs}
