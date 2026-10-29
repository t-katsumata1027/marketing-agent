"""29限定の明示operator。通常daemonから起動せず、既定OFFを維持する。"""
import argparse,hashlib,json,os,re,sqlite3,stat
from contextlib import closing
from datetime import UTC,datetime
from pathlib import Path
from .current_day_origin import TARGET,VERSION,capture,policy_proof
from .preparation import fingerprint


def protected_json(path,root_owned=False,*,max_bytes=131072):
    """資格原文を読まない承認ファイル。リンク・第三者書込・過大資料を拒否する。"""
    if type(max_bytes) is not int or max_bytes not in (131072,262144):raise ValueError('原本読取上限が不正です')
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>max_bytes:raise ValueError('承認原本の実体がありません')
    if any(p.is_symlink() for p in path.parents):raise ValueError('承認原本の親リンクを拒否します')
    if root_owned and os.name!='posix':raise ValueError('実行/配置承認はroot所有の管理原本が必要です')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>max_bytes:raise ValueError('承認原本の実体が不正です')
        if root_owned and info.st_uid!=0:raise ValueError('実行/配置承認はroot所有の管理原本が必要です')
        if os.name=='posix' and (info.st_uid not in (0,os.getuid()) or info.st_mode&0o022):raise ValueError('承認原本の所有・書込権限が不正です')
        raw=stream.read(max_bytes+1)
    if len(raw)>max_bytes:raise ValueError('承認原本が大きすぎます')
    from .replacement_client import json_object
    return json.loads(raw,object_pairs_hook=json_object),hashlib.sha256(raw).hexdigest()


def config(path,root,*,version=VERSION,extras=()):
    from .readiness import release_commit
    document,_=protected_json(path,root_owned=True)
    if set(document)!={'approval','database','directory','host','organization_id','channels','references','history','discord_channel',*extras}:raise ValueError('当日構成の形式が不正です')
    if document['host']!='www.log-eats.com' or not isinstance(document['discord_channel'],str) or not document['discord_channel'].isdigit():raise ValueError('承認されたAPI/送付先が不正です')
    approval=document['approval']
    if approval.get('target_date')!=TARGET or approval.get('spec')!=version or not re.fullmatch('[0-9a-f]{40}',approval.get('code_commit','')):raise ValueError('当日構成の版が不正です')
    binding=fingerprint({k:v for k,v in document.items() if k!='references'})
    if set(document['references'])!={'implementation_review','deployment_approval','execution_approval'}:raise ValueError('別承認3原本が必要です')
    for role,reference in document['references'].items():
        proof,sha=protected_json(reference['path'],root_owned=True)
        expected={'role':role,'contract_version':version,'config_digest':binding,'verdict':'Go' if role=='implementation_review' else 'approved'}
        if sha!=reference['sha256'] or proof!=expected:raise ValueError('当日承認原本の範囲・版が一致しません')
    history= document['history']
    if set(history)!={'path','sha256'}:raise ValueError('履歴原本の参照形式が不正です')
    _,history_sha=protected_json(history['path'])
    if history_sha!=history['sha256']:raise ValueError('履歴原本の版が不正です')
    release_commit(root,approval['code_commit'])
    protected=[*Path(root,'src/mina').rglob('*.py'),*Path(root,'assets/instagram').rglob('*')]
    seal={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in protected if p.is_file()}
    expected_config=fingerprint(document)
    def check():
        latest,_=protected_json(path,root_owned=True)
        if fingerprint(latest)!=expected_config:raise ValueError('構成原本が変更されています')
        for role,ref in document['references'].items():
            _,sha=protected_json(ref['path'],root_owned=True)
            if sha!=ref['sha256']:raise ValueError('承認原本が変更されています')
        _,history_sha=protected_json(history['path'])
        if history_sha!=history['sha256']:raise ValueError('履歴原本が変更されています')
        if any(p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=sha for p,sha in seal.items()):raise ValueError('配布コード・画像資産が変更されています')
    return document,check


def scoped_meal():
    """既存Bearerを任意のenv URLへ転送しない。読取照会も正式originだけに限る。"""
    from .clients import preparation_client
    base=os.environ.get('MINA_MEAL_API_BASE_URL','')
    if base!='https://www.log-eats.com':raise ValueError('当日APIは正式originへ固定してください')
    return preparation_client(base,os.environ.get('MINA_AGENT_API_TOKEN',''))


