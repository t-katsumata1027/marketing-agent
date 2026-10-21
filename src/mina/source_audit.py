"""公開前の各読取りと実画像取得を、同じ公開枠の履歴へ結ぶ。"""
from datetime import UTC,datetime


class SourceClient:
    def __init__(self,client,audit,candidate,revision):
        self.client,self.audit,self.candidate,self.revision=client,audit,candidate,revision
        self.states=0

    def day_state(self,target):
        self.states+=1
        stage='source_state' if self.states==1 else 'source_final'
        with self.audit.operation('publication',target,self.candidate.slot,self.revision,stage):
            self.client.clock_observer=lambda delta:self.audit.emit('publication',target,self.candidate.slot,self.revision,stage,'observed',details={'clock_ahead_ms':round(delta,3)})
            try:
                result=self.client.day_state(target)
            finally:
                self.client.clock_observer=None
            checked=datetime.fromisoformat(result['checked_at'])
            delta=(checked-datetime.now(UTC)).total_seconds()*1000
            self.audit.emit('publication',target,self.candidate.slot,self.revision,stage,'observed',details={'clock_ahead_ms':round(delta,3)})
            return result

    def request_state(self,key,operation):
        with self.audit.operation('publication',self.candidate.target_date,self.candidate.slot,self.revision,'source_request'):
            return self.client.request_state(key,operation)


def verify_audited(candidate,client,host,capture,store):
    from .m1_contract import verify_source
    row=store.get(candidate.target_date,candidate.slot)
    if not row or row['digest']!=candidate.digest:
        raise ValueError('公開前照合の対象版が変わりました')
    wrapped=SourceClient(client,store.audit,candidate,row['revision'])
    def image(url,allowed):
        with store.audit.operation('publication',candidate.target_date,candidate.slot,row['revision'],'source_image'):
            return capture(url,allowed)
    return verify_source(candidate,wrapped,host,image)
