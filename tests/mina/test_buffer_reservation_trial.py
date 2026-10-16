"""停止条件と日時指定・取消の一回性を、実SQLiteで確認する。"""
from datetime import UTC,datetime
import unittest
from unittest.mock import Mock
import test_buffer_trial as fixture
from src.mina.buffer_trial import ProbeError,probe
from src.mina.buffer_reservation_trial import EDIT,reservation_trial


class BufferReservationTests(unittest.TestCase):
    reply=fixture.BufferTrialTests.reply
    def setUp(self):
        fixture.BufferTrialTests.setUp(self)
        probe(self.client,self.path,True)
        self.channel['isQueuePaused']=True
        parent=self.reply
        def reply(query,variables):
            if query==EDIT:
                data=variables['input']
                self.assertEqual(data['id'],'post')
                self.assertEqual(data['text'],self.post['text'])
                if data['saveToDraft']:
                    self.post['status']='draft'
                else:
                    self.post['status']='scheduled';self.post['dueAt']=data['dueAt']
                return {'data':{'editPost':{'__typename':'PostActionSuccess','post':dict(self.post)}}}
            return parent(query,variables)
        self.transport=Mock(side_effect=reply)
        self.client._transport=self.transport

    # 親の共通下書き検査を再実行せず、追加の予約条件だけを検証する。
    def test_roundtrip_and_restart_have_exactly_two_edits(self):
        report=reservation_trial(self.client,self.path,lambda:datetime(2026,10,15,tzinfo=UTC))
        self.assertTrue(report['reservation_roundtrip_verified'])
        again=reservation_trial(self.client,self.path)
        self.assertTrue(again['cached'])
        self.assertEqual(sum(c.args[0]==EDIT for c in self.transport.call_args_list),2)

    def test_unpaused_channel_never_schedules(self):
        self.channel['isQueuePaused']=False
        with self.assertRaisesRegex(ProbeError,'pause_required'):
            reservation_trial(self.client,self.path)
        self.assertFalse(any(c.args[0]==EDIT for c in self.transport.call_args_list))

    def test_lost_schedule_response_is_not_repeated(self):
        parent=self.transport.side_effect
        def lost(query,variables):
            result=parent(query,variables)
            if query==EDIT:raise TimeoutError()
            return result
        self.transport.side_effect=lost
        with self.assertRaises(ProbeError):reservation_trial(self.client,self.path)
        with self.assertRaisesRegex(ProbeError,'reconciliation_required'):
            reservation_trial(self.client,self.path)
        self.assertEqual(sum(c.args[0]==EDIT for c in self.transport.call_args_list),1)
