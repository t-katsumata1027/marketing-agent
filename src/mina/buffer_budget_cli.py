"""root所有の今回承認原本だけを使う一回probeと共通枠予約。"""
import argparse,json,os
from pathlib import Path
from .current_day_cli import protected_json
from .readiness import release_commit
from .replacement_client import timestamp
from .buffer_budget import subject,run_probe,reserve,release
from .buffer_publisher import production_client
from .store import Store

def main():
    parser=argparse.ArgumentParser(description='Buffer実枠の一回確認と共通予約')
    parser.add_argument('command',choices=('identity','probe','reserve','release'));parser.add_argument('--config')
    args=parser.parse_args();identity=subject(os.environ['BUFFER_API_KEY'])
    if args.command=='identity':print(json.dumps({'subject':identity}));return
    if os.getenv('MINA_BUFFER_BUDGET_ENABLED','false')!='true':raise ValueError('buffer_budget_disabled')
    if not args.config:raise ValueError('quota承認原本が必要です')
    settings,_=protected_json(args.config,root_owned=True)
    if set(settings)!={'spec','source_commit','database','grant_id','budget_id','subject','target_date','not_after','units','approval_reference'} or settings['spec']!='mina-buffer-quota-reconcile-v1' or settings['subject']!=identity or settings['target_date']!='2026-10-29' or not settings['approval_reference']:raise ValueError('quota承認範囲が不一致です')
    release_commit(Path(__file__).resolve().parents[2],settings['source_commit'])
    if not Path(settings['database']).is_file() or Path(settings['database']).is_symlink():raise ValueError('既存台帳だけを使います')
    store=Store(settings['database'])
    if args.command=='probe':
        result=run_probe(store,production_client(store),settings['grant_id']);print(json.dumps({'verified_windows':result}));return
    if args.command=='reserve':
        reserve(store,settings['budget_id'],identity,settings['target_date'],settings['units'],timestamp(settings['not_after']).timestamp());print(json.dumps({'reserved':settings['units']}));return
    release(store,settings['budget_id']);print(json.dumps({'released_untransmitted_only':True}))

if __name__=='__main__':main()
