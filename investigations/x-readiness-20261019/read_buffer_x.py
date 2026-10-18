"""既存の無料枠を計数し、固定Xの全状態を照会する。mutationは拒否する。"""
import json,os,sqlite3,sys,time
from contextlib import contextmanager
from datetime import datetime,UTC
from zoneinfo import ZoneInfo
sys.path.insert(0,'/opt/marketing-agent')
from src.mina.buffer_trial import BufferTrialClient,ORGANIZATIONS,CHANNELS
from src.mina.buffer_limits import record_request
POSTS='''query($input:PostsInput!,$after:String){posts(input:$input,after:$after,first:20){pageInfo{hasNextPage endCursor} edges{node{id status dueAt sentAt channel{id service}}}}}'''
target='2026-10-19';start=time.monotonic();calls=[]
class ExistingUsage:
 @contextmanager
 def transaction(self):
  db=sqlite3.connect('file:/var/lib/mina-agent/mina.sqlite3?mode=rw',uri=True);db.row_factory=sqlite3.Row
  try:
   db.execute('BEGIN IMMEDIATE');yield db;db.commit()
  except BaseException:db.rollback();raise
  finally:db.close()
usage=ExistingUsage()
def count(q):
 if q not in (ORGANIZATIONS,CHANNELS,POSTS) or 'mutation' in q or len(calls)>=16 or time.monotonic()-start>=100:raise ValueError('readonly_inventory_limit')
 record_request(usage,q);calls.append('posts' if q==POSTS else 'organizations' if q==ORGANIZATIONS else 'channels')
api=BufferTrialClient(os.environ['BUFFER_API_KEY'],on_request=count)
out={'target_date':target,'observed_at':datetime.now(UTC).isoformat(),'external_mutations':0,'business_writes':0,'usage_accounting_only':True,'channels':[]}
try:
 organizations=api._request(ORGANIZATIONS)['account']['organizations'];matches=[]
 if not 1<=len(organizations)<=5:raise ValueError('organization_unconfirmed')
 for org in organizations:
  channels=api._request(CHANNELS,{'input':{'organizationId':org['id']}})['channels']
  selected={}
  for kind,service,name in [('x','twitter','EatsLog88161')]:
   rows=[r for r in channels if r.get('service')==service and r.get('name','').lstrip('@').lower()==name.lower()]
   if len(rows)==1:selected[kind]=rows[0]
  if set(selected)=={'x'}:matches.append((org['id'],selected))
 if len(matches)!=1:raise ValueError('fixed_accounts_unconfirmed')
 org,selected=matches[0]
 for kind,channel in selected.items():
  if any(channel.get(k) is not False for k in ('isDisconnected','isLocked','isQueuePaused')):raise ValueError('channel_unavailable')
  cursor=None;seen=set();counts={};entry={'channel':kind,'connected':True,'complete':False,'pages':0,'target_or_undated_posts':0,'undated_posts':0,'unknown_status_posts':0}
  out['channels'].append(entry)
  for _ in range(5):
   value=api._request(POSTS,{'input':{'organizationId':org,'filter':{'channelIds':[channel['id']]}},'after':cursor})['posts'];entry['pages']+=1
   for edge in value['edges']:
    p=edge['node'];status=p.get('status')
    if p.get('channel')!={'id':channel['id'],'service':channel['service']}:raise ValueError('post_channel_unconfirmed')
    if status not in ('draft','error','needs_approval','scheduled','sending','sent'):entry['unknown_status_posts']+=1
    else:counts[status]=counts.get(status,0)+1
    dates=[]
    for f in ('dueAt','sentAt'):
     if p.get(f):
      at=datetime.fromisoformat(p[f].replace('Z','+00:00'))
      if at.tzinfo is None:raise ValueError('post_date_unconfirmed')
      dates.append(at.astimezone(ZoneInfo('Asia/Tokyo')).date().isoformat())
    if not dates:entry['undated_posts']+=1
    if not dates or target in dates:entry['target_or_undated_posts']+=1
   page=value['pageInfo']
   if page['hasNextPage'] is False:entry['complete']=True;break
   cursor=page.get('endCursor')
   if not cursor or cursor in seen:raise ValueError('cursor_unconfirmed')
   seen.add(cursor)
  entry['counts']=counts
 out['inventory_complete']=all(c['complete'] for c in out['channels'])
 out['no_target_or_unknown_posts']=out['inventory_complete'] and all(c['target_or_undated_posts']==0 and c['unknown_status_posts']==0 for c in out['channels'])
except Exception as error:
 from src.mina.audit import safe_failure
 out.update(inventory_complete=False,no_target_or_unknown_posts=False,reason=safe_failure(error)[0])
out['requests']=len(calls);out['finished_at']=datetime.now(UTC).isoformat();print(json.dumps(out,ensure_ascii=False,indent=2))
