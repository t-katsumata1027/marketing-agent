"""長いコメントと別メニュー再計画を通常CLIで通し、4承認カードまで検証する。"""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
import test_nightly_flow as nightly
from src.mina.runtime import notify_day,send_notifications
from src.mina.x_text import weighted_length

class XCommentContinuityTests(unittest.TestCase):
 def test_initial_long_comments_finish_four_candidates_without_duplicate_writes(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);fixture=nightly.NightlyFlowTests();flow=fixture.setup_flow(root,'plain')
   store,clock,ai,drive,meal,_=flow
   for slot,label in (('breakfast','朝'),('lunch','昼'),('dinner','夕')):
    ai.plan['meals'][slot]['comment']=(label+'の献立の組み合わせを紹介します。')*5
   ai.plan['daily_comment']='一日の献立の組み合わせを紹介します。'*5
   structured=ai.structured;fits=[]
   def generated(instructions,content,schema,name):
    if name=='mina_comment_fit':
     original=json.loads(content)['original'];fits.append(original)
     return {'comment':original[:original.index('。')+1]}
    return structured(instructions,content,schema,name)
   ai.structured=generated
   fixture.prepare(root,flow,'plain')
   self.assertEqual(len(fits),4);self.assertEqual((ai.images,len(drive.uploads),len(meal.calls)),(3,3,4))
   rows=store.list_day(nightly.TARGET);self.assertEqual(len(rows),4)
   for row in rows:
    payload=json.loads(row['payload']);self.assertLessEqual(weighted_length(payload['publish_text']),280)
   notify_day(store,nightly.TARGET,nightly.NOW.replace(hour=20));discord=Mock()
   self.assertEqual(send_notifications(store,discord),4)
   fixture.prepare(root,flow,'plain')
   self.assertEqual((len(fits),ai.images,len(drive.uploads),len(meal.calls)),(4,3,3,4))
 def test_replanned_long_comment_is_fitted_before_image_upload_and_api_registration(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);fixture=nightly.NightlyFlowTests();flow=fixture.setup_flow(root,'plain')
   store,clock,ai,drive,meal,_=flow;original=copy.deepcopy(ai.plan)
   structured=ai.structured;fits=[]
   def generated(instructions,content,schema,name):
    if name=='mina_comment_fit':
     fits.append((ai.images,len(drive.uploads),len(meal.calls)))
     return {'comment':'昼は主食とおかずを組み合わせた献立案です。'}
    result=structured(instructions,content,schema,name)
    if name=='mina_meal_replan':result['comment']='昼の献立の組み合わせを紹介します。'*6
    return result
   ai.structured=generated
   ai.review_image=lambda raw,value:({'status':'hold','reason_codes':['image_analysis_mismatch']} if value==original['meals']['lunch'] else {'status':'image_acceptable','reason_codes':[]})
   fixture.prepare(root,flow,'plain')
   self.assertEqual(fits,[(3,0,0)])
   draft=json.loads((root/'daily'/nightly.TARGET/'draft.json').read_text(encoding='utf-8'))
   for slot in ('breakfast','dinner'):self.assertEqual(draft['meals'][slot],original['meals'][slot])
   self.assertEqual(draft['daily_comment'],original['daily_comment'])
   self.assertLessEqual(weighted_length(draft['meals']['lunch']['comment']),110)
   self.assertEqual(len(meal.calls),4);self.assertEqual(len(store.list_day(nightly.TARGET)),4)
   notify_day(store,nightly.TARGET,nightly.NOW.replace(hour=20));self.assertEqual(send_notifications(store,Mock()),4) def test_terminal_unsaved_replan_keeps_saved_food_and_fits_before_replacement_image(self):
  import test_automatic_repair as repair_fixtures
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);fixture=nightly.NightlyFlowTests();flow=fixture.setup_flow(root,'plain')
   store,clock,ai,drive,meal,_=flow
   repair_fixtures.AutomaticRepairTests().terminal_failure(meal,{'lunch'})
   structured=ai.structured;fits=[]
   def generated(instructions,content,schema,name):
    if name=='mina_comment_fit':
     fits.append((ai.images,len(drive.uploads),len(meal.calls)))
     return {'comment':'昼の主食とおかずを合わせた献立案です。'}
    result=structured(instructions,content,schema,name)
    if name=='mina_meal_replan':result['comment']='昼の献立の組み合わせを紹介します。'*6
    return result
   ai.structured=generated
   fixture.prepare(root,flow,'plain')
   self.assertEqual(fits,[(3,3,2)])
   calls=[(method,slot) for method,slot,key in meal.calls]
   self.assertEqual(calls.count(('POST','breakfast')),1);self.assertEqual(calls.count(('POST','lunch')),2)
   self.assertEqual((ai.images,len(drive.uploads),len(meal.calls)),(4,4,5))
   self.assertEqual(len({key for method,slot,key in meal.calls}),5)
   self.assertEqual(len(store.list_day(nightly.TARGET)),4)
   fixture.prepare(root,flow,'plain');self.assertEqual((len(fits),len(meal.calls)),(1,5))