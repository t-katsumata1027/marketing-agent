"""サーバー用OAuthで画像をDriveへ保存し、実体を再読取する。"""

import hashlib
import json
import os
import re
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .clients import OutcomeUnknown, require_live


class NoRedirect(HTTPRedirectHandler):
    """OAuthのBearerを別ホストへ転送しない。"""

    def redirect_request(self, *args, **kwargs):
        return None


class DriveClient:
    """アプリの接続情報を転用せず、サーバー専用OAuthを使用する。"""

    fields = 'id,name,mimeType,size,parents,md5Checksum,trashed,webViewLink'

    def __init__(self, client_id, client_secret, refresh_token, folder_id=None):
        if not all((client_id, client_secret, refresh_token)) or (folder_id is not None and not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', folder_id)):
            raise ValueError('Driveの専用OAuthと保存先フォルダーが必要です')
        self.client_id, self.client_secret, self.refresh_token = client_id, client_secret, refresh_token
        self.folder_id = folder_id
        self.opener = build_opener(NoRedirect())
        self.access_token, self.expires = '', 0

    @classmethod
    def from_env(cls):
        return cls(*(os.getenv(name, '') for name in ('MINA_GOOGLE_CLIENT_ID', 'MINA_GOOGLE_CLIENT_SECRET',
                                                      'MINA_GOOGLE_REFRESH_TOKEN', 'MINA_DRIVE_FOLDER_ID')))

    def _request(self, url, data=None, headers=None, method='GET', limit=1048576, write=False):
        try:
            with self.opener.open(Request(url, data=data, headers=headers or {}, method=method), timeout=60) as response:
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise ValueError('Drive応答が大きすぎます')
                return raw
        except HTTPError as error:
            if write and error.code >= 500:
                raise OutcomeUnknown('drive_write_unknown') from None
            raise RuntimeError(f'drive_http_{error.code}') from None
        except (URLError, TimeoutError, OSError, ValueError):
            if write:
                raise OutcomeUnknown('drive_write_unknown') from None
            raise RuntimeError('drive_read_failed') from None

    def _authorization(self):
        if not self.access_token or time.monotonic() >= self.expires:
            body = urlencode({'client_id': self.client_id, 'client_secret': self.client_secret,
                              'refresh_token': self.refresh_token, 'grant_type': 'refresh_token'}).encode()
            raw = self._request('https://oauth2.googleapis.com/token', body,
                                {'Content-Type': 'application/x-www-form-urlencoded'}, 'POST')
            try:
                result = json.loads(raw)
                token, duration = result['access_token'], int(result['expires_in'])
                if not isinstance(token, str) or not token or duration <= 60 or result.get('token_type', '').lower() != 'bearer':
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise RuntimeError('drive_oauth_invalid') from None
            self.access_token, self.expires = token, time.monotonic() + min(duration - 60, 3600)
        return {'Authorization': 'Bearer ' + self.access_token}

    @staticmethod
    def _id(file_id):
        if not isinstance(file_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', file_id):
            raise ValueError('DriveファイルIDが不正です')
        return file_id

    def metadata(self, file_id, fields=None):
        url = 'https://www.googleapis.com/drive/v3/files/' + self._id(file_id)
        raw = self._request(url + '?' + urlencode({'fields': fields or self.fields, 'supportsAllDrives': 'true'}),
                            headers=self._authorization())
        try:
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get('id') != file_id:
                raise ValueError()
            return result
        except (ValueError, TypeError):
            raise RuntimeError('drive_metadata_invalid') from None

    def create_folder(self, name):
        """アプリ自身の保存先を一度作成する。再送防止は呼出元の台帳で行う。"""
        require_live()
        if name != 'AI広報みな marketing-agent':
            raise ValueError('専用保存先の名前が必要です')
        body = json.dumps({'name': name, 'mimeType': 'application/vnd.google-apps.folder',
                           'appProperties': {'mina_role': 'posting_images'}}, ensure_ascii=False).encode()
        raw = self._request('https://www.googleapis.com/drive/v3/files?fields=id', body,
                            {**self._authorization(), 'Content-Type': 'application/json'}, 'POST', write=True)
        try:
            return {'id': self._id(json.loads(raw)['id'])}
        except (ValueError, KeyError, TypeError):
            raise OutcomeUnknown('drive_write_unknown') from None

    def upload(self, image, filename):
        """一度だけ保存する。呼出元の工程台帳が再送を防ぐ。"""
        require_live()
        if not isinstance(image, bytes) or not image.startswith(b'\xff\xd8\xff') or len(image) > 4 * 1024 * 1024:
            raise ValueError('4MB以下のJPEGが必要です')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}-(breakfast|lunch|dinner)\.jpg', filename):
            raise ValueError('日付と食事区分を含む保存名が必要です')
        parent = self.metadata(self.folder_id, 'id,mimeType,trashed,capabilities(canAddChildren)')
        if parent.get('mimeType') != 'application/vnd.google-apps.folder' or parent.get('trashed') is not False or parent.get('capabilities', {}).get('canAddChildren') is not True:
            raise ValueError('Drive保存先の書き込み権限を確認してください')
        boundary = uuid.uuid4().hex
        meta = json.dumps({'name': filename, 'mimeType': 'image/jpeg', 'parents': [self.folder_id],
                           'appProperties': {'mina_sha256': hashlib.sha256(image).hexdigest()}}, ensure_ascii=False).encode()
        body = (f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'.encode() + meta +
                f'\r\n--{boundary}\r\nContent-Type: image/jpeg\r\n\r\n'.encode() + image + f'\r\n--{boundary}--\r\n'.encode())
        url = 'https://www.googleapis.com/upload/drive/v3/files?' + urlencode({'uploadType': 'multipart', 'fields': 'id', 'supportsAllDrives': 'true'})
        raw = self._request(url, body, {**self._authorization(), 'Content-Type': 'multipart/related; boundary=' + boundary}, 'POST', write=True)
        try:
            return {'id': self._id(json.loads(raw)['id'])}
        except (ValueError, KeyError, TypeError):
            raise OutcomeUnknown('drive_write_unknown') from None

    def verify(self, file_id, image, filename):
        """保存名・親・容量・MD5と再ダウンロードしたSHA-256を照合する。"""
        meta = self.metadata(file_id)
        if (meta.get('name') != filename or meta.get('mimeType') != 'image/jpeg' or
                meta.get('trashed') is not False or meta.get('parents') != [self.folder_id] or
                str(meta.get('size')) != str(len(image)) or meta.get('md5Checksum') != hashlib.md5(image).hexdigest()):
            raise ValueError('Drive保存結果が元画像と一致しません')
        raw = self._request('https://www.googleapis.com/drive/v3/files/' + self._id(file_id) + '?alt=media',
                            headers=self._authorization(), limit=4 * 1024 * 1024)
        if hashlib.sha256(raw).digest() != hashlib.sha256(image).digest():
            raise ValueError('Drive再読取画像の内容が一致しません')
        return {'id': file_id, 'name': filename, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                'verified': True}
