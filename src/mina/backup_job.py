"""日次の暗号化コピーとDrive保存を同じ私有記録へ対応付ける。"""
import hashlib
import json
import re
from datetime import UTC,datetime
from pathlib import Path
from .artifact_backup import checked_path
from .drive_backup import BackupDriveClient,require_backup_upload
from .encrypted_backup import protect_directory,save_new,read_regular
from .recovery_set import create_recovery_set
from .domain import JST


def run_backup_job(config,drive,commit,now=None):
    """同じ日の再実行では完成した暗号化ファイルを再生成しない。"""
    require_backup_upload()
    now=(now or datetime.now(UTC)).astimezone(UTC)
    if (not isinstance(commit,str) or not re.fullmatch(r'[0-9a-f]{40}',commit)
            or not isinstance(config,dict)
            or set(config)!={'database','artifacts','files','directory','public_key','recipient_sha256'}):
        raise ValueError('バックアップ対象設定の項目が不正です')
    if (not isinstance(config['artifacts'],dict) or set(config['artifacts'])-{'daily','history'}
            or not isinstance(config['files'],dict)
            or set(config['files'])!={'service-env','doppler-env','codex-auth','history-bootstrap','cutover'}):
        raise ValueError('バックアップ対象の成果物・保護ファイルが不正です')
    directory=checked_path(config['directory'])
    if not directory.exists():
        directory.mkdir(mode=0o700)
        protect_directory(directory)
    binding=hashlib.sha256(json.dumps({'config':config,'commit':commit},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    job=directory/('job-'+now.astimezone(JST).date().isoformat()+'.json')
    if job.exists():
        state=json.loads(read_regular(job,131072))
        if state.get('binding')!=binding:
            raise ValueError('当日バックアップの対象設定・コード版が変わっています')
        if state.get('phase') not in ('encrypted','verified'):
            raise ValueError('暗号化コピーの成否確認が必要です。自動再作成しません')
    else:
        filename='mina-recovery-'+now.strftime('%Y%m%dT%H%M%S%fZ')+'-'+commit[:8]+'.encrypted'
        state={'binding':binding,'phase':'started','filename':filename,'source_commit':commit}
        save_new(job,json.dumps(state).encode())
        artifacts={}
        for name,value in config['artifacts'].items():
            path=checked_path(value)
            if path.is_dir() and any(item.is_file() for item in path.rglob('*')):
                artifacts[name]=path
        receipt=create_recovery_set(config['database'],artifacts,config['files'],directory/filename,
                                    config['public_key'],config['recipient_sha256'],commit)
        state.update(phase='encrypted',receipt=receipt)
        BackupDriveClient._update(job,state)
    filename=state['filename']
    if not isinstance(filename,str) or Path(filename).name!=filename or '/' in filename or '\\' in filename:
        raise ValueError('バックアップ保存名が不正です')
    result=BackupDriveClient(drive).upload_once(directory/filename,directory/(filename+'.upload.json'),
                                               filename,state['receipt']['sha256'],commit)
    state.update(phase='verified',verified_at=now.isoformat())
    BackupDriveClient._update(job,state)
    return {**result,'daily_backup_verified':True,'decryption_verified':False,
            'business_calls_performed':False,'production_start_allowed':False}
