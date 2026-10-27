"""複数RPCを一括成功にせず、各実送信を同じ期限と操作台帳へ束縛する。"""
import copy
import hashlib
from contextvars import ContextVar
from .replacement_contract import digest

CURRENT=ContextVar('scoped_rescue_rpc',default=None)


def safe_input(value):
    if isinstance(value,bytes):return {'sha256':hashlib.sha256(value).hexdigest(),'byte_length':len(value)}
    if isinstance(value,(tuple,list)):return [safe_input(v) for v in value]
    if isinstance(value,dict):return {k:safe_input(v) for k,v in value.items()}
    return value


def rpc(flow,inputs,operation,*,sensitive=False):
    parent=CURRENT.get()
    if parent is None:raise ValueError('scoped_rescue_rpc_without_operation')
    parent['ordinal']+=1
    key=f"rpc:{parent['key']}:{parent['ordinal']}"
    return flow.call(key,parent['kind'],safe_input(inputs),operation,saved=parent['saved'],sensitive=sensitive)


class GuardedAI:
    def __init__(self,flow,inner):self.flow,self.inner=flow,inner
    def __getattr__(self,name):
        if name not in ('structured','image','execute','review_meal','review_ogp'):
            raise AttributeError(name)
        def invoke(*args,**kwargs):
            return rpc(self.flow,{'method':name,'args':args,'kwargs':kwargs},lambda:getattr(self.inner,name)(*args,**kwargs))
        return invoke


def guarded_drive(flow,drive):
    client=copy.copy(drive)
    # 元clientを変更せず、OAuth・metadata・upload・再取得も個別に記録する。
    client.request_guard=lambda url,data,method,operation:rpc(flow,{'url_sha256':digest(url),'body':data,'method':method},operation,
        sensitive=url=='https://oauth2.googleapis.com/token')
    return client
