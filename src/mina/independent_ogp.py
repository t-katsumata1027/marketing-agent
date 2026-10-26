"""外部CLIを使わず、実画像へ束縛したroot保持の独立点検証跡を待つ。"""
import hashlib,json,os,time
from datetime import UTC,datetime
from pathlib import Path
from .domain import instant
from .preparation import fingerprint
from .preparation_status import QualityHold,PreparationDeadline


class IndependentOgp:
    last_review_evidence=None
    def __init__(self,work,receipts,deadline,clock=lambda:datetime.now(UTC),sleep=time.sleep):
        self.work,self.receipts=Path(work),Path(receipts)
        self.deadline,self.clock,self.sleep=deadline,clock,sleep

    def review_ogp(self,images,expected,meta):
        """要求を一度固定し、同じ画像/本文の独立判定だけを受け入れる。"""
        slot=expected['slot']
        if slot not in ('dinner','daily'):raise ValueError('independent_review_slot_invalid')
        request={'expected':expected,'meta':meta,'image_sha256':[hashlib.sha256(raw).hexdigest() for raw in images]}
        sha=fingerprint(request);folder=self.work/slot
        folder.mkdir(mode=0o700)
        (folder/'request.json').write_text(json.dumps(request,ensure_ascii=False),encoding='utf-8')
        for i,raw in enumerate(images):(folder/f'image-{i}.jpg').write_bytes(raw)
        receipt=self.receipts/(slot+'.json')
        while not receipt.exists():
            if instant(self.clock())>=self.deadline:raise PreparationDeadline('independent_review_deadline')
            self.sleep(2)
        if instant(self.clock())>=self.deadline:raise PreparationDeadline('independent_review_deadline')
        if receipt.is_symlink() or receipt.stat().st_uid!=0 or receipt.stat().st_mode&0o022:raise ValueError('independent_review_receipt_owner')
        value=json.loads(receipt.read_text())
        if (set(value)!={'request_sha256','image_sha256','reviewer','review_reference','status','reason_codes'}
                or value['request_sha256']!=sha or value['image_sha256']!=request['image_sha256']
                or value['reviewer']!='/root/review_x_comment_fix' or not value['review_reference']):raise ValueError('independent_review_receipt_binding')
        if value['status']!='publishable' or value['reason_codes']:raise QualityHold('独立実画像点検の保留','ogp_review:'+slot,value['reason_codes'])
        return {'status':'publishable','reason_codes':[]}

    def __getattr__(self,name):raise ValueError('independent_review_unexpected_ai_call')
