"""限定CLIの承認・添付・OFF時照会を外部通信なしで検証する。"""
import io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,MagicMock,patch
from PIL import Image
from src.mina.clients import DiscordClient,OutcomeUnknown
from src.mina.external_guard import guard
from src.mina.current_day_cli import protected_json,config


class CurrentDayCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.path=self.root/'meal-x.jpg';b=io.BytesIO();Image.new('RGB',(10,10)).save(b,'JPEG');self.path.write_bytes(b.getvalue())
        self.env=patch.dict(os.environ,{'MINA_LIVE_WRITES':'true'});self.env.start();self.addCleanup(self.env.stop)

    def test_real_multipart_has_no_action_buttons_and_guard_runs_before_post(self):
        client=DiscordClient('private-token','1234');response=Mock();response.read.return_value=b'{"id":"123"}'
        opener=MagicMock();opener.open.return_value.__enter__.return_value=response
        check=Mock();token=guard.set(check)
        try:
            with patch('urllib.request.build_opener',return_value=opener):
                result=client.send_files({'content':'準備専用・時刻未定','allowed_mentions':{'parse':[]}},[self.path])
        finally:guard.reset(token)
        self.assertEqual(result,{'id':'123'});check.assert_called_once()
        request=opener.open.call_args.args[0];self.assertEqual(request.get_method(),'POST')
        self.assertIn(b'name="files[0]"',request.data);self.assertIn(b'name="payload_json"',request.data)
        self.assertNotIn(b'private-token',request.data);self.assertNotIn(b'custom_id',request.data)

    def test_expired_immediate_pre_post_guard_keeps_http_zero(self):
        token=guard.set(Mock(side_effect=ValueError('期限切れ')))
        try:
            with patch('urllib.request.build_opener') as build:
                with self.assertRaises(ValueError):DiscordClient('private-token','1234').send_files({'content':'準備専用'},[self.path])
                build.assert_not_called()
        finally:guard.reset(token)

    def test_lost_or_malformed_discord_response_is_unknown_and_single_post(self):
        for raw in (b'not-json',b'{}',b'{"id":null}'):
            with self.subTest(raw=raw):
                response=Mock();response.read.return_value=raw;opener=MagicMock();opener.open.return_value.__enter__.return_value=response
                with patch('urllib.request.build_opener',return_value=opener):
                    with self.assertRaises(OutcomeUnknown):DiscordClient('private-token','1234').send_files({'content':'準備専用'},[self.path])
                self.assertEqual(opener.open.call_count,1)

    def test_attachment_symlink_and_wrong_format_do_not_post(self):
        bad=self.root/'bad.jpg';bad.write_bytes(b'not-jpeg')
        with patch('urllib.request.build_opener') as opener:
            with self.assertRaises(ValueError):DiscordClient('x','1234').send_files({'content':'準備専用'},[bad])
            opener.assert_not_called()

    def test_actor_cannot_synthesize_configuration_or_role_approved(self):
        if os.name!='posix' or os.getuid()==0:self.skipTest('Actor UIDの実所有検査')
        path=self.root/'approval.json';path.write_text(json.dumps({'verdict':'approved'}));path.chmod(0o600)
        with self.assertRaisesRegex(ValueError,'root所有'):protected_json(path,root_owned=True)
        with self.assertRaisesRegex(ValueError,'root所有'):config(path,self.root)

    def test_read_only_symlink_reference_is_not_a_protected_original(self):
        path=self.root/'original.json';path.write_text('{}')
        link=self.root/'reference.json'
        try:link.symlink_to(path)
        except OSError:self.skipTest('symlink権限なし')
        with self.assertRaises(ValueError):protected_json(link)

    def test_bearer_is_not_constructed_for_unapproved_environment_origin(self):
        from src.mina.current_day_cli import scoped_meal
        with patch.dict(os.environ,{'MINA_MEAL_API_BASE_URL':'https://other.invalid','MINA_AGENT_API_TOKEN':'private-token'}),patch('src.mina.clients.preparation_client') as factory:
            with self.assertRaises(ValueError):scoped_meal()
            factory.assert_not_called()


if __name__=='__main__':unittest.main()
