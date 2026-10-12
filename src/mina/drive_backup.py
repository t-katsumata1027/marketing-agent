"""暗号化復旧セットだけをDriveへ分割保存し、成否不明時は新規再送しない。"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request

from .artifact_backup import checked_path
from .clients import OutcomeUnknown
from .drive import DriveClient
from .encrypted_backup import MAGIC, LIMIT, read_regular, save_new, check_private_parent

CHUNK = 4*1024*1024


def require_backup_upload():
    """業務処理の停止とは独立した、暗号化保存だけの許可を確認する。"""
    if os.getenv('MINA_BACKUP_UPLOAD_ENABLED','false').lower()!='true':
        raise RuntimeError('暗号化バックアップのDrive保存は停止中です')


class BackupDriveClient:
    """専用Google認証を使い、LogEats・Discord・Xへの接続を作らない。"""

    def __init__(self, drive):
        self.drive = drive

    def _folder(self):
        parent=self.drive.metadata(self.drive.folder_id,'id,mimeType,trashed,shared,owners(me),capabilities(canAddChildren)')
        if (parent.get('mimeType')!='application/vnd.google-apps.folder' or parent.get('trashed') is not False
                or parent.get('shared') is not False or not parent.get('owners')
                or not all(row.get('me') is True for row in parent['owners'])
                or parent.get('capabilities',{}).get('canAddChildren') is not True):
            raise ValueError('本人所有の非共有バックアップ保存先が必要です')

    def _send(self, method, url, body, headers):
        """応答本文・認証・セッションURLをエラーログへ出さない。"""
        try:
            with self.drive.opener.open(Request(url,data=body,headers=headers,method=method),timeout=60) as response:
                raw=response.read(1024*1024+1)
                if len(raw)>1024*1024:raise ValueError()
                return response.status,dict(response.headers),raw
        except HTTPError as error:
            headers=dict(error.headers or {})
            error.close()
            if error.code==308:
                return 308,headers,b''
            raise OutcomeUnknown('backup_upload_unknown') from None
        except (URLError,TimeoutError,OSError,ValueError):
            raise OutcomeUnknown('backup_upload_unknown') from None

    @staticmethod
    def _session(url):
        if not isinstance(url,str) or len(url)>8192:
            raise ValueError('保存セッションが不正です')
        parsed=urlsplit(url)
        if (parsed.scheme!='https' or parsed.netloc!='www.googleapis.com'
                or parsed.path!='/upload/drive/v3/files' or not parsed.query or parsed.fragment):
            raise ValueError('保存セッションの送信先が不正です')
        return url

    @staticmethod
    def _update(path,state):
        """自分の私有台帳だけを置換し、部分的なJSONを完成状態にしない。"""
        path=checked_path(path);check_private_parent(path.parent)
        fd,name=tempfile.mkstemp(prefix='.backup-receipt-',dir=path.parent)
        temporary=Path(name)
        try:
            with os.fdopen(fd,'wb') as writer:
                writer.write(json.dumps(state,ensure_ascii=False).encode());writer.flush();os.fsync(writer.fileno())
            if path.is_symlink():raise ValueError('保存台帳のリンクを拒否しました')
            os.replace(temporary,path)
        finally:
            temporary.unlink(missing_ok=True)

    def verify(self, file_id, raw, filename, sha):
        """実保存先と再取得した全バイトを照合する。"""
        self._folder()
        meta=self.drive.metadata(file_id,'id,name,mimeType,size,parents,md5Checksum,trashed,shared,appProperties')
        if (meta.get('name')!=filename or meta.get('mimeType')!='application/octet-stream'
                or meta.get('parents')!=[self.drive.folder_id] or meta.get('trashed') is not False
                or meta.get('shared') is not False or str(meta.get('size'))!=str(len(raw))
                or meta.get('md5Checksum')!=hashlib.md5(raw).hexdigest()
                or meta.get('appProperties',{}).get('mina_sha256')!=sha
                or meta.get('appProperties',{}).get('mina_role')!='encrypted_recovery'):
            raise ValueError('暗号化バックアップの保存実体が不一致です')
        data=self.drive._request('https://www.googleapis.com/drive/v3/files/'+self.drive._id(file_id)+'?alt=media',
                                headers=self.drive._authorization(),limit=LIMIT+4096)
        if len(data)!=len(raw) or hashlib.sha256(data).hexdigest()!=sha:
            raise ValueError('暗号化バックアップの再取得が不一致です')
        return {'verified':True,'bytes':len(raw),'sha256':sha,'business_calls_performed':False}

    def upload_once(self, source, journal, filename, sha, commit):
        require_backup_upload()
        if (not re.fullmatch(r'mina-recovery-\d{8}(?:T\d{12}Z)?-[0-9a-f]{7,40}\.encrypted',filename)
                or not re.fullmatch(r'[0-9a-f]{64}',sha) or not re.fullmatch(r'[0-9a-f]{40}',commit)):
            raise ValueError('保存名・固定指紋・コード版が不正です')
        raw=read_regular(source,LIMIT+4096)
        if not raw.startswith(MAGIC) or hashlib.sha256(raw).hexdigest()!=sha:
            raise ValueError('暗号化済みの固定バックアップが必要です')
        journal=checked_path(journal)
        binding={'sha256':sha,'bytes':len(raw),'filename':filename,'folder_id':self.drive.folder_id,'source_commit':commit}
        if journal.exists():
            state=json.loads(read_regular(journal,131072))
            if state.get('binding')!=binding:raise ValueError('保存台帳の対象が変わっています')
            if state.get('state') not in ('uploaded','verified'):
                raise OutcomeUnknown('backup_upload_reconciliation_required')
            result=self.verify(self.drive._id(state['file_id']),raw,filename,sha)
            self._update(journal,{**state,'state':'verified'})
            return result
        self._folder()
        state={'state':'started','binding':binding,'automatic_retry_allowed':False}
        save_new(journal,json.dumps(state).encode())
        try:
            meta={'name':filename,'mimeType':'application/octet-stream','parents':[self.drive.folder_id],
                  'appProperties':{'mina_role':'encrypted_recovery','mina_sha256':sha,'mina_source_commit':commit}}
            url='https://www.googleapis.com/upload/drive/v3/files?'+urlencode({'uploadType':'resumable','fields':'id','supportsAllDrives':'true'})
            code,headers,_=self._send('POST',url,json.dumps(meta).encode(),{
                **self.drive._authorization(),'Content-Type':'application/json; charset=UTF-8',
                'X-Upload-Content-Type':'application/octet-stream','X-Upload-Content-Length':str(len(raw))})
            if code not in (200,201):raise OutcomeUnknown('backup_upload_unknown')
            session=self._session(headers.get('Location') or headers.get('location'))
            state.update(state='initiated',session_url=session)
            self._update(journal,state)
            offset=0
            for _ in range(192):
                end=min(offset+CHUNK,len(raw))
                code,headers,response=self._send('PUT',session,raw[offset:end],{
                    **self.drive._authorization(),'Content-Type':'application/octet-stream',
                    'Content-Length':str(end-offset),'Content-Range':f'bytes {offset}-{end-1}/{len(raw)}'})
                if code in (200,201):
                    if end!=len(raw):raise OutcomeUnknown('backup_upload_unknown')
                    state.update(state='uploaded',file_id=self.drive._id(json.loads(response)['id']))
                    self._update(journal,state)
                    break
                match=re.fullmatch(r'bytes=0-(\d+)',headers.get('Range',headers.get('range','')))
                if code!=308 or not match or not offset<int(match[1])+1<=end or int(match[1])+1>=len(raw):
                    raise OutcomeUnknown('backup_upload_unknown')
                offset=int(match[1])+1
                state.update(state='uploading',acknowledged_bytes=offset)
                self._update(journal,state)
            else:
                raise OutcomeUnknown('backup_upload_unknown')
        except Exception:
            if state.get('state')!='uploaded':
                state['state']='outcome_unknown';self._update(journal,state)
            raise OutcomeUnknown('backup_upload_unknown') from None
        result=self.verify(state['file_id'],raw,filename,sha)
        self._update(journal,{**state,'state':'verified'})
        return result
