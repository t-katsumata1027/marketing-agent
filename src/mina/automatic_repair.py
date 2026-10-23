"""通常準備内だけで行う、回数制限付きの画像修復。原記録は保持する。"""

import uuid

from .generation import MEAL_SLOTS
from .preparation import PreparationJournal, prepare_images
from .preparation_status import QualityHold


class AutomaticImageRepair:
    """同一献立の画像を一枠一回だけ修復する。登録要求は作成しない。"""

    def __init__(self, store, draft, draft_path, directory, ai, drive,clock=None):
        self.store, self.draft, self.draft_path = store, draft, draft_path
        self.directory, self.ai, self.drive = directory, ai, drive
        self.rounds = {}
        self.journal = PreparationJournal(store)
        from .meal_replanning import MealReplanner
        self.planner = MealReplanner(store,draft,draft_path,ai,clock)
        self.outputs = {}
        self.image_meals = {}

    def decision(self, kind, slot, inputs):
        return self.journal.run(self.draft['target_date'], kind + ':' + slot, inputs,
                                lambda: {'round': str(uuid.uuid4())})['round']

    def one(self,slot,menu_round=None,meal_client=None):
        """同じメニューの画像修復を一回試し、再保留ならその食事だけ再計画する。"""
        from .preparation import fingerprint
        if menu_round is None:
            menu_round=(self.planner.last_rounds.get(slot) if self.image_meals.get(slot)!=fingerprint(self.draft['meals'][slot]) else self.rounds.get(slot))
        while True:
            active=menu_round
            failure=None
            for attempt in range(2):
                try:
                    result = prepare_images(self.store, self.draft_path, self.directory, self.ai,
                        replacement_round=active,replace_slots=(slot,) if active else (),only_slots=(slot,))
                    if active:self.rounds[slot]=active
                    self.outputs[slot]=result['images'][slot]
                    self.image_meals[slot]=fingerprint(self.draft['meals'][slot])
                    return
                except QualityHold as error:
                    if error.step!='image_review:'+slot or not error.reason_codes:raise
                    failure=error
                    if attempt==0 and set(error.reason_codes)=={'image_analysis_mismatch'}:
                        active=self.decision('automatic_image_repair',slot+(':'+menu_round if menu_round else ''),
                            {'meal':self.draft['meals'][slot],'reason_codes':list(error.reason_codes)})
                    else:break
            # AIや画像生成の成否不明・通信障害はここへ入れず、品質判定だけを対象にする。
            menu_round=self.planner.replan(slot,failure,meal_client)

    def images(self):
        from .preparation import fingerprint
        from .meal_replanning import MAX_REPLANS
        for _ in range(MAX_REPLANS*len(MEAL_SLOTS)+1):
            self.planner.check_budget()
            before=fingerprint(self.draft)
            for slot in MEAL_SLOTS:self.one(slot)
            if before==fingerprint(self.draft):
                return {'target_date':self.draft['target_date'],'state':'images_reviewed','images':dict(self.outputs)}
        raise ValueError('再計画の上限へ到達しました')

    def rejected_registration(self,slot,error,meal_client):
        """GETで終端未保存を確認した食事だけ、別メニューと新画像へ移行する。"""
        menu_round=self.planner.replan(slot,error,meal_client)
        self.one(slot,menu_round,meal_client)
        return {'target_date':self.draft['target_date'],'state':'images_reviewed','images':dict(self.outputs)}
