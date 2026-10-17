"""新APIの読取宛先と実画像完全性を合成応答だけで検証する。外部通信しない。"""

import base64
from datetime import UTC, datetime, timedelta
from email.message import Message
import hashlib
from io import BytesIO
import json
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

from PIL import Image

from src.mina.replacement_client import ImageDescriptor, ReplacementReadClient, ReplacementReadFailure

KEY = '33333333-3333-4333-8333-333333333333'
NOW = datetime(2026,10,17,10,tzinfo=UTC)
VERSION = 'cv1_'+'a'*64


class Response(BytesIO):
    def __init__(self, raw, headers=None, status=200):
        super().__init__(raw)
        self.headers, self.status = Message(), status
        for name,value in (headers or {}).items():
            self.headers[name] = value


class ReplacementReadTests(unittest.TestCase):
    def client(self, opener, decoder=lambda kind, payload:(kind,payload)):
        return ReplacementReadClient('https://logeats.test', 'test-only-token-'*4,
                                     decoder=decoder, opener=opener, clock=lambda:NOW, sleep=lambda _:None)

    def picture(self):
        out = BytesIO()
        Image.new('RGB',(20,30),'green').save(out,format='PNG')
        raw = out.getvalue()
        sha = hashlib.sha256(raw).hexdigest()
        expected = ImageDescriptor(VERSION,sha,len(raw),'image/png')
        headers = {'Content-Type':'image/png','Content-Length':str(len(raw)),
            'Content-Digest':'sha-256=:'+base64.b64encode(bytes.fromhex(sha)).decode()+':',
            'X-Mina-Candidate-Version':VERSION,'X-Mina-Image-SHA256':sha,'X-Mina-Complete':'true',
            'X-Mina-Checked-At':'2026-10-17T09:59:59.000Z','X-Mina-Valid-Until':'2026-10-17T10:01:00.000Z'}
        return raw, expected, headers

    def test_missing_contract_decoder_makes_zero_requests(self):
        opener = Mock()
        client = self.client(opener, decoder=None)
        with self.assertRaisesRegex(ReplacementReadFailure,'contract_unavailable'):
            client.preconditions('2026-10-18')
        opener.assert_not_called()

    def test_get_paths_and_candidate_control_are_separate_from_m1_commit_lookup(self):
        requests = []
        def open_request(request, timeout):
            requests.append(request)
            return Response(b'{"complete":true}')
        client = self.client(open_request)
        client.preconditions('2026-10-18')
        client.candidate(KEY)
        for operation in ('candidate_prepare','candidate_discard','meal_replace'):
            kind,payload = client.request(KEY,operation)
            self.assertEqual(kind,operation)
        self.assertIn('/meal-replacement-requests/',requests[2].full_url)
        self.assertIn('/requests/',requests[4].full_url)
        for request in requests:
            self.assertEqual(request.get_method(),'GET')
            self.assertIsNone(request.data)
            self.assertEqual(request.get_header('Cache-control'),'no-store')
            self.assertIsNone(request.get_header('Cookie'))
            self.assertIsNone(request.get_header('Origin'))

    def test_404_or_network_error_stays_unknown_without_any_write_or_retry(self):
        for failure in (HTTPError('https://logeats.test',404,'hidden',{},None),URLError('unavailable')):
            opener = Mock(side_effect=failure)
            with self.assertRaises(ReplacementReadFailure) as caught:
                self.client(opener).request(KEY,'meal_replace')
            self.assertEqual(str(caught.exception),'replacement_lookup_unknown')
            self.assertEqual(opener.call_count,1)

    def test_ambiguous_nonfinite_or_oversized_json_never_reaches_decoder(self):
        for raw in (b'{"x":1,"x":2}',b'{"x":1e999}',b'{"x":NaN}',b'x'*131073):
            decoder = Mock()
            with self.assertRaises(ReplacementReadFailure):
                self.client(Mock(return_value=Response(raw)),decoder).candidate(KEY)
            decoder.assert_not_called()

    def test_decoder_rejects_incomplete_or_wrong_contract_response(self):
        decoder = Mock(side_effect=ValueError('invalid contract'))
        opener = Mock(return_value=Response(b'{"complete":false}'))
        with self.assertRaisesRegex(ReplacementReadFailure,'response_invalid'):
            self.client(opener,decoder).candidate(KEY)
        self.assertEqual(opener.call_count,1)

    def test_real_bytes_headers_and_candidate_version_are_verified(self):
        raw, expected, headers = self.picture()
        opener = Mock(return_value=Response(raw,headers))
        self.assertEqual(self.client(opener).image(KEY,'original',expected),raw)
        request = opener.call_args.args[0]
        self.assertEqual(request.get_header('Candidate-version'),VERSION)
        self.assertEqual(request.get_method(),'GET')

    def test_same_metadata_with_changed_or_missing_bytes_is_rejected(self):
        raw, expected, headers = self.picture()
        for changed in (raw[:-1],raw[:-1]+b'x',raw+b'x'):
            with self.assertRaises(ReplacementReadFailure):
                self.client(Mock(return_value=Response(changed,headers))).image(KEY,'original',expected)

    def test_partial_range_encoded_or_wrong_mime_response_is_rejected(self):
        raw, expected, headers = self.picture()
        changes = ({'Content-Range':'bytes 0-1/20'},{'Content-Encoding':'gzip'},
                   {'Content-Type':'application/json'},{'X-Mina-Candidate-Version':'cv1_'+'b'*64},
                   {'X-Mina-Complete':'false'},{'Content-Length':'1'},{'Content-Digest':'wrong'})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ReplacementReadFailure):
                self.client(Mock(return_value=Response(raw,{**headers,**change}))).image(KEY,'original',expected)
        with self.assertRaises(ReplacementReadFailure):
            self.client(Mock(return_value=Response(raw,headers,206))).image(KEY,'original',expected)

    def test_stale_future_expired_or_noncanonical_timestamps_are_rejected(self):
        raw, expected, headers = self.picture()
        for change in ({'X-Mina-Checked-At':'2026-10-17T10:00:01.000Z'},
                       {'X-Mina-Checked-At':'2026-10-17T09:57:59.000Z'},
                       {'X-Mina-Valid-Until':'2026-10-17T10:00:00.000Z'},
                       {'X-Mina-Checked-At':'2026-10-17T09:59:59Z'}):
            with self.subTest(change=change), self.assertRaises(ReplacementReadFailure):
                self.client(Mock(return_value=Response(raw,{**headers,**change}))).image(KEY,'original',expected)

    def test_byte_hash_alone_does_not_make_nonimage_data_acceptable(self):
        raw, _, headers = self.picture()
        raw = b'this is not an image'
        sha = hashlib.sha256(raw).hexdigest()
        headers.update({'Content-Length':str(len(raw)),'Content-Digest':'sha-256=:'+base64.b64encode(bytes.fromhex(sha)).decode()+':',
                        'X-Mina-Image-SHA256':sha})
        with self.assertRaises(ReplacementReadFailure):
            self.client(Mock(return_value=Response(raw,headers))).image(KEY,'original',ImageDescriptor(VERSION,sha,len(raw),'image/png'))

    def test_lookup_does_not_follow_redirects(self):
        opener = Mock(side_effect=HTTPError('https://logeats.test',302,'moved',{},None))
        with self.assertRaises(ReplacementReadFailure) as caught:
            self.client(opener).candidate(KEY)
        self.assertEqual(caught.exception.http_status,302)
        self.assertEqual(opener.call_count,1)

    def test_stored_image_contract_requires_webp(self):
        raw,expected,headers = self.picture()
        opener = Mock()
        with self.assertRaises(ReplacementReadFailure):
            self.client(opener).image(KEY,'stored',expected)
        opener.assert_not_called()

    def test_small_clock_ahead_waits_real_time_without_accepting_future(self):
        raw,expected,headers = self.picture()
        headers['X-Mina-Checked-At'] = '2026-10-17T10:00:02.000Z'
        current,waits = [NOW],[]
        def sleep(seconds):
            waits.append(seconds)
            current[0] += timedelta(seconds=seconds)
        client = self.client(Mock(return_value=Response(raw,headers)))
        client.clock,client.sleep = lambda:current[0],sleep
        self.assertEqual(client.image(KEY,'original',expected),raw)
        self.assertEqual(waits,[2])

    def test_image_get_supports_chunked_reads_but_requires_complete_exact_body(self):
        raw,expected,headers = self.picture()
        response = Response(raw,headers)
        read = response.read
        response.read = lambda size:read(min(size,7))
        self.assertEqual(self.client(Mock(return_value=response)).image(KEY,'original',expected),raw)
