"""管理資格を持たないAgent側の有界file handshake。inboxはJSONデータだけ。"""
import hashlib,os,stat,tempfile,time
from pathlib import Path
from jsonschema import Draft202012Validator,FormatChecker
from .replacement_contract import canonical,stamp
from .replacement_client import timestamp
from .scoped_management_proofs import root_file,root_bytes
from .scoped_coordination_json import strict_json
from jsonschema.exceptions import ValidationError

PROTOCOL='mina-management-handshake-v1'
SCHEMA=Path(__file__).parent/'contracts/management_handshake_v1/envelope.schema.json'
SCHEMA_SHA='38b94c69bcd23f5384b15d2900ecc58fcca280b01286a50391d6cf87b189759a'
AGENT_INBOX_ROOT=Path('/var/lib/mina-agent/scoped-coordination')
NAMES={'initial-snapshot-ready':'initial-snapshot.json','reviews-ready':'reviews.json','snapshot-ready':'snapshot.json',
       'bundle-ready':'bundle.json','grant-result':'result.json','stopped':'stopped.json'}


def sync_directory(directory):
    if os.name=='posix':
        fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)


def publish_once(path,raw):
    """公開名を上書きせず、fsyncした実バイトだけをatomic linkで公開する。"""
    if path.is_symlink():raise ValueError('scoped_coordination_inbox_invalid')
    if path.exists():
        if path.read_bytes()!=raw:raise ValueError('scoped_coordination_payload_changed')
        return
    fd,name=tempfile.mkstemp(prefix='.pending-',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(fd,'wb') as output:output.write(raw);output.flush();os.fsync(output.fileno())
        os.chmod(temporary,0o600)
        try:os.link(temporary,path,follow_symlinks=False)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes()!=raw:raise ValueError('scoped_coordination_payload_changed')
        sync_directory(path.parent)
    finally:
        temporary.unlink();sync_directory(path.parent)


class ManagementStopped(ValueError):
    def __init__(self,issue_started):
        super().__init__('scoped_coordination_management_stopped')
        self.issue_started=issue_started


class ManagementHandshake:
    def __init__(self,manifest_reference,scope,clock,helper_shas,*,sleep=time.sleep):
        raw=SCHEMA.read_bytes()
        if SCHEMA.is_symlink() or SCHEMA.parent.is_symlink() or hashlib.sha256(raw).hexdigest()!=SCHEMA_SHA:raise ValueError('scoped_coordination_schema_changed')
        self.validator=Draft202012Validator(strict_json(raw),format_checker=FormatChecker())
        self.validator.check_schema(self.validator.schema)
        self.manifest_reference=manifest_reference;self.scope=scope;self.clock=clock;self.sleep=sleep
        self.scope_raw=canonical(scope)
        manifest_raw=root_file(manifest_reference)
        manifest=strict_json(manifest_raw,65536)
        if (set(manifest)!={'protocol','scope','helper_shas'} or manifest['protocol']!=PROTOCOL or manifest['scope']!=scope
                or set(manifest['helper_shas'])!={'agent','management','fs_proxy','grant_cli'} or manifest['helper_shas']!=helper_shas):
            raise ValueError('scoped_coordination_manifest_changed')
        import re
        if any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v) for v in manifest['helper_shas'].values()):
            raise ValueError('scoped_coordination_manifest_changed')
        self.manifest_raw=manifest_raw
        self.helper_shas=dict(helper_shas)
        self.envelope('reviews-ready',{'name':'reviews.json','sha256':'0'*64},scope['not_after'])
        self.outbox=Path(manifest_reference['path']).parent
        name=scope['case_uuid']+'-r1-'+scope['human_approval_sha256']
        self.inbox=AGENT_INBOX_ROOT/name
        if AGENT_INBOX_ROOT.is_symlink() or self.inbox.is_symlink():raise ValueError('scoped_coordination_inbox_invalid')
        AGENT_INBOX_ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
        info=AGENT_INBOX_ROOT.stat()
        if os.name=='posix' and (info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)&0o077):raise ValueError('scoped_coordination_inbox_invalid')
        self.inbox.mkdir(mode=0o700,exist_ok=True)
        self.private()

    def validate(self,value):
        try:self.validator.validate(value)
        except ValidationError:raise ValueError('scoped_coordination_envelope_invalid') from None

    def private(self):
        for path in (AGENT_INBOX_ROOT,self.inbox):
            info=path.stat()
            if path.is_symlink() or (os.name=='posix' and (info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)&0o077)):
                raise ValueError('scoped_coordination_inbox_invalid')

    def check(self):
        self.private()
        if root_file(self.manifest_reference)!=self.manifest_raw:raise ValueError('scoped_coordination_manifest_changed')
        if SCHEMA.is_symlink() or SCHEMA.parent.is_symlink() or hashlib.sha256(SCHEMA.read_bytes()).hexdigest()!=SCHEMA_SHA:raise ValueError('scoped_coordination_schema_changed')
        if canonical(self.scope)!=self.scope_raw:raise ValueError('scoped_coordination_scope_changed')
        if self.clock()>=timestamp(self.scope['not_after']):raise ValueError('scoped_coordination_deadline')

    def envelope(self,phase,payload,until):
        value={'protocol':PROTOCOL,'phase':phase,'scope':self.scope,'payload':payload,'created_at':stamp(self.clock()),'valid_until':until}
        self.validate(value)
        if not self.clock()<timestamp(until)<=timestamp(self.scope['not_after']):raise ValueError('scoped_coordination_deadline')
        return value

    def publish(self,phase,value,until):
        self.check()
        if phase not in ('reviews-ready','bundle-ready','stopped'):raise ValueError('scoped_coordination_phase_invalid')
        required={'reviews-ready':{'review_round','parent_history_digest','claim_digest','formal_reviews','history'},
                  'bundle-ready':{'input','approval','evidence'},'stopped':{'reason_code','issue_started'}}[phase]
        if not isinstance(value,dict) or set(value)!=required:raise ValueError('scoped_coordination_payload_invalid')
        data=canonical(value)
        if len(data)>1048576:raise ValueError('scoped_coordination_payload_too_large')
        payload={'name':NAMES[phase],'sha256':hashlib.sha256(data).hexdigest()}
        event=self.inbox/(phase+'.envelope.json')
        publish_once(self.inbox/payload['name'],data)
        if event.exists():
            old=strict_json(event.read_bytes(),65536);self.validate(old)
            if old['scope']!=self.scope or old['phase']!=phase or old['payload']!=payload or old['valid_until']!=until:
                raise ValueError('scoped_coordination_payload_changed')
            if not self.clock()<timestamp(old['valid_until']):raise ValueError('scoped_coordination_deadline')
        else:publish_once(event,canonical(self.envelope(phase,payload,until)))

    def read(self,phase):
        self.check()
        value=strict_json(root_bytes(self.outbox/(phase+'.envelope.json')),65536);self.validate(value)
        if value['scope']!=self.scope or value['phase']!=phase:raise ValueError('scoped_coordination_scope_changed')
        if not timestamp(value['created_at'])<=self.clock()<timestamp(value['valid_until'])<=timestamp(self.scope['not_after']):
            raise ValueError('scoped_coordination_deadline')
        data=root_bytes(self.outbox/value['payload']['name'],1048576)
        if hashlib.sha256(data).hexdigest()!=value['payload']['sha256']:raise ValueError('scoped_coordination_reference_changed')
        return strict_json(data),value

    def wait(self,phase,*,max_polls=240):
        if phase not in ('initial-snapshot-ready','snapshot-ready','grant-result'):raise ValueError('scoped_coordination_phase_invalid')
        for _ in range(max_polls):
            self.check()
            try:
                stopped,_=self.read('stopped')
                if set(stopped)!={'reason_code','issue_started'} or type(stopped['issue_started']) is not bool:
                    raise ValueError('scoped_coordination_result_invalid')
                raise ManagementStopped(stopped['issue_started'])
            except FileNotFoundError:pass
            try:return self.read(phase)
            except FileNotFoundError:self.sleep(.25)
        raise ValueError('scoped_coordination_wait_exhausted')

    def snapshot(self,phase):
        value,_=self.wait(phase)
        if set(value)!={'native','unissued'}:raise ValueError('scoped_coordination_snapshot_invalid')
        result={}
        for kind,name in (('native','initial-native.json' if phase=='initial-snapshot-ready' else 'fresh-native.json'),
                          ('unissued','initial-unissued.json' if phase=='initial-snapshot-ready' else 'fresh-unissued.json')):
            ref=value[kind]
            if set(ref)!={'name','sha256'} or ref['name']!=name:raise ValueError('scoped_coordination_snapshot_invalid')
            path=self.outbox/name;data=root_file({'path':str(path),'sha256':ref['sha256']})
            result[kind]=strict_json(data)
            result[kind+'_reference']={'path':str(path),'sha256':ref['sha256']}
        return result

    def publish_bundle(self,packet):
        if set(packet)!={'input','approval','evidence','agent_issues_authority'} or packet['agent_issues_authority'] is not False:
            raise ValueError('scoped_coordination_bundle_invalid')
        refs={}
        for role in ('input','approval','evidence'):
            raw=canonical(packet[role]);name='grant-'+role+'.json'
            if len(raw)>1048576:raise ValueError('scoped_coordination_payload_too_large')
            publish_once(self.inbox/name,raw);refs[role]={'name':name,'sha256':hashlib.sha256(raw).hexdigest()}
        self.publish('bundle-ready',refs,self.scope['not_after'])
