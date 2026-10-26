"""新しく作った投稿画像だけを配信する。既存Driveファイルの公開権限は変更しない。"""

import hashlib
import io
import json
import re
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image

from .clients import require_live
from .instagram_domain import media_url

HOSTS = ('drive.google.com',)


def upload_image(drive, raw, name, parent=None):
    """食事原本用の保存名制約を変えず、SNS合成画像だけを別名で保存する。"""
    require_live()
    if (not raw.startswith(b'\xff\xd8\xff') or len(raw) > 4 * 1024 * 1024 or
            not re.fullmatch(r'mina-instagram-[0-9a-f]{64}\.jpg', name)):
        raise ValueError('Instagram完成画像の保存形式が不正です')
    parent = parent if parent is not None else drive.metadata(drive.folder_id, 'id,mimeType,trashed,capabilities(canAddChildren)')
    if (parent.get('mimeType') != 'application/vnd.google-apps.folder' or parent.get('trashed') is not False
            or parent.get('capabilities', {}).get('canAddChildren') is not True):
        raise ValueError('Instagram画像の保存先を確認できません')
    boundary = uuid.uuid4().hex
    meta = json.dumps({'name': name, 'mimeType': 'image/jpeg', 'parents': [drive.folder_id],
                       'appProperties': {'mina_sha256': hashlib.sha256(raw).hexdigest(),
                                         'mina_role': 'instagram_composite'}}, ensure_ascii=False).encode()
    body = (f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'.encode() + meta +
            f'\r\n--{boundary}\r\nContent-Type: image/jpeg\r\n\r\n'.encode() + raw +
            f'\r\n--{boundary}--\r\n'.encode())
    response = drive._request('https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id&supportsAllDrives=true',
                              body, {**drive._authorization(), 'Content-Type': 'multipart/related; boundary=' + boundary},
                              'POST', write=True)
    return {'id': drive._id(json.loads(response)['id'])}


class PublicMediaRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urlsplit(newurl).hostname or ''
        if (urlsplit(newurl).scheme != 'https' or not
                (host in ('drive.google.com', 'drive.usercontent.google.com') or host.endswith('.googleusercontent.com'))):
            raise ValueError('画像の転送先が許可外です')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def verify_public(asset, opener=None):
    """認証なしの実バイトを確認し、HTML閲覧ページを画像扱いしない。"""
    media_url(asset['url'], HOSTS)
    opener = opener or build_opener(PublicMediaRedirect())
    with opener.open(Request(asset['url'], headers={'User-Agent': 'Mina-Instagram-Media/1.0'}), timeout=20) as response:
        mime = response.headers.get('Content-Type', '').split(';')[0].lower()
        raw = response.read(4 * 1024 * 1024 + 1)
    if mime not in ('image/jpeg', 'application/octet-stream') or hashlib.sha256(raw).hexdigest() != asset['sha256']:
        raise ValueError('配信画像が完成画像と一致しません')
    with Image.open(io.BytesIO(raw)) as image:
        if image.format != 'JPEG' or image.size != (asset['width'], asset['height']):
            raise ValueError('配信画像の寸法が一致しません')
    return True


def host_asset(asset, drive, store, allow_public=False, verify=verify_public):
    """保存・権限付与の途中で落ちても、新しいファイルを自動作成し直さない。"""
    require_live()
    if not allow_public:
        raise ValueError('新規投稿素材の公開配信を明示してください')
    raw = Path(asset['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != asset['sha256']:
        raise ValueError('完成画像が変更されています')
    with store.transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS instagram_media
            (sha256 TEXT PRIMARY KEY,state TEXT NOT NULL,file_id TEXT,asset_json TEXT)''')
        previous = db.execute('SELECT * FROM instagram_media WHERE sha256=?', (asset['sha256'],)).fetchone()
        if previous and previous['state'] != 'verified':
            raise ValueError('既存の画像保存・権限付与の成否照合が必要です')
    if previous:
        # 匿名HTTP読取の間は共有SQLiteの書込ロックを保持しない。
        hosted = json.loads(previous['asset_json'])
        verify(hosted)
        return {**asset, 'url': hosted['url']}
    name = 'mina-instagram-' + asset['sha256'] + '.jpg'
    # 保存先・OAuthの読取失敗を、不明な画像保存として記録しない。
    parent=drive.metadata(drive.folder_id,'id,mimeType,trashed,capabilities(canAddChildren)')
    if (parent.get('mimeType')!='application/vnd.google-apps.folder' or parent.get('trashed') is not False
            or parent.get('capabilities',{}).get('canAddChildren') is not True):raise ValueError('Instagram保存先を確認できません')
    drive._authorization()
    begin=getattr(drive,'before_media_write',None)
    if begin:begin()
    with store.transaction() as db:
        if db.execute('SELECT 1 FROM instagram_media WHERE sha256=?',(asset['sha256'],)).fetchone():raise ValueError('Instagram画像を別処理が保存しています')
        db.execute("INSERT INTO instagram_media(sha256,state) VALUES(?,'attempting')",(asset['sha256'],))
    uploaded = upload_image(drive, raw, name,parent=parent)
    file_id = uploaded['id']
    with store.transaction() as db:
        db.execute("UPDATE instagram_media SET state='uploaded',file_id=? WHERE sha256=?", (file_id, asset['sha256']))
    drive.verify(file_id, raw, name)
    # この処理で作った画像一件だけを、リンクを知る閲覧者向けにする。
    permission = json.dumps({'type': 'anyone', 'role': 'reader', 'allowFileDiscovery': False}).encode()
    drive._request('https://www.googleapis.com/drive/v3/files/' + drive._id(file_id) + '/permissions?supportsAllDrives=true',
                   data=permission, headers={**drive._authorization(), 'Content-Type': 'application/json'}, method='POST', write=True)
    hosted = {**asset, 'url': 'https://drive.google.com/uc?' + urlencode({'export': 'download', 'id': file_id})}
    verify(hosted)
    with store.transaction() as db:
        db.execute("UPDATE instagram_media SET state='verified',asset_json=? WHERE sha256=?", (json.dumps(hosted, ensure_ascii=False), asset['sha256']))
    return hosted
