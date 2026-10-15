"""準備工程の実行記録と、生成画像の事前点検。"""

import hashlib
import json
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
        digest = fingerprint(inputs)
        with self.store.transaction() as db:
            previous = db.execute("SELECT * FROM preparation_steps WHERE target_date=? AND step=?", (target, step)).fetchone()
            if previous:
                if previous["digest"] != digest:
                    raise ValueError("準備対象の版が変わっています。既存の外部状態を照合してください")
                if previous["state"] == "complete":
                    return json.loads(previous["result"])
                if previous['state']=='held':
                    held=json.loads(previous['result'])
                    raise QualityHold('保存済み工程の品質保留を保持しています',held['step'],held['reason_codes'])
                if previous['state']=='stopped':
                    raise PreparationDeadline('準備期限による工程停止を保持しています')
                raise OutcomeUnknown("preparation_reconciliation_required")
            db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'started',NULL,?)", (target, step, digest, datetime.now(UTC).timestamp()))
        self.store.record_preparation_progress(target,step,step_status(step))
        try:
            result = operation()
            raw = json.dumps(result, ensure_ascii=False, allow_nan=False)
            if len(raw.encode("utf-8")) > 131072:
                raise ValueError("工程結果が大きすぎます")
            with self.store.transaction() as db:
                db.execute("UPDATE preparation_steps SET state='complete',result=? WHERE target_date=? AND step=? AND state='started'", (raw, target, step))
            return result
        except PreparationDeadline:
            with self.store.transaction() as db:
                db.execute("UPDATE preparation_steps SET state='stopped',result=? WHERE target_date=? AND step=? AND state='started'",
                    (json.dumps({'classification':'deadline_exceeded','step':step}),target,step))
            raise
        except QualityHold as error:
            with self.store.transaction() as db:
                db.execute("UPDATE preparation_steps SET state='held',result=? WHERE target_date=? AND step=? AND state='started'",
                    (json.dumps({'classification':'quality_hold','step':error.step,'reason_codes':error.reason_codes}),target,step))
            raise
        except Exception:
            with self.store.transaction() as db:
                db.execute("UPDATE preparation_steps SET state='outcome_unknown' WHERE target_date=? AND step=? AND state='started'", (target, step))
            raise


def prepare_images(store, draft_path: Path, directory: Path, client):
    """画像3枚と事前点検結果を保存する。LogEats登録や承認候補化はまだ行わない。"""
    draft = json.loads(draft_path.read_text(encoding="utf-8-sig"))
    if draft.get("state") != "draft_unreviewed":
        raise ValueError("未レビューの献立下書きが必要です")
    validate_plan({"meals": draft["meals"], "daily_comment": draft["daily_comment"]})
    from datetime import date
    date.fromisoformat(draft["target_date"])
    target = draft["target_date"]
    journal = PreparationJournal(store)
    directory.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for slot in MEAL_SLOTS:
        meal = draft["meals"][slot]
        path = directory / f"{target}-{slot}.jpg"
        description=meal_image_description(meal)
        prompt_sha256=hashlib.sha256((IMAGE_STYLE+'\n料理と量：'+description).encode()).hexdigest()

        def generate():
            if path.exists() or path.is_symlink():
                raise ValueError("既存の画像を自動上書きしません")
            raw = client.image(description)
            with path.open("xb") as file:
                file.write(raw)
            return {"path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}

        image = journal.run(target, "image:" + slot, {"meal": meal, "directory": str(directory.resolve()),
                                                     "prompt_sha256":prompt_sha256}, generate)
        saved = Path(image["path"])
        if saved.is_symlink():
            raise ValueError("画像実体の確認が必要です")
        raw = saved.read_bytes()
        if hashlib.sha256(raw).hexdigest() != image["sha256"] or len(raw) != image["size"]:
            raise ValueError("保存した画像の版が変わっています")
        review = journal.run(target, "image_review:" + slot,
                             {"sha256": image["sha256"], "meal": meal,
                              "review_policy_sha256": image_review_policy_sha256()},
                             lambda: client.review_image(raw, meal))
        if review["status"] != "image_acceptable" or review["reason_codes"]:
            raise QualityHold("画像の事前点検で保留になりました",'image_review:'+slot,review.get('reason_codes',()))
        outputs[slot] = {**image, "image_review": review}
    return {"target_date": target, "state": "images_reviewed", "images": outputs}


def save_images_to_drive(store, reviewed, drive):
    """点検済み画像の保存と再読取を別工程で記録する。登録はまだ行わない。"""
    if reviewed.get('state') != 'images_reviewed' or set(reviewed.get('images', {})) != set(MEAL_SLOTS):
        raise ValueError('点検済みの3画像が必要です')
    journal = PreparationJournal(store)
    target = reviewed['target_date']
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
        uploaded = journal.run(target, 'drive_upload:' + slot, inputs, lambda: drive.upload(raw, filename))
        # 読取検証が失敗しても、完了済みuploadの新規実行はしない。
        verified = drive.verify(uploaded['id'], raw, filename)
        outputs[slot] = {**image, 'drive': verified}
    return {'target_date': target, 'state': 'images_drive_verified', 'images': outputs}
