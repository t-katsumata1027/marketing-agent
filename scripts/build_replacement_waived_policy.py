"""本人の金額上限撤廃に対応するpolicy案を作る。設定反映・外部通信は行わない。"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.mina.replacement_contract import digest,canonical


def build():
    approval={'date':'2026-10-23','scope':'mina_saved_meal_replacement','cost_control_mode':'waived',
        'decision':'本人が追加課金100円/日を金額上限なしへ変更すると明示回答',
        'retained_controls':['cumulative_max_replans_2','daily_analysis_limit_60','shortest_existing_deadline','publication_hold','independent_review','new_approval']}
    reference=digest(approval)
    agent={'approval_digest':reference,'approval_reference_digest':reference,'cost_control_mode':'waived',
        'currency':None,'limit_units':None,'opening_exposure_units':None,'server_allocation_units':None,
        'call_bounds':{k:None for k in ('menu','image','review','drive','rebuild')},
        'cost_bounds_verified':False,'existing_usage_verified':False,'server_allocation_verified':False,
        'policy_version':'mina_gemini_waived_20261023_v1','analysis_provider':'gemini','model':'gemini-3.1-flash-lite',
        'max_output_tokens':2048,'required_channels':['x','instagram'],'max_replans':2,'daily_analysis_limit':60}
    sha=digest(agent)
    provider={'version':agent['policy_version'],'budget_policy_digest':sha,'approval_reference_digest':reference,
        'analysis_provider':'gemini','model':'gemini-3.1-flash-lite','max_output_tokens':2048,
        'cost_control_mode':'waived','currency':None,'budget_total_minor':None,'prepare_max_cost_minor':None,
        'commit_max_cost_minor':None,'cost_bounds_verified':False,'agent_integration_verified':False}
    return {'approval':approval,'approval_reference_digest':reference,'policy_digest':sha,'agent_policy':agent,
        'provider_policy_proposal':provider,'activation_allowed':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    value=build()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('xb') as output:output.write(canonical(value))
    print(json.dumps({'policy_version':value['agent_policy']['policy_version'],'policy_digest':value['policy_digest'],
        'approval_reference_digest':value['approval_reference_digest'],'activation_allowed':False}))