def main():
    parser=argparse.ArgumentParser(description='10/29準備専用operator。SNS公開機能なし')
    parser.add_argument('command',choices=('origin','status','activate','run','lookups'))
    parser.add_argument('--database',required=True);parser.add_argument('--directory',required=True)
    parser.add_argument('--config');args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    if args.command in ('origin','status','lookups'):
        with closing(sqlite3.connect(Path(args.database).resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            if args.command=='origin':
                draft=json.loads(Path(args.directory,'draft.json').read_bytes())
                origin=capture(db,draft,policy_proof(root))
                print(json.dumps({'target_date':TARGET,'origin_digest':fingerprint(origin),'old_unknown_started':origin['old_unknown_started'],
                    'old_preparation_deadline':origin['old_preparation_deadline'],'derived_policy_deadline':origin['derived_policy_deadline'],
                    'old_cost_status':'unknown_consumed','used_ordinals':[1],'new_calls':0}))
            elif args.command=='lookups':
                from .m1_contract import request_report
                from .request_ledger import RequestLedger
                from contextlib import contextmanager
                class ReadOnly:
                    @contextmanager
                    def transaction(self):yield db
                ledger=object.__new__(RequestLedger);ledger.store=ReadOnly()
                meal=scoped_meal()
                results=[]
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='mina_requests'").fetchone():
                    keys=[r[0] for r in db.execute('SELECT request_key FROM mina_requests WHERE target_date=?',(TARGET,))]
                    for key in keys:
                        row=ledger.restore(key)
                        try:report=request_report(meal.request_state(key,row['operation']),key,row['operation'])
                        except Exception:report={'persistence':'unknown'}
                        results.append({'request_key_sha256':hashlib.sha256(key.encode()).hexdigest(),'persistence':report['persistence']})
                print(json.dumps({'target_date':TARGET,'lookups':results,'post_count':0,'ledger_updates':0}))
            elif db.execute("SELECT 1 FROM sqlite_master WHERE name='current_day_jobs'").fetchone():
                row=db.execute('SELECT state,activated_at,deadline FROM current_day_jobs WHERE target_date=?',(TARGET,)).fetchone()
                print(json.dumps(dict(row) if row else {'state':'not_activated'}))
            else:print(json.dumps({'state':'not_activated'}))
            db.rollback()
        return
    if os.getenv('MINA_CURRENT_DAY_RECOVERY_ENABLED','false').lower()!='true':raise ValueError('current_day_recovery_disabled')
    if not args.config:raise ValueError('当日構成と別承認原本が必要です')
    settings,check_code=config(args.config,root)
    if Path(args.database).resolve()!=Path(settings['database']).resolve() or Path(args.directory).resolve()!=Path(settings['directory']).resolve():raise ValueError('別の台帳/原本領域へ当日権限を転用しません')
    from .store import Store
    from .clients import DiscordClient
    from .current_day_gate import Gate
    store=Store(args.database);clock=lambda:datetime.now(UTC)
    meal=scoped_meal()
    from .buffer_publisher import production_client
    from .replacement_inventory import BufferInventoryAdapter,ExternalInventory
    inventory=ExternalInventory(store,BufferInventoryAdapter(production_client(store),settings['organization_id']),clock,settings['channels'])
    gate=Gate(store,settings['approval'],clock,check_code,inventory)
    if args.command=='activate':
        from .clients import require_live
        require_live()
        receipt=inventory.check(TARGET);state=meal.day_state(TARGET)
        draft=json.loads(Path(args.directory,'draft.json').read_bytes())
        deadline=gate.issue(draft,policy_proof(root),state,receipt)
        print(json.dumps({'target_date':TARGET,'deadline':deadline.isoformat(),'state':'issued','sns_enabled':False}));return
    from .codex_client import generation_client
    from .drive import DriveClient
    from .ogp import capture_ogp
    from .current_day_recovery import execute
    from .clients import require_live
    require_live()
    evidence,_=protected_json(settings['history']['path'])
    result=execute(gate,args.directory,generation_client(),DriveClient.from_env(),meal,settings['host'],evidence,capture_ogp,
        DiscordClient(os.environ['DISCORD_BOT_TOKEN'],settings['discord_channel']),settings['approval']['code_commit'])
    print(json.dumps(result))


if __name__=='__main__':main()
