"""明示承認済みの一プロセスだけで管理協調から8候補送付まで進める。常駐処理には接続しない。"""
import argparse,hashlib,json,os,stat
from datetime import datetime,UTC
from pathlib import Path
from .scoped_coordination_json import strict_json


def private_file(path):
    path=Path(path)
    if not path.is_absolute() or path.resolve()!=path or path.is_symlink():raise ValueError('scoped_rescue_origin_file_changed')
    info=path.stat()
    if os.name!='posix' or not stat.S_ISREG(info.st_mode) or info.st_size>16777216 or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)&0o077:
        raise ValueError('scoped_rescue_origin_file_changed')
    return path.read_bytes()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--settings',required=True);parser.add_argument('--manifest-path');parser.add_argument('--manifest-sha')
    action=parser.add_mutually_exclusive_group(required=True);action.add_argument('--execute',action='store_true');action.add_argument('--lookup',action='store_true')
    args=parser.parse_args();path=Path(args.settings);settings=strict_json(private_file(path));base=path.parent
    parent=base.stat()
    if parent.st_uid!=os.geteuid() or stat.S_IMODE(parent.st_mode)&0o077:raise ValueError('scoped_rescue_origin_file_changed')
    import fcntl
    # 同一作業先の二重プロセスだけを排他する。業務回数・期限・管理側の一回発行markerとは別物。
    from .scoped_coordination import publish_once
    lock_path=base/'coordinator.lock';publish_once(lock_path,b'coordinator-v1\n')
    with lock_path.open('rb') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:return operate(settings,base,args)
        except BaseException as error:
            from .scoped_rescue_reasons import INTERNAL_REASONS
            from .replacement_contract import canonical,stamp
            reason=str(error) if isinstance(error,ValueError) and str(error) in INTERNAL_REASONS else 'scoped_rescue_reconciliation_required'
            record={'reason_code':reason,'error_type':type(error).__name__,'case_uuid':settings['case_uuid'],
                'checked_at':stamp(datetime.now(UTC)),'automatic_retry':False}
            # 最初の停止原本を保持する。例外本文や資格情報は保存しない。
            if not (base/'operator-stopped.json').exists():publish_once(base/'operator-stopped.json',canonical(record))
            print(json.dumps(record));return 2


def operate(settings,base,args):
    from .replacement_contract import canonical
    from .scoped_coordination import ManagementHandshake,publish_once
    from .scoped_rescue_runtime import build_flow
    from .store import Store
    from .clients import MealClient,DiscordClient
    from .readiness import release_commit
    from .generation import meal_review_policy_sha256
    from .scoped_pregrant_coordinator import coordinate,scope_for
    from .scoped_issue_builder import stage
    clock=lambda:datetime.now(UTC)
    if (settings['target_date'],settings['target_slot'])!=('2026-10-28','lunch'):raise ValueError('scoped_preparation_scope_invalid')
    commit=os.environ['MINA_SOURCE_COMMIT'];release_commit(Path(__file__).resolve().parents[2],commit)
    context_ref=settings['context_reference'];context_path=Path(context_ref['path'])
    if set(context_ref)!={'path','sha256'} or context_path.parent!=base:raise ValueError('scoped_rescue_origin_file_changed')
    raw=private_file(context_path)
    if hashlib.sha256(raw).hexdigest()!=context_ref['sha256']:raise ValueError('scoped_rescue_origin_file_changed')
    context=strict_json(raw)['context'];store=Store('/var/lib/mina-agent/mina.sqlite3')
    meal=MealClient(os.environ['MINA_MEAL_API_BASE_URL'],os.environ['MINA_AGENT_API_TOKEN'],max_clock_ahead=5)
    # 実行だけを明示ONにする。service環境とSNS予約/公開の禁止は維持する。
    if args.execute:os.environ['MINA_SCOPED_RESCUE_ENABLED']='true'
    os.environ['MINA_PUBLISH_ENABLED']='false';os.environ['MINA_INSTAGRAM_PUBLISH_ENABLED']='false'
    ai=drive=None
    if args.execute:
        from .codex_client import generation_client
        from .drive import DriveClient
        ai=generation_client();drive=DriveClient.from_env()
    flow=build_flow(store,context['directory'],context['draft'],context['saved'],commit,ai,drive,meal,clock,settings,recovery=args.lookup)
    if args.lookup:
        case=flow.bind_case()
        observations=[flow.reconcile(r['request_key']) for r in flow.journal.rows('requests') if r['phase']!='prepared']
        print(json.dumps({'case_state':case['state'],'observations':len(observations),'new_posts':0}));return
    if not args.manifest_path or not args.manifest_sha:raise ValueError('scoped_coordination_manifest_changed')
    policy='mina-food-ogp-v3-'+meal_review_policy_sha256()[:16]
    port=ManagementHandshake({'path':args.manifest_path,'sha256':args.manifest_sha},scope_for(flow,policy),clock,flow.management_helpers)
    from .ogp import capture_ogp
    coordinate(flow,port,meal,ai,capture_ogp,context['draft'],context['saved'],context['host'],policy,settings['owner'],settings['epoch'],
        lambda reviews,anchors,snapshot:stage(flow,reviews,anchors,snapshot,settings['owner'],settings['epoch']))
    from .scoped_preparation_completion import prepare_once,notify_once
    saved=prepare_once(flow);publish_once(base/'commit-saved.json',canonical(saved))
    from .replacement_rebuild import ReplacementRebuilder
    from .buffer_publisher import production_client
    directory=Path(context['directory'])
    builder=ReplacementRebuilder(store,meal,ai,drive,directory.parent,settings['instagram_directory'],context['evidence'],commit,context['host'],clock,
        api=production_client(store),ports_directory=directory/'scoped-rescue'/settings['case_uuid'],instagram_enabled=True)
    completed=flow.finish(saved['request']['request_uuid'],builder);publish_once(base/'completed.json',canonical(completed))
    result=notify_once(flow,completed,DiscordClient(os.environ['DISCORD_BOT_TOKEN'],os.environ['DISCORD_ALLOWED_CHANNEL_ID']))
    publish_once(base/'notifications.json',canonical(result));print(json.dumps(result))


if __name__=='__main__':raise SystemExit(main() or 0)
