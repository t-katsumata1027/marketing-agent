"""別の継続承認が揃うまでOFF。親jobの再発行・状態更新は行わない。"""
import argparse,json,os,sqlite3
from contextlib import closing
from datetime import UTC,datetime
from pathlib import Path
from .current_day_origin import TARGET
from .current_day_checkpoint import VERSION,capture,verify
from .current_day_cli import protected_json,config,scoped_meal
from .preparation import fingerprint


def main():
    p=argparse.ArgumentParser(description='10/29停止箇所だけの継続operator')
    p.add_argument('command',choices=('checkpoint','status','activate','run'))
    p.add_argument('--database',required=True);p.add_argument('--directory',required=True)
    p.add_argument('--receipts');p.add_argument('--stop-log');p.add_argument('--config')
    a=p.parse_args();root=Path(__file__).resolve().parents[2]
    if a.command in ('checkpoint','status'):
        with closing(sqlite3.connect(Path(a.database).resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            if a.command=='checkpoint':
                if os.name!='posix' or os.geteuid()!=0:raise ValueError('停止traceの原本確認はroot管理入口だけです')
                if not a.receipts or not a.stop_log:raise ValueError('実CLI原本と停止traceが必要です')
                trace=Path(a.stop_log)
                if trace.is_symlink() or trace.stat().st_uid!=0 or trace.stat().st_mode&0o022:raise ValueError('root停止原本が必要です')
                value=capture(db,a.directory,a.receipts,trace.read_bytes());print(json.dumps(value,ensure_ascii=False))
            else:
                exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='current_day_continuations'").fetchone()
                row=db.execute('SELECT state,activated_at,deadline FROM current_day_continuations WHERE target_date=?',(TARGET,)).fetchone() if exists else None
                print(json.dumps(dict(row) if row else {'state':'not_activated'}))
            db.rollback()
        return
    if os.getenv('MINA_CURRENT_DAY_CONTINUATION_ENABLED','false').lower()!='true':raise ValueError('current_day_continuation_disabled')
    if not a.config:raise ValueError('継続専用の別承認構成が必要です')
    settings,code=config(a.config,root,version=VERSION,extras=('checkpoint','receipts'))
    if Path(a.database).resolve()!=Path(settings['database']).resolve() or Path(a.directory).resolve()!=Path(settings['directory']).resolve():raise ValueError('継続の対象領域が不一致です')
    reference=settings['checkpoint'];snapshot,sha=protected_json(reference['path'],root_owned=True)
    if set(reference)!={'path','sha256'} or sha!=reference['sha256'] or fingerprint(snapshot)!=settings['approval']['checkpoint_digest']:raise ValueError('停止原本の版が不一致です')
    if Path(snapshot['base']).resolve()!=Path(a.directory).resolve() or Path(snapshot['receipts']).resolve()!=Path(settings['receipts']).resolve():raise ValueError('停止原本の保存先を変更しません')
    def check():
        code();_,current=protected_json(reference['path'],root_owned=True)
        if current!=sha:raise ValueError('root停止原本が変更されています')
    from .store import Store
    from .buffer_publisher import production_client
    from .replacement_inventory import ExternalInventory,BufferInventoryAdapter
    from .current_day_continuation import ContinuationGate,execute
    from .clients import require_live,DiscordClient
    require_live();store=Store(a.database);clock=lambda:datetime.now(UTC);meal=scoped_meal()
    inventory=ExternalInventory(store,BufferInventoryAdapter(production_client(store),settings['organization_id']),clock,settings['channels'])
    gate=ContinuationGate(store,settings['approval'],snapshot,clock,check,inventory)
    if a.command=='activate':
        check()
        with store.transaction() as db:
            gate.parent(db)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='current_day_continuations'").fetchone() and db.execute('SELECT 1 FROM current_day_continuations WHERE target_date=?',(TARGET,)).fetchone():raise ValueError('発行済み継続権限は元状態の照会だけに戻ってください')
        receipt=inventory.check(TARGET);state=meal.day_state(TARGET)
        deadline=gate.issue(state,receipt)
        print(json.dumps({'target_date':TARGET,'deadline':deadline.isoformat(),'state':'issued','parent_state':'held','sns_enabled':False}));return
    from .codex_client import generation_client
    from .drive import DriveClient
    from .ogp import capture_ogp
    evidence,_=protected_json(settings['history']['path'])
    result=execute(gate,generation_client(),DriveClient.from_env(),meal,settings['host'],evidence,capture_ogp,DiscordClient(os.environ['DISCORD_BOT_TOKEN'],settings['discord_channel']),settings['approval']['code_commit'])
    print(json.dumps(result))


if __name__=='__main__':main()
