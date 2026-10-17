"""外部サービスを代替し、翌日4枠の準備から承認・時刻公開までを通す。"""

import hashlib
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from src.mina.daily_pipeline import prepare_day
from src.mina.domain import ACCOUNT, Candidate, SLOTS, JST
from src.mina.m1_contract import verify_source
from src.mina.runtime import notify_day, publish_due, send_notifications
from src.mina.store import Store
from tests.mina import test_registration
from tests.mina.test_daily import NOW, NOTIFY, TARGET
from tests.mina.test_readiness import evidence


class OfflineDayTest(unittest.TestCase):
    def test_full_day_uses_real_pipeline_and_local_ledger_without_network(self):
        self.exercise_day()

    def test_same_day_registers_three_meals_but_never_publishes_breakfast(self):
        self.exercise_day(same_day=True)

    def exercise_day(self, same_day=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root/'db')
            history = root/'history.json'
            history.write_text(json.dumps(evidence()))
            helper = test_registration.RegistrationTests()
            draft, _ = helper.setup_inputs(root)
            ai = Mock()
            ai.structured.side_effect = lambda instructions,content,schema,name: (
                {slot:[300,300] for slot in draft['meals']} if name=='mina_menu_nutrition_check'
                else {'meals':draft['meals'],'daily_comment':draft['daily_comment']})
            image = io.BytesIO()
            Image.new('RGB', (64, 64), 'ivory').save(image, 'JPEG')
            ai.image.return_value = image.getvalue()
            ai.review_image.return_value = {'status':'image_acceptable','reason_codes':[]}
            ai.review_meal.return_value = ai.review_ogp.return_value = {'status':'publishable','reason_codes':[]}
            drive = Mock(folder_id='合成フォルダ')
            drive.upload.side_effect = lambda raw, filename: {'id': filename}
            drive.verify.side_effect = lambda key, raw, filename: {'verified':True,'sha256':hashlib.sha256(raw).hexdigest()}
            meal = helper.client()
            state_call, request_call = meal.day_state.side_effect, meal.request_state.side_effect
            current = [datetime(2026, 10, 8, 9, 30, tzinfo=JST) if same_day else NOW]
            def state(target):
                value = state_call(target)
                value['checked_at'] = current[0].isoformat()
                return value
            def request(key, operation):
                value = request_call(key, operation)
                value['current_state_version'] = state(TARGET)['state_version']
                return value
            meal.day_state.side_effect, meal.request_state.side_effect = state, request
            class Clock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return current[0].astimezone(tz) if tz else current[0].replace(tzinfo=None)
            host = 'www.log-eats.com'
            def capture(url, allowed):
                self.assertEqual(allowed, host)
                return {'meta':{'og:image':url+'/og-image','image_sha256':'e'*64},
                        'digest':'d'*64,'review_image':image.getvalue()}
            with patch('socket.socket.connect', side_effect=AssertionError('実ネットワークは禁止')), \
                 patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('実HTTPは禁止')), \
                 patch('src.mina.daily_pipeline.release_commit', return_value='a'*40), \
                 patch('src.mina.registration.datetime', Clock), \
                 patch('src.mina.ogp.capture_ogp', side_effect=capture):
                args = (store,root/'daily',history,history,'a'*40,root,host,ai,drive,meal)
                self.assertEqual(prepare_day(*args,clock=lambda:current[0],same_day=same_day)['state'],'approval_ready')
                self.assertEqual(prepare_day(*args,clock=lambda:current[0],same_day=same_day)['state'],'already_complete')
                self.assertEqual(ai.image.call_count,3)
                self.assertEqual(ai.review_image.call_count,3)
                self.assertEqual(ai.review_meal.call_count,3)
                self.assertEqual(ai.review_ogp.call_count,4)
                self.assertEqual(drive.upload.call_count,3)
                self.assertEqual(drive.verify.call_count,3)
                self.assertEqual(meal.register.call_count,3)
                self.assertEqual(meal.json_write.call_count,1)
                notify_day(store,TARGET,NOW)
                self.assertEqual(store.issue_cards(TARGET,NOW),[])
                cards = store.issue_cards(TARGET,NOTIFY)
                expected = 3 if same_day else 4
                self.assertEqual(len(cards),expected)
                if same_day:
                    self.assertIsNone(store.get(TARGET, 'breakfast'))
                discord = Mock()
                self.assertEqual(send_notifications(store,discord),expected)
                for card in cards:
                    self.assertEqual(store.decide(card['components'][0]['components'][0]['custom_id'],
                                                 '合成承認者',NOTIFY),'approved')
                x = Mock()
                x.publish.side_effect = [str(100+i) for i in range(4)]
                for row in store.list_day(TARGET):
                    candidate = Candidate(**json.loads(row['payload']))
                    current[0] = candidate.scheduled_at
                    self.assertEqual(publish_due(store,x,current[0],host,
                        lambda c:verify_source(c,meal,host,capture,clock=lambda:current[0]),
                        clock=lambda:current[0]),1)
                    self.assertEqual(publish_due(store,x,current[0],host,
                        lambda c:verify_source(c,meal,host,capture,clock=lambda:current[0]),
                        clock=lambda:current[0]),0)
                self.assertEqual(x.publish.call_count,expected)
                x.verify_account.assert_called_with(ACCOUNT)
                self.assertEqual({r['state'] for r in store.list_day(TARGET)},{'published'})
