"""本番保存後の読取再点検で要求が増えず、未知状態と不合格で止まることを確認する。"""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from src.mina.domain import JST
from src.mina.saved_day import finalize_saved_day
from src.mina.registration import register_day
from src.mina.store import Store
import test_registration

NOW = datetime(2026,10,8,13,tzinfo=JST)
HOST = 'www.log-eats.com'


class SavedDayTests(unittest.TestCase):
    def scenario(self, root):
        base=root/'2026-10-08';base.mkdir()
        helper=test_registration.RegistrationTests()
        draft,saved=helper.setup_inputs(base)
        (base/'draft.json').write_text(json.dumps(draft))
        (base/'drive.json').write_text(json.dumps(saved))
        store,client,ai=Store(root/'db'),helper.client(),Mock()
        capture=lambda url,host:{'meta':{'og:image':url+'/og-image','image_sha256':'e'*64},'digest':'d'*64,'review_image':b'\xff\xd8\xffpreview'}
        positive={'status':'publishable','reason_codes':[]}
        ai.review_meal.return_value=positive
        ai.review_ogp.side_effect=[positive,positive,positive,{'status':'hold','reason_codes':['share_ogp_mismatch']}]
        with self.assertRaisesRegex(ValueError,'日次OGP'):
            register_day(store,draft,saved,base/'ogp',client,ai,HOST,capture)
        with store.transaction() as db:
            db.execute("INSERT INTO publication_exclusions VALUES('2026-10-08','breakfast','本人指定')")
        original=client.day_state.side_effect
        def state(target):
            value=original(target);value['checked_at']=NOW.isoformat();return value
        client.day_state.side_effect=state
        client.register.reset_mock();client.json_write.reset_mock()
        ai.review_ogp.side_effect=None;ai.review_ogp.return_value=positive
        ai.review_meal.reset_mock()
        return store,client,ai,capture

    def run_finish(self,root,store,client,ai,capture):
        with patch('src.mina.saved_day.release_commit',return_value='a'*40):
            return finalize_saved_day(store,root,root,'a'*40,HOST,ai,client,
                '11111111-1111-4111-8111-111111111111',clock=lambda:NOW,capture=capture)

    def test_saved_requests_are_read_only_and_old_hold_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store,client,ai,capture=self.scenario(root)
            self.assertEqual(self.run_finish(root,store,client,ai,capture)['state'],'posts_reviewed')
            client.register.assert_not_called();client.json_write.assert_not_called()
            ai.review_meal.assert_not_called();self.assertEqual(ai.review_ogp.call_count,5)
            with store.transaction() as db:
                old=json.loads(db.execute("SELECT result FROM preparation_steps WHERE step='ogp_review:daily'").fetchone()[0])
            self.assertEqual(old['status'],'hold')
            with self.assertRaises(ValueError):self.run_finish(root,store,client,ai,capture)

    def test_unknown_business_request_stops_before_external_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store,client,ai,capture=self.scenario(root)
            with store.transaction() as db:db.execute("UPDATE mina_requests SET phase='held' WHERE slot='daily'")
            client.request_state.reset_mock()
            with self.assertRaisesRegex(ValueError,'成否不明'):
                self.run_finish(root,store,client,ai,capture)
            client.request_state.assert_not_called();client.register.assert_not_called();client.json_write.assert_not_called()

    def test_daily_quality_hold_is_not_bypassed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store,client,ai,capture=self.scenario(root)
            ai.review_ogp.return_value={'status':'hold','reason_codes':['share_ogp_mismatch']}
            with self.assertRaisesRegex(ValueError,'再点検が保留'):
                self.run_finish(root,store,client,ai,capture)
            self.assertEqual(store.list_day('2026-10-08'),[])
            client.register.assert_not_called();client.json_write.assert_not_called()
