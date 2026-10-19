"""OGPの正規別名を実バイトで照合し、別画像・別版・別公開先を拒否する。"""
import hashlib,copy,unittest
from unittest.mock import Mock,patch
from src.mina.ogp import verify_image_reference
HOST='www.log-eats.com'
class OgpAliasTests(unittest.TestCase):
 def fixture(self):
  raw=b'\x89PNG\r\n\x1a\nsynthetic'
  ogp={'revision':'meal-version','image_url':f'https://{HOST}/share/abcd1234/og-image?v=meal-version','image_sha256':hashlib.sha256(raw).hexdigest()}
  page=f'https://{HOST}/share/11111111-1111-4111-8111-111111111111'
  snapshot={'meta':{'og:image':page+'/og-image?v=meal-version','page_url':page,'image_sha256':ogp['image_sha256']}}
  response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
  response.headers={'Content-Type':'image/png'};response.read.return_value=raw
  opener=Mock();opener.open.return_value=response
  return snapshot,ogp,opener,response
 def test_alias_reads_api_png_once_and_compares_sha(self):
  snapshot,ogp,opener,response=self.fixture()
  with patch('src.mina.ogp.build_opener',return_value=opener):
   verify_image_reference(snapshot,ogp,f'https://{HOST}/s/abcd1234',HOST)
  opener.open.assert_called_once()
 def test_changed_pixels_or_version_or_page_are_rejected(self):
  for kind in ('pixels','version','page','other_host'):
   snapshot,ogp,opener,response=self.fixture()
   if kind=='pixels':response.read.return_value=b'changed'
   elif kind=='version':ogp['revision']='changed'
   elif kind=='page':snapshot['meta']['page_url']=f'https://{HOST}/share/22222222-2222-4222-8222-222222222222'
   else:ogp['image_url']='https://other.example/og.png'
   with patch('src.mina.ogp.build_opener',return_value=opener),self.subTest(kind=kind),self.assertRaises(ValueError):
    verify_image_reference(snapshot,ogp,f'https://{HOST}/s/abcd1234',HOST)
 def test_internal_rewrite_keeps_short_page_url_and_still_checks_both_pngs(self):
  snapshot,ogp,opener,response=self.fixture()
  snapshot['meta']['page_url']=f'https://{HOST}/s/abcd1234'
  with patch('src.mina.ogp.build_opener',return_value=opener):
   verify_image_reference(snapshot,ogp,f'https://{HOST}/s/abcd1234',HOST)
  opener.open.assert_called_once()
