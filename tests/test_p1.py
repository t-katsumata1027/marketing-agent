"""P1の排他、リトライ、品質、Discord安全化をモックで検証する。"""
import unittest
from unittest.mock import Mock, patch
from src.quality_gate import validate_content
from src.retry import retry_call
from src import listener

class Response: status_code=429; headers={"Retry-After":"0"}
class Temporary(Exception): response=Response()

class P1Test(unittest.TestCase):
 def test_retry_classification_and_no_real_wait(self):
  call, sleep = Mock(side_effect=[Temporary(), "ok"]), Mock()
  self.assertEqual(retry_call(call, sleep=sleep, random_value=lambda:0), "ok")
  sleep.assert_called_once()
  with self.assertRaises(ValueError): retry_call(lambda: (_ for _ in ()).throw(ValueError("入力")), sleep=sleep)
 def test_quality_gate(self):
  errors=validate_content("@other #a #b #c #d https://evil.example 禁止", {"allowed_domains":["ok.example"],"max_hashtags":3,"banned_words":["禁止"]}, "target")
  self.assertGreaterEqual(len(errors),4)
  self.assertEqual(validate_content("@target 本文", {}, "target"), [])
 def test_listener_duplicate_is_safely_skipped(self):
  tweet=Mock(id="1",author_id="a",text="本文"); response=Mock(data=[tweet],includes={"users":[Mock(id="a",username="u")]})
  with patch.object(listener,"get_twitter_client",return_value=Mock(search_recent_tweets=Mock(return_value=response))), patch.object(listener,"log_mention",return_value=[]) as log:
   self.assertEqual(listener.run_social_listening("p","q",10),0); log.assert_called_once()
 def test_discord_payload_blocks_mentions(self):
  from src import discord_client
  safe_config=Mock(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test", NEXT_PUBLIC_SITE_URL="https://approval.example")
  with patch.object(discord_client,"config",safe_config), patch.object(discord_client,"create_approval_token",return_value="x"*44), patch("src.discord_client.requests.post",return_value=Mock(raise_for_status=Mock())) as post:
   discord_client.send_discord_notification("p","t","本文",1)
  self.assertEqual(post.call_args.kwargs["json"]["allowed_mentions"],{"parse":[]})
