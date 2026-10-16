"""バックアップの鮮度・停止状態・容量を、秘密値を返さず読取確認する。"""
import json
import hashlib
import shutil
from datetime import UTC,datetime
from pathlib import Path

from .artifact_backup import checked_path
from .encrypted_backup import read_regular,LIMIT,MAGIC


def backup_health(directory,now=None):
    """保存済みの受領記録を確認する。復号・実Drive照合の代用にはしない。"""
    now=now or datetime.now(UTC)
    root=checked_path(directory)
    if not root.is_dir():
        return {'healthy':False,'reason_codes':['backup_directory_missing'],'mutations_performed':False}
    problems=[];verified=[];uncertain=0;size=0;count=0
    entries=list(root.iterdir())
    if len(entries)>4096:
        return {'healthy':False,'reason_codes':['backup_inventory_limit'],'mutations_performed':False}
    for path in entries:
        if path.name.endswith('.encrypted'):
            checked_path(path)
            if not path.is_file():raise ValueError('バックアップ保存先に特殊な実体があります')
            count+=1;size+=path.stat().st_size
        if path.name.startswith('job-') and path.suffix=='.json':
            try:
                record=json.loads(read_regular(path,131072))
                if record.get('phase')!='verified':
                    uncertain+=1;continue
                timestamp=datetime.fromisoformat(record['verified_at'])
                if timestamp.tzinfo is None or timestamp>now:
                    raise ValueError()
                verified.append((timestamp,record))
            except (ValueError,KeyError,TypeError,AttributeError):
                problems.append('backup_receipt_invalid')
    latest=max(verified,key=lambda item:item[0]) if verified else None
    age=(now-latest[0]).total_seconds() if latest else None
    if latest:
        try:
            record=latest[1];filename=record['filename']
            if not isinstance(filename,str) or Path(filename).name!=filename or '/' in filename or '\\' in filename:
                raise ValueError()
            raw=read_regular(root/filename,LIMIT+4096)
            receipt=record['receipt']
            journal=json.loads(read_regular(root/(filename+'.upload.json'),131072))
            if (not raw.startswith(MAGIC) or hashlib.sha256(raw).hexdigest()!=receipt['sha256']
                    or len(raw)!=receipt['bytes'] or journal.get('state')!='verified'
                    or journal['binding']['sha256']!=receipt['sha256']
                    or journal['binding']['filename']!=filename):
                raise ValueError()
        except (OSError,ValueError,KeyError,TypeError,AttributeError):
            problems.append('backup_local_copy_invalid')
    if age is None or age>36*3600:problems.append('backup_stale_or_missing')
    if uncertain:problems.append('backup_reconciliation_required')
    free=shutil.disk_usage(root).free
    if free<2*1024**3:problems.append('backup_disk_space_low')
    if count>30 or size>10*1024**3:problems.append('backup_retention_review_required')
    return {'healthy':not problems,'reason_codes':sorted(set(problems)),
            'latest_verified_age_seconds':age,'unresolved_jobs':uncertain,
            'encrypted_file_count':count,'encrypted_bytes':size,'free_bytes':free,
            'drive_readback_performed':False,'decryption_performed':False,'mutations_performed':False}
