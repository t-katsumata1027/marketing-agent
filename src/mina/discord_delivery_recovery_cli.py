"""root承認済みの完成案4件だけを送る。食品書込clientを公開しない。"""
import argparse,json,os
from pathlib import Path
from datetime import UTC,datetime
from .discord_delivery_recovery import VERSION,Delivery,DiscordReader
from .current_day_cli import config,protected_json,scoped_meal
from .preparation import fingerprint

def main():
    parser=argparse.ArgumentParser(description='夕食/日次のDiscord送付専用')
    parser.add_argument('command',choices=('activate','run','reconcile'));parser.add_argument('--config',required=True);a=parser.parse_args()
    if os.getenv('MINA_DISCORD_DELIVERY_RECOVERY_ENABLED','false').lower()!='true':raise ValueError('discord_delivery_recovery_disabled')
    envelope,_=protected_json(a.config,root_owned=True)
    adopted='original_config' in envelope
    settings,code=config(a.config,Path(__file__).resolve().parents[2],version=VERSION,extras=('checkpoint','own_author','original_config') if adopted else ('checkpoint','own_author'))
    approval=settings['approval']
    if adopted:
        original,original_sha=protected_json(settings['original_config']['path'],root_owned=True)
        if original_sha!=settings['original_config']['sha256']:raise ValueError('旧承認原本の版が不一致です')
        if {k:v for k,v in approval.items() if k!='code_commit'}!={k:v for k,v in original['approval'].items() if k!='code_commit'}:raise ValueError('新検証版で旧権限を変更できません')
        for key in ('database','directory','host','organization_id','channels','history','discord_channel','checkpoint','own_author'):
            if settings[key]!=original[key]:raise ValueError('新検証版で送付条件を変更できません')
        if a.command=='activate':raise ValueError('新検証版で旧権限を再発行しません')
        approval=original['approval']
    snapshot,sha=protected_json(settings['checkpoint']['path'],root_owned=True)
    if sha!=settings['checkpoint']['sha256'] or fingerprint(snapshot)!=settings['approval']['checkpoint_digest']:raise ValueError('送付原本の版が不一致です')
    def check():
        code()
        if adopted and protected_json(settings['original_config']['path'],root_owned=True)[1]!=original_sha:raise ValueError('旧承認原本が変更されました')
        if protected_json(settings['checkpoint']['path'],root_owned=True)[1]!=sha:raise ValueError('送付原本が変更されました')
    from .store import Store
    from .clients import require_live,DiscordClient
    from .m1_contract import verify_source
    from .domain import Candidate
    from .ogp import capture_ogp
    require_live();store=Store(settings['database']);reader=DiscordReader(os.environ['DISCORD_BOT_TOKEN'],settings['discord_channel'])
    if reader.identity()!=settings['own_author']:raise ValueError('Discord著者が承認と不一致です')
    gate=Delivery(store,approval,snapshot,lambda:datetime.now(UTC),check,settings['own_author'],settings['discord_channel'])
    if a.command=='activate':print(json.dumps({'deadline':gate.issue().isoformat(),'food_writes':0,'ai_calls':0}));return
    if a.command=='reconcile':print(json.dumps(gate.reconcile(reader)));return
    raw=scoped_meal()
    class ReadOnly:
        def day_state(self,*args):return raw.day_state(*args)
        def request_state(self,*args):return raw.request_state(*args)
    def verify(payload):
        candidate=Candidate(**payload['source']['candidate']);candidate.validate_content(settings['host'])
        verify_source(candidate,ReadOnly(),settings['host'],capture_ogp,clock=lambda:datetime.now(UTC))
    print(json.dumps(gate.run(DiscordClient(os.environ['DISCORD_BOT_TOKEN'],settings['discord_channel']),reader,verify)))

if __name__=='__main__':main()
