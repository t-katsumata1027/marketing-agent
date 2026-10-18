"""固定アカウントの全ページ証跡を作る。未対応のproviderを空と扱わない。"""
from .domain import ACCOUNT
from .replacement_contract import digest,stamp

REQUIRED_STATES=frozenset(('pending','scheduled','draft','processing','failed','published','unknown'))
POSTS='''query($input:PostsInput!,$after:String){posts(input:$input,after:$after,first:20){
 pageInfo{hasNextPage endCursor} edges{node{id status dueAt sentAt channel{id service}}}}}'''

class BufferInventoryAdapter:
    """Buffer公式の全status列挙を日付で間引かずに読む。日不明の下書きは停止対象。"""
    def __init__(self,api,organization_id):
        if not organization_id:raise ValueError('external_dispatch_unverified')
        if not callable(getattr(api,'_on_request',None)):raise ValueError('external_inventory_quota_unverified')
        self.api,self.organization=api,organization_id
        self.usage_counted=True

    def __call__(self,channel,binding,target,cursor):
        from .buffer_trial import ORGANIZATIONS,CHANNELS
        from .instagram_domain import HANDLE
        from .domain import JST
        from datetime import datetime
        organizations=self.api._request(ORGANIZATIONS)['account']['organizations']
        if sum(o.get('id')==self.organization for o in organizations)!=1:raise ValueError('external_dispatch_unverified')
        rows=self.api._request(CHANNELS,{'input':{'organizationId':self.organization}})['channels']
        matches=[r for r in rows if r.get('id')==binding]
        service='twitter' if channel=='x' else 'instagram';name=ACCOUNT if channel=='x' else HANDLE
        if len(matches)!=1 or matches[0].get('service')!=service or matches[0].get('name','').lstrip('@').lower()!=name.lower() or any(matches[0].get(k) is not False for k in ('isDisconnected','isLocked','isQueuePaused')):
            raise ValueError('external_dispatch_unverified')
        # statusと日付をfilterしない。未定日draftや新statusを取り落とさない。
        result=self.api._request(POSTS,{'input':{'organizationId':self.organization,'filter':{'channelIds':[binding]}},'after':cursor})['posts']
        page=result['pageInfo'];items=[];seen=set()
        for edge in result['edges']:
            post=edge['node']
            if not isinstance(post.get('id'),str) or post['id'] in seen or post.get('channel')!={'id':binding,'service':service} or post.get('status') not in ('draft','error','needs_approval','scheduled','sending','sent'):
                raise ValueError('external_dispatch_unverified')
            seen.add(post['id']);dates=[]
            for field in ('dueAt','sentAt'):
                value=post.get(field)
                if value is not None:
                    when=datetime.fromisoformat(value.replace('Z','+00:00'))
                    if when.tzinfo is None:raise ValueError('external_dispatch_unverified')
                    dates.append(when.astimezone(JST).date().isoformat())
            if not dates or target in dates:items.append({'post_digest':digest(post['id']),'status':post['status']})
        return {'account':ACCOUNT,'channel':binding,'date':target,'covered_states':sorted(REQUIRED_STATES),'complete':True,
                'items':items,'has_next':page['hasNextPage'],'end_cursor':page.get('endCursor')}

class ExternalInventory:
    def __init__(self,store,read_page,clock,channels):
        self.store,self.read_page,self.clock=store,read_page,clock
        if set(channels)!= {'x','instagram'} or any(not v for v in channels.values()):raise ValueError('external_dispatch_unverified')
        self.channels=channels

    def check(self,target):
        started=self.clock();pages=[]
        for channel,binding in self.channels.items():
            cursor=None;seen=set()
            for _ in range(100):
                # 実providerアダプターも共通Buffer枠を先に消費する。
                from .buffer_limits import record_request
                if not getattr(self.read_page,'usage_counted',False):record_request(self.store,'query replacement_inventory')
                page=self.read_page(channel,binding,target,cursor)
                if (page.get('account')!=ACCOUNT or page.get('channel')!=binding or page.get('date')!=target
                        or page.get('complete') is not True or set(page.get('covered_states',[]))!=REQUIRED_STATES
                        or not isinstance(page.get('items'),list) or type(page.get('has_next')) is not bool):
                    raise ValueError('external_dispatch_unverified')
                # 一件でも対象あり・対応不明なら、ローカル台帳0件でも自動対象外。
                if page['items']:raise ValueError('external_dispatch_present')
                pages.append({'channel':channel,'cursor_digest':digest(cursor),'response_digest':digest(page)})
                if not page['has_next']:break
                cursor=page.get('end_cursor')
                if not isinstance(cursor,str) or not cursor or cursor in seen:raise ValueError('external_inventory_incomplete')
                seen.add(cursor)
            else:raise ValueError('external_inventory_incomplete')
        if (self.clock()-started).total_seconds()>120:raise ValueError('external_inventory_stale')
        receipt={'target_date':target,'account':ACCOUNT,'read_started_at':stamp(started),'checked_at':stamp(self.clock()),'pages':pages}
        with self.store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS replacement_inventory_checks (digest TEXT PRIMARY KEY,target_date TEXT NOT NULL,receipt TEXT NOT NULL)')
            from .replacement_contract import canonical
            db.execute('INSERT OR IGNORE INTO replacement_inventory_checks VALUES(?,?,?)',(digest(receipt),target,canonical(receipt).decode()))
        return receipt
