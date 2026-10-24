"""保存済み観測だけから、未承認の救済準備票を作る。通信・DB書込み・権限発行はしない。"""
import argparse
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path


def load(path, lines=False):
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise ValueError('通常の観測ファイルが必要です')
    raw=path.read_bytes()
    if len(raw)>2*1024*1024:raise ValueError('観測ファイルが上限を超えています')
    value=[json.loads(line) for line in raw.decode('utf-8-sig').splitlines() if line.strip()] if lines else json.loads(raw)
    return value,hashlib.sha256(raw).hexdigest()


def prepare(api_path,agent_path):
    api,api_sha=load(api_path,True);agent,agent_sha=load(agent_path)
    target='2026-10-24'
    rows=[r for r in api if r.get('date')==target and r.get('endpoint')=='state']
    if len(rows)!=1 or rows[0].get('status')!=200 or rows[0].get('m1_valid') is not True:
        raise ValueError('本日の完全な正本の観測が必要です')
    state=rows[0]
    if state.get('meal_count')!=1 or len(state.get('meals',[]))!=1 or state['meals'][0].get('slot')!='breakfast':
        raise ValueError('朝食1件の観測から状態が変わっています。再照合が必要です')
    meal=state['meals'][0]
    if (not re.fullmatch('rv1_[0-9a-f]{64}',meal.get('result_version',''))
            or not re.fullmatch('sv1_[0-9a-f]{64}',state.get('state_version',''))
            or any(not re.fullmatch('[0-9a-f]{64}',meal.get(k,'')) for k in ('image_sha256','stored_image_sha256'))):
        raise ValueError('正本・画像の観測形式が不正です')
    if agent.get('target')!=target or agent.get('candidates') or agent.get('instagram_candidates'):
        raise ValueError('対象日・候補の状況が変わっています')
    requests=agent.get('mina_requests',[])
    if (sorted((r['slot'],r['operation'],r['phase']) for r in requests)!=
        [('breakfast','meal_correction','complete'),('breakfast','meal_register','complete')]
        or any(json.loads(r.get('observation') or '{}').get('persistence')!='saved' for r in requests)):
        raise ValueError('既存朝食の登録・訂正成否が未確認です')
    images=agent.get('drive.json',{}).get('images',{})
    if set(images)!={'breakfast','lunch','dinner'} or any(
        not all(row.get(k) is True for k in ('file_exists','sha_matches','drive_verified')) for row in images.values()):
        raise ValueError('3画像の保存原本が一致しません')
    observed=datetime.fromisoformat(state['checked_at_utc'])
    expires=observed+timedelta(seconds=120)
    return {'document_type':'agent-rescue-readiness-observation-v1','target_date':target,
        'purpose':'準備票のみ。正式Schemaの要求入力・救済許可・品質合格の代用にはしない',
        'execution_authorized':False,'limited_rescue_enabled':False,'contract_status':'awaiting_fixed_provider_contract',
        'observed_state_version':state['state_version'],'observed_breakfast':meal,
        'source_sha256':{'api_observation':api_sha,'agent_observation':agent_sha},
        'observed_at':observed.isoformat(),'evidence_valid_until':expires.isoformat(),
        'fresh_at_generation':observed<=datetime.now(UTC)<expires,
        'standard_replacement_deadline_jst':'2026-10-23T20:00:00+09:00',
        'breakfast_quality_hold':'image_analysis_mismatch','image_references_intact':True,
        'next_normal_registration_slots':['lunch','dinner'],'existing_breakfast_repost_allowed':False,
        'external_inventory_status':'execution_time_full_inventory_required',
        'pending':['正式な救済Schema・状態表・DDL・固定commit・独立レビュー',
                   'Agent専用adapter・昼夕収集許可と隔離受入',
                   '本体とAgentの本番反映承認・当日実行承認',
                   '本人承認の絶対終了時刻と初回有効化時刻',
                   '昼夕登録後の新鮮な3食正本・版・世代',
                   '旧期限・真の開始時刻・累積回数・費用・unknownの原本照合',
                   '外部予約全状態全ページ・Instagram担当の所有照合',
                   '全3食の独立点検・実合計1800〜2000kcal・新版再承認'],
        'prohibited':['新UUIDやepisodeによる初期化','朝食の再POST・削除再登録',
                      '品質保留解除','昼夕だけの先行公開','準備票から直接API送信']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-observation',required=True);parser.add_argument('--agent-observation',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args();result=prepare(args.api_observation,args.agent_observation)
    path=Path(args.output)
    with path.open('x',encoding='utf-8') as out:json.dump(result,out,ensure_ascii=False,indent=2);out.write('\n')
    print(json.dumps({'saved':str(path),'execution_authorized':False,'fresh_at_generation':result['fresh_at_generation']},ensure_ascii=False))


if __name__=='__main__':main()