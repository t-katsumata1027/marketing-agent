"""公開前停止した夕食の再承認・Buffer一度送信と、他枠の保全を検証する。"""
import hashlib,json,tempfile,unittest
from dataclasses import replace
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
from src.mina.domain import Candidate,JST
from src.mina.store import Store
from src.mina.buffer_publisher import BufferPublisher
from src.mina.runtime import publish_due
from src.mina.missed_dinner import recover_missed_dinner
import test_daily as daily
import test_readiness as readiness

NOW=datetime(2026,10,8,19,50,tzinfo=JST)
KEY='11111111-1111-4111-8111-111111111111'
RAW=b'\xff\xd8\xfforiginal-dinner'


class MissedDinnerTests(unittest.TestCase):
    def setup_case(self,root):
        store=Store(root/'db')
        c=daily.candidate('dinner')
        c=replace(c,image_sha256=hashlib.sha256(RAW).hexdigest(),publish_text=c.content,approval_source={'request_key':KEY,'operation':'meal_correction',
            'input_hash':'ih1_'+'a'*64,'state_version':'sv1_'+'b'*64,'result_sha256':'c'*64})
        before=NOW-timedelta(hours=2)
        store.save(c,daily.HOST,before)
        card=store.issue_cards(c.target_date,before)[0]
        old_key=card['components'][0]['components'][0]['custom_id']
        store.decide(old_key,'本人',before)
        row=store.claim_due(c.scheduled_at)[0]
        with store.transaction() as db:
            db.execute("INSERT INTO buffer_publication(target_date,slot,revision,digest,channel_id,service_id,state) VALUES(?,'dinner',1,?,'channel','native-account','armed')",(c.target_date,c.digest))
        store.stop_before_publish(row,c.scheduled_at)
        other=daily.candidate('daily')
        store.save(other,daily.HOST,before)
        untouched=store.get(c.target_date,'daily')
        replacement=replace(c,source_commit='d'*40,approval_source={**c.approval_source,'review_round':KEY,
            'publish_after_approval_until':NOW.replace(hour=21,minute=15).isoformat(),'readiness_sha256':'e'*64})
        return store,c,replacement,old_key,untouched

    def prepare_review(self,root,old):
        image=root/'dinner.jpg';image.write_bytes(RAW)
        draft=root/'draft.json';draft.write_text(json.dumps({'target_date':old.target_date,
            'meals':{'dinner':{'menu':old.menu,'comment':old.comment,'foods':[{'name':'食材','amount':'100g'}]}}}))
        saved=root/'drive.json';saved.write_text(json.dumps({'target_date':old.target_date,'images':{'dinner':{'path':str(image)}}}))
        evidence={**readiness.evidence(),'checked_at':NOW.isoformat()}
        ai=Mock();ai.review_meal.return_value=ai.review_ogp.return_value={'status':'publishable','reason_codes':[]}
        client=Mock();client.request_state.return_value={'current_result':{'ogp':{}}}
        capture=Mock(return_value={'digest':old.ogp_sha256,'meta':{},'review_image':b'preview'})
        return draft,saved,evidence,ai,client,capture

    def test_recovery_pipeline_reviews_pixels_and_queues_only_dinner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store,old,_,_,untouched=self.setup_case(root)
            draft,saved,evidence,ai,client,capture=self.prepare_review(root,old)
            with patch('src.mina.missed_dinner.release_commit',return_value='d'*40), \
                 patch('src.mina.missed_dinner.verify_source') as verify, \
                 patch('src.mina.missed_dinner.completed_request'):
                result=recover_missed_dinner(store,old.target_date,KEY,draft,saved,evidence,root,'d'*40,
                    daily.HOST,ai,client,lambda:NOW,capture)
            self.assertEqual(result['revision'],2)
            self.assertEqual(verify.call_count,2)
            ai.review_meal.assert_called_once();ai.review_ogp.assert_called_once()
            self.assertEqual(store.get(old.target_date,'daily'),untouched)
            self.assertEqual(store.get(old.target_date,'dinner')['state'],'approval_pending')

    def test_hold_or_changed_source_after_review_preserves_stopped_revision(self):
        for mode in ('hold','changed'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);store,old,_,_,_=self.setup_case(root)
                draft,saved,evidence,ai,client,capture=self.prepare_review(root,old)
                if mode=='hold':ai.review_meal.return_value={'status':'hold','reason_codes':['image_analysis_mismatch']}
                with patch('src.mina.missed_dinner.release_commit',return_value='d'*40), \
                     patch('src.mina.missed_dinner.verify_source',side_effect=[None,ValueError('正本変更')] if mode=='changed' else None), \
                     patch('src.mina.missed_dinner.completed_request'):
                    with self.assertRaises(ValueError):recover_missed_dinner(store,old.target_date,KEY,draft,saved,evidence,
                        root,'d'*40,daily.HOST,ai,client,lambda:NOW,capture)
                self.assertEqual(store.get(old.target_date,'dinner')['state'],'changes_requested')
                self.assertEqual(store.get(old.target_date,'dinner')['revision'],1)

    def test_new_card_revokes_old_and_buffer_publishes_once_only_after_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            store,old,c,old_key,untouched=self.setup_case(Path(tmp))
            self.assertEqual(store.requeue_failed_dinner(c,daily.HOST,NOW,old.digest,KEY),2)
            self.assertEqual(store.get(c.target_date,'daily'),untouched)
            self.assertEqual(publish_due(store,Mock(),NOW,daily.HOST,Mock()),0)
            with self.assertRaises(ValueError):store.decide(old_key,'本人',NOW)
            card=next(card for card in store.issue_cards(c.target_date,NOW) if '夕食｜' in card['content'])
            self.assertIn('本日の夕食は承認後に臨時公開',card['content'])
            store.decide(card['components'][0]['components'][0]['custom_id'],'本人',NOW)
            self.assertEqual(store.get(c.target_date,'dinner')['scheduled'],NOW.timestamp())
            api=Mock()
            api.channel.return_value={'id':'channel','serviceId':'native-account','isQueuePaused':False}
            post={'id':'buffer-post','text':c.content,'channel':{'id':'channel','service':'twitter'},'status':'sent',
                  'sentAt':NOW.isoformat(),'externalLink':'https://x.com/EatsLog88161/status/12345'}
            api._request.return_value={'createPost':{'__typename':'PostActionSuccess','post':post}}
            proof={name:NOW.isoformat() for name in ('started_at','observed_at','checked_at')}
            proof.update(expires_at=(NOW+timedelta(hours=1)).isoformat(),started_monotonic=1,observed_monotonic=1)
            with patch.dict('os.environ',{'MINA_LIVE_WRITES':'true','MINA_PUBLISH_ENABLED':'true'}), \
                 patch('src.mina.buffer_publisher.production_client',return_value=api), \
                 patch('src.mina.buffer_publisher.BufferPublisher',side_effect=lambda store,row,route,api:
                    BufferPublisher(store,row,route,api,clock=lambda:NOW,timer=lambda:1,sleep=Mock())):
                self.assertEqual(publish_due(store,Mock(),NOW,daily.HOST,lambda c:proof,clock=lambda:NOW,timer=lambda:1),1)
                self.assertEqual(publish_due(store,Mock(),NOW,daily.HOST,Mock(),clock=lambda:NOW,timer=lambda:1),0)
            self.assertEqual(api._request.call_count,1)
            self.assertEqual(store.get(c.target_date,'dinner')['state'],'published')
            with store.transaction() as db:
                archived=db.execute('SELECT previous_route FROM publication_recoveries').fetchone()[0]
                self.assertEqual(json.loads(archived)['revision'],1)

    def test_attempted_unknown_published_changed_or_expired_dinner_never_requeues(self):
        for mode in ('attempted','unknown','published','changed','expired','stale_digest'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                store,old,c,_,_=self.setup_case(Path(tmp))
                with store.transaction() as db:
                    if mode=='attempted':db.execute("UPDATE buffer_publication SET attempted=?,state='accepted'",(NOW.timestamp(),))
                    if mode in ('unknown','published'):
                        db.execute("UPDATE candidates SET state=? WHERE slot='dinner'",('outcome_unknown' if mode=='unknown' else 'published',))
                if mode=='changed':c=replace(c,menu='別の献立')
                now=NOW.replace(hour=21,minute=15) if mode=='expired' else NOW
                with self.assertRaises(ValueError):store.requeue_failed_dinner(c,daily.HOST,now,'f'*64 if mode=='stale_digest' else old.digest,KEY)
                with store.transaction() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM publication_exceptions').fetchone()[0],0)

    def test_missing_explicit_exception_record_never_accepts_dinner(self):
        with tempfile.TemporaryDirectory() as tmp:
            store,old,c,_,_=self.setup_case(Path(tmp))
            with self.assertRaises(ValueError):store.save(c,daily.HOST,NOW)

    def test_buffer_rejects_wrong_approval_time_and_expired_recovery_before_send(self):
        for mode in ('wrong_time','expired'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                store,old,c,_,_=self.setup_case(Path(tmp))
                store.requeue_failed_dinner(c,daily.HOST,NOW,old.digest,KEY)
                card=next(card for card in store.issue_cards(c.target_date,NOW) if '夕食｜' in card['content'])
                store.decide(card['components'][0]['components'][0]['custom_id'],'本人',NOW)
                row=store.claim_due(NOW)[0]
                if mode=='wrong_time':row['approved']-=1
                with store.transaction() as db:route=dict(db.execute('SELECT * FROM buffer_publication').fetchone())
                api=Mock()
                publisher=BufferPublisher(store,row,route,api,clock=lambda:c.scheduled_at if mode=='expired' else NOW,timer=lambda:1)
                publisher.proof={'started_monotonic':1,'expires_at':(NOW+timedelta(hours=2)).isoformat()}
                with patch.dict('os.environ',{'MINA_LIVE_WRITES':'true','MINA_PUBLISH_ENABLED':'true'}):
                    with self.assertRaises(Exception):publisher.publish(c.content)
                api._request.assert_not_called()
