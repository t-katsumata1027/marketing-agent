"""管理資格を渡さず、Agentが置換できない管理側原本だけを読む。"""
import hashlib,os,stat
from pathlib import Path
from .replacement_client import timestamp
from .scoped_coordination_json import strict_json

MANAGEMENT_ROOT=Path('/var/lib/mina-scoped-coordination')


def root_bytes(path,limit=65536):
    path=Path(path);root=MANAGEMENT_ROOT
    if os.name!='posix' or not path.is_absolute() or not path.is_relative_to(root):
        raise ValueError('scoped_coordination_authority_unavailable')
    if path.resolve()!=path or root.resolve()!=root:raise ValueError('scoped_coordination_reference_invalid')
    for entry in (root.parent,root):
        if entry.is_symlink() or entry.stat().st_uid!=0 or stat.S_IMODE(entry.stat().st_mode)&0o022:
            raise ValueError('scoped_coordination_authority_invalid')
    current=path
    while current!=root:
        if current.is_symlink():raise ValueError('scoped_coordination_authority_invalid')
        info=current.stat()
        if info.st_uid!=0 or stat.S_IMODE(info.st_mode)&0o022:raise ValueError('scoped_coordination_authority_invalid')
        current=current.parent
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)&0o022 or info.st_size>limit:
            raise ValueError('scoped_coordination_authority_invalid')
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(limit+1)
        current=path.stat(follow_symlinks=False)
        if (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)!=(current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns):
            raise ValueError('scoped_coordination_reference_changed')
    finally:os.close(fd)
    if len(raw)>limit:raise ValueError('scoped_coordination_reference_invalid')
    return raw


def root_file(reference):
    if set(reference)!={'path','sha256'}:raise ValueError('scoped_coordination_reference_invalid')
    raw=root_bytes(reference['path'])
    if hashlib.sha256(raw).hexdigest()!=reference['sha256']:
        raise ValueError('scoped_coordination_reference_changed')
    return raw


def unissued(reference,scope,integration,clock,planned_prepare_uuid):
    value=strict_json(root_file(reference))
    fixed={'artifact_spec':'mina-rescue-v3-unissued-proof-v1','target_date':scope[1],'target_slot':scope[2],
        'case_uuid':scope[3],'planned_prepare_uuid':planned_prepare_uuid,'source_body_commit':integration['body_commit'],
        'schema_sha256':integration['schema_sha256'],'complete':True,'read_only':True,'same_case_count':0,
        'same_day_slot_case_count':0,'planned_prepare_request_count':0,'planned_prepare_candidate_count':0,'first_activation_present':False}
    if (set(value)!=set(fixed)|{'read_started_at','read_finished_at','valid_until'}
            or any(type(value.get(k)) is not type(v) or value.get(k)!=v for k,v in fixed.items())):
        raise ValueError('scoped_coordination_unissued_unverified')
    start,end,until=(timestamp(value[k]) for k in ('read_started_at','read_finished_at','valid_until'))
    if not start<=end<=clock()<until or not 0<(until-start).total_seconds()<=120:
        raise ValueError('scoped_coordination_unissued_stale')
    return value
