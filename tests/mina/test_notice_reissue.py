"""表示失敗・差戻しの履歴と七承認を保持した一件の実添付再通知を検証する。"""
import copy,hashlib,io,json,unittest,uuid
from dataclasses import asdict,replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import test_morning_preparation as fixture
from src.mina import notice_reissue as r,morning_registry as m
from src.mina.notice_attachment import validate_actual,canonical_upload,AttachedDiscord
from src.mina.preparation import fingerprint
from src.mina.domain import JST
from src.mina.routine_handoff import matches

class ReissueTests(unittest.TestCase):
 def setUp(self):
  self.f=fixture.CohortTests();self.f.setUp();self.addCleanup(self.f.doCleanups);self.store=self.f.store;self.ig=self.f.ig
  # 限定契約の日付を試験時計へ固定し、翌日以降のCIでも実時計に依存しない。
  clock_patch=patch('src.mina.notice_reissue.datetime',wraps=datetime);clock_patch.start().now.return_value=self.f.now;self.addCleanup(clock_patch.stop)
  self.path=Path(self.f.tmp.name)/'dinner-story.jpg';image=io.BytesIO();Image.new('RGB',(1080,1920),'white').save(image,'JPEG');self.raw=image.getvalue();self.path.write_bytes(self.raw)
  self.asset={'path':str(self.path),'sha256':hashlib.sha256(self.raw).hexdigest(),'role':'dinner_story','width':1080,'height':1920,'mime_type':'image/jpeg','size':len(self.raw),'url':'https://drive.google.com/uc?id=fixture'}
  with self.store.transaction() as db:
   old=dict(db.execute("SELECT * FROM instagram_candidates WHERE slot='dinner'").fetchone());payload=json.loads(old['payload']);payload['assets']=[self.asset]
   from src.mina.instagram_domain import InstagramCandidate
   c=InstagramCandidate(**payload);db.execute("UPDATE instagram_candidates SET payload=?,digest=? WHERE slot='dinner'",(json.dumps(payload),c.digest));db.execute("UPDATE morning_members SET digest=?,payload_digest=? WHERE platform='instagram' AND slot='dinner'",(c.digest,fingerprint(payload)))
  self.f.sent('meals');self.f.sent('daily');self.f.approve('meals');self.f.approve('daily')
  with self.store.transaction() as db:
   db.execute("UPDATE instagram_candidates SET state='changes_requested' WHERE slot='dinner'")
   old=dict(db.execute("SELECT * FROM instagram_candidates WHERE slot='dinner'").fetchone());self.old=old
   self.before={t:[dict(v) for v in db.execute('SELECT * FROM '+t+" WHERE NOT(slot='dinner' AND '"+t+"'='instagram_candidates')")] for t in ('candidates','instagram_candidates')}
   notice=db.execute("SELECT * FROM instagram_notices WHERE slot='dinner'").fetchone();receipt=db.execute("SELECT * FROM routine_notice_receipts WHERE platform='instagram' AND notice_id=?",(notice['id'],)).fetchone();self.mid=receipt['message_id']
  self.display=copy.deepcopy(self.f.discord.messages[self.mid]);self.display['embeds'][0]['image'].update(width=0,height=0);self.display['attachments']=[]
  self.config={'contract_version':r.VERSION,'request_uuid':str(uuid.uuid4()),'target_date':m.TARGET,'slot':'dinner','not_after':r.END.isoformat(),'approval_reference':'本人の画像未表示・差戻し後再通知の復旧依頼','commit':'c'*40,'case_uuid':self.f.key,'old_revision':1,'digest':old['digest'],'asset':self.asset,'channel':self.f.discord.channel,'old_message_id':self.mid}
 def test_same_body_image_schedule_seven_approvals_and_history_are_preserved(self):
  r.register(self.store,self.config,'f'*64,self.display)
  with self.store.transaction() as db:
   row=dict(db.execute("SELECT * FROM instagram_candidates WHERE slot='dinner'").fetchone());history=db.execute('SELECT * FROM notice_reissues').fetchone()
   self.assertEqual(json.loads(history['old_row']),self.old);self.assertEqual(row['revision'],2);self.assertEqual(row['state'],'approval_pending')
   for k in ('payload','digest','scheduled'):self.assertEqual(row[k],self.old[k])
   self.assertFalse(m.receipts(db,m.job(db,m.TARGET),'meals'))
   for table,rows in self.before.items():self.assertEqual([dict(v) for v in db.execute('SELECT * FROM '+table+" WHERE NOT(slot='dinner' AND '"+table+"'='instagram_candidates')")],rows)
  with self.assertRaises(ValueError):r.register(self.store,self.config,'f'*64,self.display)
 def test_wrong_channel_author_or_message_id_is_rejected_before_revision_change(self):
  for field,value in (('channel','wrong'),('old_message_id','999999')):
   with self.assertRaises(ValueError):r.register(self.store,{**self.config,field:value},'f'*64,self.display)
  changed=copy.deepcopy(self.display);changed['author']['id']='other'
  with self.assertRaises(ValueError):r.register(self.store,self.config,'f'*64,changed)
  with self.store.transaction() as db:self.assertEqual(db.execute("SELECT revision FROM instagram_candidates WHERE slot='dinner'").fetchone()[0],1)
 def test_normal_notifier_and_reconciler_cannot_use_attachment_only_notice(self):
  r.register(self.store,self.config,'f'*64,self.display,ig=self.ig)
  with self.store.transaction() as db:
   notice=dict(db.execute("SELECT * FROM instagram_notices WHERE slot='dinner' AND revision=2").fetchone());self.assertFalse(m.notice(db,notice,'instagram'))
   db.execute("UPDATE instagram_notices SET state='pending' WHERE id=?",(notice['id'],))
  self.assertEqual(self.ig.send_notices(self.f.discord),0)
  from src.mina.routine_handoff import reconcile
  self.assertFalse(reconcile(self.store,'instagram',notice['id'],self.f.discord))
 def actual(self):
  return {'attachments':[{'filename':self.path.name,'url':'https://cdn.discordapp.com/attachments/1/2/dinner-story.jpg','content_type':'image/jpeg','size':len(self.raw)}],'embeds':[{'image':{'url':'https://cdn.discordapp.com/attachments/1/2/dinner-story.jpg','width':1080,'height':1920}}]}
 def test_actual_attachment_bytes_and_visible_dimensions_required(self):
  class Response(io.BytesIO):pass
  class Opener:
   def __init__(self,raw):self.raw=raw
   def open(self,*a,**k):return Response(self.raw)
  a=self.actual();self.assertTrue(validate_actual(a,self.asset,Opener(self.raw)))
  a['embeds'][0]['image']['width']=0
  with self.assertRaises(ValueError):validate_actual(a,self.asset,Opener(self.raw))
  with self.assertRaises(ValueError):validate_actual(self.actual(),self.asset,Opener(b'wrong'))
 def test_discord_embedded_upload_is_explicitly_normalized_then_byte_checked(self):
  class Response(io.BytesIO):pass
  class Opener:
   def open(_, *a,**k):return Response(self.raw)
  raw=self.actual();raw['channel_id']='1';raw['attachments']=[]
  view=canonical_upload(raw,self.asset);self.assertEqual(view['_mina_attachment_representation'],'embedded_upload');self.assertEqual(raw['attachments'],[])
  self.assertTrue(validate_actual(view,self.asset,Opener()))
  raw['embeds'][0]['image']['url']='https://drive.google.com/uc?id=bad'
  self.assertEqual(canonical_upload(raw,self.asset)['attachments'],[])
 def test_raw_consumed_upload_is_preserved_and_sender_requires_persistent_nonce(self):
  from unittest.mock import Mock
  raw=self.actual();raw['channel_id']='1';raw['attachments']=[]
  client=Mock(channel='1');client.message.return_value=raw;wrapped=AttachedDiscord(client,self.asset)
  class Opener:
   def open(_, *a,**k):return io.BytesIO(self.raw)
  with patch('src.mina.notice_attachment.build_opener',return_value=Opener()):value=wrapped.message('123')
  proof=value['_mina_attachment_proof'];self.assertEqual(proof['raw_message'],raw);self.assertEqual(proof['raw_attachments_count'],0);self.assertIsNone(proof['api_image_content_type']);self.assertEqual(proof['validated_mime_type'],'image/jpeg')
  with self.assertRaises(ValueError):wrapped.send({'attachments':[{'id':0,'filename':self.path.name}],'embeds':[{'image':{'url':'attachment://'+self.path.name}}]})
  client.send_files.assert_not_called()
 def test_module_entry_uses_canonical_scope_and_explicit_pre_send_finish(self):
  import runpy
  with patch('sys.argv',['notice_reissue','--config','sealed','--finish-registered']),patch('src.mina.notice_reissue.run') as main:
   runpy.run_module('src.mina.notice_reissue',run_name='__main__')
  main.assert_called_once_with('sealed',resume_registered=True,continuation_path=None)
 def test_complete_operator_uses_one_multipart_and_same_id_get(self):
  self.operator(False)
 def test_registered_before_post_finishes_same_nonce_and_never_registers_again(self):
  self.operator(True)
 def operator(self,resume):
  outer=self
  class Client:
   channel=outer.f.discord.channel;posts=0
   def identity(self):return '999'
   def message(self,key):return outer.display if key==outer.mid else self.actual
   def send_files(self,wire,files):
    self.posts+=1;self.actual={**copy.deepcopy(wire),**outer.actual(),'id':'99999','channel_id':self.channel,'author':{'id':'999'},'mentions':[],'mention_roles':[],'mention_everyone':False};return self.actual
  client=Client()
  class Response(io.BytesIO):pass
  class Opener:
   def open(self,*a,**k):return Response(outer.raw)
  continuation={}
  if resume:
   r.register(self.store,self.config,'f'*64,self.display,ig=self.ig)
   with self.store.transaction() as db:
    notice=db.execute("SELECT * FROM instagram_notices WHERE slot='dinner' AND revision=2").fetchone();old_wire=notice['payload']
   continuation={'contract_version':r.VERSION,'mode':'finish_registered_before_post','config_sha256':'f'*64,'request_uuid':self.config['request_uuid'],'notice_id':notice['id'],'new_revision':2,'not_after':r.END.isoformat(),'approval_reference':'同じ送信前要求の継続','commit':'d'*40}
  def protected(path,**kwargs):return (continuation,'e'*64) if path=='continuation' else (self.config,'f'*64)
  with patch('src.mina.current_day_cli.protected_json',side_effect=protected),patch('src.mina.readiness.release_commit'),patch('src.mina.store.Store',return_value=self.store),patch('src.mina.instagram_store.InstagramStore',return_value=self.ig),patch('src.mina.clients.DiscordClient',return_value=client),patch('src.mina.notice_attachment.build_opener',return_value=Opener()),patch.dict('os.environ',{'DISCORD_BOT_TOKEN':'fake'}):r.run('fake',resume_registered=resume,continuation_path='continuation' if resume else None)
  self.assertEqual(client.posts,1)
  with self.store.transaction() as db:
   self.assertTrue(m.receipts(db,m.job(db,m.TARGET),'meals'));self.assertEqual(db.execute('SELECT state FROM notice_reissues').fetchone()[0],'sent')
   self.assertIsNone(m.members(db,m.job(db,m.TARGET),'meals',True))
   if resume:self.assertEqual(db.execute('SELECT payload FROM instagram_notices WHERE id=?',(notice['id'],)).fetchone()[0],old_wire)
  self.assertEqual([v['slot'] for v in self.store.claim_due(self.f.xs[0].scheduled_at)],['breakfast'])
  with self.store.transaction() as db:new_notice=json.loads(db.execute("SELECT payload FROM instagram_notices WHERE slot='dinner' AND revision=2").fetchone()[0])
  token=new_notice['components'][0]['components'][0]['custom_id'];self.ig.decide(token,'human',self.f.now)
  with self.store.transaction() as db:self.assertEqual(db.execute("SELECT state FROM morning_cohorts WHERE cohort='meals'").fetchone()[0],'ready')

if __name__=='__main__':unittest.main()
