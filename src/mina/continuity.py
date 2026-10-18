"""既存台帳だけから作成・投稿の停止と応急候補を分類する。実行や解除は行わない。"""
import argparse
import json
import math
import sqlite3
from contextlib import closing
from datetime import UTC,date,datetime,timedelta
from pathlib import Path
from .domain import SLOTS,slot_time,approval_time,instant
from .audit import REASONS

STATES={'approval_pending','approved','changes_requested','cancelled','dispatching','published','outcome_unknown','expired'}
MEALS=('breakfast','lunch','dinner')

def summarize(database,target,now):
    """mode=roで読み、保存済みやローカル0件を外部の成功・不存在へ読み替えない。"""
    day=date.fromisoformat(target)
    if day.isoformat()!=target:raise ValueError('対象日はYYYY-MM-DDが必要です')
    now=instant(now)
    report={'target_date':target,'observed_at':now.isoformat(),'planning_only':True,
            'external_inventory':'unverified','current_source':'unverified','database_available':False,
            'approval_deadline':approval_time(day).isoformat(),'slots':[]}
    try:
        with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required={'candidates','preparation','preparation_steps','mina_requests','buffer_publication'}
            report['ledger_complete']=required<=tables
            if not report['ledger_complete']:return report
            report['database_available']=True
            prep=db.execute('SELECT state FROM preparation WHERE target_date=?',(target,)).fetchone()
            state=prep[0] if prep else 'not_started'
            report['creation_status']='held' if state.startswith('held:') else state if state in ('not_started','started','complete') else 'unconfirmed'
            report['preparation_deadline_missed']=now>=approval_time(day) and state!='complete'
            request_rows=list(db.execute('SELECT phase FROM mina_requests WHERE target_date=?',(target,)))
            report['local_request_count']=len(request_rows)
            report['unresolved_request_count']=sum(r[0]!='complete' for r in request_rows)
            if 'publication_holds' in tables:
                hold=db.execute('SELECT state FROM publication_holds WHERE target_date=?',(target,)).fetchone()
                report['day_hold_active']=bool(hold and hold[0]!='released')
            else:report['day_hold_active']=False
            calories={}
            for slot in SLOTS:
                scheduled=slot_time(day,slot)
                item={'slot':slot,'scheduled_at':scheduled.isoformat(),'time_window':'future' if now<scheduled else 'due' if now<=scheduled+timedelta(seconds=120) else 'elapsed',
                      'latest_local_review':'unknown','local_candidate_state':'absent','recovery':'needs_source_recheck'}
                row=db.execute('SELECT state,approved,dispatch_started,x_id FROM candidates WHERE target_date=? AND slot=?',(target,slot)).fetchone()
                if row:
                    item['local_candidate_state']=row['state'] if row['state'] in STATES else 'unknown'
                    item['has_local_approval']=row['approved'] is not None
                    item['local_submission_started']=row['dispatch_started'] is not None
                    item['has_publication_id']=row['x_id'] is not None
                route=db.execute('SELECT state,attempted,post_id,request_json FROM buffer_publication WHERE target_date=? AND slot=?',(target,slot)).fetchone()
                item['external_dispatch_recorded']=bool(route and (route['attempted'] is not None or route['post_id'] is not None or route['request_json'] is not None or route['state']!='armed'))
                if slot in MEALS:
                    corrected=db.execute('SELECT result FROM preparation_steps WHERE target_date=? AND step=? AND state=?',(target,'meal_correction:'+slot,'complete')).fetchone()
                    item['saved_result_recorded']=False
                    if corrected:
                        value=json.loads(corrected[0]);result=value.get('result',{})
                        n=result.get('analysis',{}).get('summary',{}).get('totalCalories')
                        if type(n) in (int,float) and math.isfinite(n) and n>=0:calories[slot]=n
                        item['saved_result_recorded']=bool(result)
                    reviewed=db.execute("SELECT result FROM preparation_steps WHERE target_date=? AND step LIKE ? AND state='complete' ORDER BY started DESC,rowid DESC LIMIT 1",(target,'meal_review:'+slot+'%')).fetchone()
                    if reviewed:
                        status=json.loads(reviewed[0]).get('status')
                        item['latest_local_review']=status if status in ('publishable','hold') else 'unknown'
                    if item['saved_result_recorded'] and item['latest_local_review']=='hold':item['recovery']='saved_meal_quality_hold'
                    elif item['saved_result_recorded'] and item['latest_local_review']=='publishable':item['recovery']='individual_source_recheck_candidate'
                if item['external_dispatch_recorded'] or item['local_candidate_state'] in ('dispatching','outcome_unknown','published') or item.get('has_publication_id'):
                    item['recovery']='external_reconciliation_required'
                elif report['unresolved_request_count']:item['recovery']='request_reconciliation_required'
                elif report['day_hold_active']:item['recovery']='day_publication_hold'
                report['slots'].append(item)
            total=sum(calories.values()) if set(calories)==set(MEALS) else None
            report['recorded_total_calories']=total
            report['daily_energy_in_range']=1800<=total<=2000 if total is not None else None
            daily=next(r for r in report['slots'] if r['slot']=='daily')
            if total is not None and not report['daily_energy_in_range'] and daily['recovery']=='needs_source_recheck':daily['recovery']='daily_energy_out_of_range'
            report['publication_failures']=[]
            if 'operation_events' in tables:
                for r in db.execute("SELECT slot,stage,reason FROM operation_events WHERE target_date=? AND category='publication' AND event IN ('failed','stopped') ORDER BY id DESC LIMIT 8",(target,)):
                    report['publication_failures'].append({'slot':r['slot'] if r['slot'] in SLOTS else 'unknown','reason':r['reason'] if r['reason'] in REASONS else 'unclassified'})
            report['required_before_any_publication']=['current_source_and_real_ogp','independent_review','history_and_external_inventory','explicit_slot_policy','new_human_approval']
    except (sqlite3.Error,OSError,ValueError,TypeError,KeyError,AttributeError):
        # 壊れた台帳から部分的な正しい候補を推測しない。例外原文も返さない。
        report.update(database_available=False,ledger_complete=False,slots=[],error='local_evidence_unavailable')
    return report

def main():
    parser=argparse.ArgumentParser(description='作成・投稿を分ける読取専用の計画診断')
    parser.add_argument('--database',type=Path,required=True);parser.add_argument('--date',required=True)
    args=parser.parse_args();print(json.dumps(summarize(args.database,args.date,datetime.now(UTC)),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
