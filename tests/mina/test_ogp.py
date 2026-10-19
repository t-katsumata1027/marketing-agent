"""同じOGP URLの画素変更と、許可外画像・偽装応答を拒否する。"""

import io
import unittest
from unittest.mock import Mock, patch
from PIL import Image

from src.mina.ogp import capture_ogp


class OgpTests(unittest.TestCase):
    def capture(self, color='white', url='https://www.log-eats.com/share/example/og-image', mime='image/png'):
        buffer = io.BytesIO()
        Image.new('RGB', (600, 300), color).save(buffer, format='PNG')
        html = ('<meta property="og:title" content="献立"><meta property="og:description" content="解析">'
                '<meta property="og:image" content="' + url + '">').encode()
        responses = []
        for body, content_type in ((html, 'text/html'), (buffer.getvalue(), mime)):
            response = Mock()
            response.read.return_value = body
            response.headers = {'Content-Type': content_type}
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            responses.append(response)
        opener = Mock()
        opener.open.side_effect = responses
        with patch('src.mina.ogp.build_opener', return_value=opener):
            return capture_ogp('https://www.log-eats.com/s/example', 'www.log-eats.com')

    def test_same_url_new_pixels_changes_approval_digest(self):
        white, black = self.capture(), self.capture('black')
        self.assertNotEqual(white['digest'], black['digest'])
        self.assertTrue(white['review_image'].startswith(b'\xff\xd8\xff'))
        self.assertEqual(white['meta']['image_width'], 600)

    def test_cross_host_image_is_rejected(self):
        with self.assertRaises(ValueError):
            self.capture(url='https://other.example/og.png')

    def test_mime_does_not_override_actual_pixels(self):
        with self.assertRaises(ValueError):
            self.capture(mime='image/jpeg')
