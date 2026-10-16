"""通常準備内だけで行う、回数制限付きの画像修復。原記録は保持する。"""

import uuid

from .generation import MEAL_SLOTS
from .preparation import PreparationJournal, prepare_images
from .preparation_status import QualityHold


class AutomaticImageRepair:
    """同一献立の画像を一枠一回だけ修復する。登録要求は作成しない。"""

    def __init__(self, store, draft, draft_path, directory, ai, drive):
        self.store, self.draft, self.draft_path = store, draft, draft_path
        self.directory, self.ai, self.drive = directory, ai, drive
        self.rounds = {}
        self.journal = PreparationJournal(store)

    def decision(self, kind, slot, inputs):
        return self.journal.run(self.draft['target_date'], kind + ':' + slot, inputs,
                                lambda: {'round': str(uuid.uuid4())})['round']

    def images(self):
        """食品・量の画像不一致だけを修復する。表現・文脈などの保留は通さない。"""
        repaired = set()
        while True:
            try:
                return prepare_images(self.store, self.draft_path, self.directory, self.ai,
                                      reused_rounds=self.rounds)
            except QualityHold as error:
                parts = error.step.split(':')
                if (len(parts) != 2 or parts[0] != 'image_review' or parts[1] not in MEAL_SLOTS or
                        not error.reason_codes or set(error.reason_codes) != {'image_analysis_mismatch'} or
                        parts[1] in repaired):
                    raise
                slot = parts[1]
                repaired.add(slot)
                round_key = self.decision('automatic_image_repair', slot,
                    {'meal': self.draft['meals'][slot], 'reason_codes': list(error.reason_codes)})
                # 新画像自身の独立点検が保留なら、この工程からは再生成しない。
                try:
                    result = prepare_images(self.store, self.draft_path, self.directory, self.ai,
                        replacement_round=round_key, replace_slots=(slot,), reused_rounds=self.rounds)
                except QualityHold as next_error:
                    self.rounds[slot] = round_key
                    if next_error.step == error.step:
                        raise
                    # 後続枠の保留は次の反復で元の点検結果から診断する。
                    continue
                self.rounds[slot] = round_key
                return result
