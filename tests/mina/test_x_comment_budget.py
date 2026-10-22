"""保存前の一言短文化、固定文との余白、文字数停止の診断を検証する。"""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from datetime import UTC,datetime
from tests.mina.test_generation import plan
from src.mina.generation import draft_day,validate_plan
from src.mina.x_text import weighted_length,fit_plan_comments,shorten_comment,PostLengthError
from src.mina.store import Store
from src.mina.audit import safe_failure
from src.mina.domain import FIXED_MARKERS

class CommentBudgetTests(unittest.TestCase):
 def test_oversize_draft_fits_before_artifact_is_saved_without_changing_foods(self):
  original=plan();original['daily_comment']='一日の献立の組み合わせを紹介します。'*5
  self.assertLessEqual(len(original['daily_comment']),120)
  ai=Mock();ai.structured.side_effect=[original,{'comment':'主食とおかずを組み合わせた献立案です。'}]
  with tempfile.TemporaryDirectory() as tmp:
   h=Path(tmp)/'history.md';h.write_text('献立の履歴。',encoding='utf-8');out=Path(tmp)/'draft.json'
   draft_day('2026-10-23',h,out,ai);value=json.loads(out.read_text(encoding='utf-8'))
   self.assertEqual(value['meals'],original['meals']);self.assertLessEqual(weighted_length(value['daily_comment']),110)
   self.assertEqual(ai.structured.call_count,2);self.assertEqual(ai.structured.call_args.args[3],'mina_comment_fit')
 def test_short_comments_need_no_extra_calls_and_input_is_not_mutated(self):
  value=plan();before=copy.deepcopy(value);ai=Mock()
  self.assertEqual(fit_plan_comments(value,ai,'2026-10-23'),value);self.assertEqual(value,before);ai.structured.assert_not_called()
 def test_large_result_is_bounded_and_transport_failure_is_never_retried(self):
  ai=Mock();ai.structured.return_value={'comment':'長'*100}
  with self.assertRaises(ValueError):shorten_comment(ai,'元'*90)
  self.assertEqual(ai.structured.call_count,2)
  ai.reset_mock();ai.structured.side_effect=TimeoutError()
  with self.assertRaises(TimeoutError):shorten_comment(ai,'元'*90)
  self.assertEqual(ai.structured.call_count,1)
 def test_invalid_expression_and_duplicate_are_not_accepted(self):
  for invalid in ('今日食べた献立です。','改行\nあり','https://example.com'):
   ai=Mock();ai.structured.return_value={'comment':invalid}
   with self.assertRaises(ValueError):shorten_comment(ai,'元'*90)
  ai=Mock();ai.structured.side_effect=[{'comment':'既存。'},{'comment':'別の献立案です。'}]
  self.assertEqual(shorten_comment(ai,'元'*90,excluded=('既存。',)),'別の献立案です。')
 def test_reserved_budget_keeps_full_fixed_format_below_280(self):
  fixed='今日1日の食事まとめ 🔥\n3食 10000kcal (P:10000g F:10000g C:10000g)\n'+' '.join(FIXED_MARKERS)
  body='文'*55+'\n\n'+fixed+'\nhttps://www.log-eats.com/s/synthetic'
  self.assertLessEqual(weighted_length(body),280)
  self.assertEqual(weighted_length('x'*280),280);self.assertEqual(weighted_length('x'*285),285)
 def test_stop_records_daily_slot_exact_limit_and_safe_japanese_reason(self):
  error=PostLengthError('daily',285);reason,details=safe_failure(error)
  self.assertEqual(reason,'post_text_too_long');self.assertEqual(details['expected'],280);self.assertEqual(details['observed'],285)
  with tempfile.TemporaryDirectory() as tmp:
   store=Store(Path(tmp)/'db');store.record_preparation_failure('2026-10-23','candidates',error,datetime.now(UTC))
   with store.transaction() as db:
    p=db.execute('SELECT * FROM preparation_progress').fetchone();n=db.execute('SELECT payload FROM notices').fetchone()
   self.assertEqual(p['step'],'candidates:daily');self.assertIn('1日まとめ',n['payload']);self.assertIn('280',n['payload'])
