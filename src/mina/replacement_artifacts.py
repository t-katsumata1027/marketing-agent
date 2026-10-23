"""旧日次ファイルを上書きせず、不変manifestから採用先を照合する。"""
import hashlib
import json
import os
from pathlib import Path
from .replacement_contract import canonical,digest

def write_version(directory,files):
    base=Path(directory);base.mkdir(parents=True,exist_ok=False,mode=0o700)
    hashes={}
    for name,raw in files.items():
        if Path(name).name!=name or name in ('manifest.json','active-replacement.json') or not isinstance(raw,bytes):raise ValueError('replacement_artifact_invalid')
        with (base/name).open('xb') as output:output.write(raw);output.flush();os.fsync(output.fileno())
        hashes[name]=hashlib.sha256(raw).hexdigest()
    raw=canonical(hashes)
    with (base/'manifest.json').open('xb') as output:output.write(raw);output.flush();os.fsync(output.fileno())
    return hashlib.sha256(raw).hexdigest()

def resolve(base):
    base=Path(base).resolve();pointer=base/'active-replacement.json'
    if pointer.is_symlink():raise ValueError('replacement_artifact_symlink')
    if not pointer.exists():return base
    value=json.loads(pointer.read_text(encoding='utf-8'));name=value['directory']
    if Path(name).name!=name:raise ValueError('replacement_artifact_escape')
    version=base/name
    if version.is_symlink() or not version.resolve().is_relative_to(base):raise ValueError('replacement_artifact_escape')
    if (version/'manifest.json').is_symlink():raise ValueError('replacement_artifact_symlink')
    raw=(version/'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=value['manifest_sha256']:raise ValueError('replacement_artifact_manifest_changed')
    for filename,sha in json.loads(raw).items():
        path=version/filename
        if Path(filename).name!=filename or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('replacement_artifact_changed')
    return version

def adopt(store,target,owner,epoch,daily_directory,version_name,manifest_sha):
    """DB側の採用意図を先に固定。途中停止は同じmanifestだけで再照合する。"""
    base=Path(daily_directory)/target
    value={'directory':version_name,'manifest_sha256':manifest_sha};raw=canonical(value)
    if Path(version_name).name!=version_name:raise ValueError('replacement_artifact_escape')
    version=base/version_name
    if version.is_symlink() or not version.resolve().is_relative_to(base.resolve()) or (version/'manifest.json').is_symlink():raise ValueError('replacement_artifact_escape')
    manifest=(version/'manifest.json').read_bytes()
    if hashlib.sha256(manifest).hexdigest()!=manifest_sha:raise ValueError('replacement_artifact_manifest_changed')
    for name,sha in json.loads(manifest).items():
        path=version/name
        if Path(name).name!=name or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('replacement_artifact_changed')
    with store.transaction() as db:
        h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(target,)).fetchone()
        if not h or (h['owner'],h['epoch'],h['state'])!=(owner,epoch,'saved_pending_review'):raise ValueError('replacement_hold_changed')
        db.execute('CREATE TABLE IF NOT EXISTS replacement_adoptions (target_date TEXT PRIMARY KEY,owner TEXT,epoch INTEGER,pointer TEXT)')
        old=db.execute('SELECT * FROM replacement_adoptions WHERE target_date=?',(target,)).fetchone()
        if old and (old['owner'],old['epoch'],old['pointer'])!=(owner,epoch,raw.decode()):raise ValueError('replacement_adoption_changed')
        db.execute('INSERT OR IGNORE INTO replacement_adoptions VALUES(?,?,?,?)',(target,owner,epoch,raw.decode()))
    temporary=base/('replacement-pointer-'+owner+'.json')
    if temporary.exists():
        if temporary.read_bytes()!=raw:raise ValueError('replacement_adoption_changed')
    else:
        with temporary.open('xb') as output:output.write(raw);output.flush();os.fsync(output.fileno())
    if (base/'active-replacement.json').exists() and (base/'active-replacement.json').read_bytes()!=raw:raise ValueError('replacement_adoption_changed')
    os.replace(temporary,base/'active-replacement.json')
    return resolve(base)
