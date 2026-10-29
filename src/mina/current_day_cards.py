"""時刻未定の準備カード。通常Candidate/Instagram候補へ登録しない。"""
from dataclasses import asdict
import hashlib,json,math
from pathlib import Path
from .current_day_origin import TARGET,VERSION
from .domain import Candidate,SLOTS,LABELS,slot_time,date
from .preparation import fingerprint,PreparationJournal
from .scoped_preparation import KEY


def marker(gate):
    return {'contract_version':VERSION,'target_date':TARGET,'recovery_uuid':gate.approval['recovery_uuid'],
        'scheduled_at':None,'approval_digest':fingerprint(gate.approval)}


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS current_day_cards (
        recovery_uuid TEXT NOT NULL,platform TEXT NOT NULL,slot TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL,
        state TEXT NOT NULL,message_id TEXT,PRIMARY KEY(recovery_uuid,platform,slot))''')


def assets_valid(assets,base):
    from PIL import Image
    for asset in assets:
        p=Path(asset['path'])
        if p.is_symlink() or not p.resolve().is_relative_to(base.resolve()):raise ValueError('準備画像の参照先が不正です')
        raw=p.read_bytes()
        if not 0<len(raw)<=4*1024*1024 or hashlib.sha256(raw).hexdigest()!=asset['sha256']:raise ValueError('準備画像の実体が変わりました')
        with Image.open(p) as image:
            if image.format!='JPEG' or image.size!=(asset['width'],asset['height']):raise ValueError('準備画像の形式が変わりました')


def make(gate,reviewed,saved,evidence,commit,host,meal,capture,output):
    from .readiness import check_evidence
    from .m1_contract import verify_source,validate_state
    from .instagram_domain import nutrition
    from .instagram_runtime import caption
    from .instagram_render import render_bundle
    from PIL import Image
    gate.check(sending=True);entries=check_evidence(evidence,TARGET,gate.clock())
    if reviewed.get('target_date')!=TARGET or reviewed.get('state')!='posts_reviewed' or set(reviewed.get('posts',{}))!=set(SLOTS):raise ValueError('同じ日の点検済み4案が必要です')
    comments=[e['comment'] for e in entries];menus={e['menu'] for e in entries}
    candidates={};bundle={'target_date':TARGET,'meals':{},'x_candidates':{},'x_digests':{}}
    for slot in SLOTS:
        post=reviewed['posts'][slot]
        if slot!='daily' and post['menu'] in menus:raise ValueError('過去献立との重複です')
        fields=('comment','api_text','share_url','menu','nutrition','image_sha256','ogp_sha256','api_quality','secondary_review','ogp_verified','publish_text')
        source={**post['approval_source'],KEY:marker(gate),'readiness_sha256':fingerprint(evidence)}
        from .tracking import configured_policy
        tracking=configured_policy()
        if tracking is not None:source['url_tracking']=tracking
        c=Candidate(TARGET,slot,**{k:post[k] for k in fields},approval_source=source,source_commit=commit,
            history_verified=True,legacy_schedule_clear=True,meal_count=3 if slot=='daily' else 1)
        c.validate_content(host,comments);comments.append(c.comment)
        # validate()の未来時刻条件を変えず、内容とM1/OGPだけを専用カードで検証する。
        verify_source(c,meal,host,capture,clock=gate.clock)
        candidates[slot]=c;bundle['x_candidates'][slot]=asdict(c);bundle['x_digests'][slot]=c.digest
        if slot!='daily':
            info=saved['images'][slot];p=Path(info['path'])
            if (info['sha256']!=c.image_sha256 or p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=c.image_sha256
                    or info['drive'].get('verified') is not True or info['drive']['sha256']!=c.image_sha256
                    or info['image_review']!={'status':'image_acceptable','reason_codes':[]}):raise ValueError('保存画像・点検・現在正本が不一致です')
            bundle['meals'][slot]={'menu':c.menu,'comment':c.comment,'nutrition':nutrition(c.nutrition),'image_path':str(p),'image_sha256':c.image_sha256}
        else:bundle['total']=nutrition(c.nutrition)
    state=meal.day_state(TARGET);sv=validate_state(state,TARGET,gate.clock())
    if state['meal_count']!=3 or any(c.approval_source['state_version']!=sv for c in candidates.values()):raise ValueError('点検中に当日正本の版が変わりました')
    for key,field in (('calories','totalCalories'),('protein','totalProtein'),('fat','totalFat'),('carbs','totalCarbs')):
        if abs(sum(m['nutrition'][key] for m in bundle['meals'].values())-bundle['total'][key])>1e-6 or abs(state['summary'][field]-bundle['total'][key])>1e-6:raise ValueError('日次と3食の実解析が一致しません')
    targets=state.get('nutrition_targets')
    if not isinstance(targets,dict) or set(targets)!={'calories','protein','fat','carbs'} or any(v is not None and (type(v) not in (int,float) or not math.isfinite(v) or v<=0) for v in targets.values()):raise ValueError('栄養目標の取得が不完全です')
    bundle.update(state_version=sv,targets=targets);bundle['bundle_sha256']=fingerprint(bundle)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    journal=PreparationJournal(gate.store)
    rendered=journal.run(TARGET,'current_day_render',{'bundle_digest':bundle['bundle_sha256']},lambda:render_bundle(bundle,output))
    payloads=[]
    for slot,c in candidates.items():
        # Xカードにも独立点検済みの実OGPを添付し、取得URLだけで合格扱いにしない。
        snapshot=capture(c.share_url,host);raw=snapshot['review_image']
        if snapshot['digest']!=c.ogp_sha256:raise ValueError('Xカード画像の版が変わりました')
        path=output/(slot+'-x.jpg')
        if path.exists() and path.read_bytes()!=raw:raise ValueError('保存済みXカード画像が変わっています')
        if not path.exists():path.write_bytes(raw)
        with Image.open(path) as im:width,height=im.size
        xs=[{'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'width':width,'height':height}]
        for platform,body,assets in (('x',c.content,xs),('instagram',caption(bundle,slot),rendered['assets'][slot])):
            assets_valid(assets,output)
            payload={'contract_version':VERSION,'target_date':TARGET,'slot':slot,'platform':platform,'scheduled_at':None,
                'original_scheduled_at':slot_time(date.fromisoformat(TARGET),slot).isoformat(),'scheduling_state':'unapproved',
                'body':body,'assets':assets,'source':{KEY:marker(gate),'state_version':sv,'result_sha256':c.approval_source['result_sha256'],
                    'bundle_sha256':bundle['bundle_sha256'],'nutrition_targets':targets,'candidate':asdict(c)}}
            payloads.append(payload)
    with gate.store.transaction() as db:
        gate.check(db,sending=True);initialize(db)
        for p in payloads:
            db.execute("INSERT INTO current_day_cards VALUES(?,?,?,?,?,'prepared',NULL)",
                (gate.approval['recovery_uuid'],p['platform'],p['slot'],json.dumps(p,ensure_ascii=False),fingerprint(p)))
    return payloads


def deliver(gate,discord,base,verify):
    """通常通知daemonはこの表を読まない。曖昧な送付は同一カードを再送しない。"""
    with gate.store.transaction() as db:
        rows=list(db.execute('SELECT * FROM current_day_cards WHERE recovery_uuid=? ORDER BY platform,slot',(gate.approval['recovery_uuid'],)))
    if len(rows)!=8 or {(r['platform'],r['slot']) for r in rows}!={(p,s) for p in ('x','instagram') for s in SLOTS}:raise ValueError('点検済み8カードが揃っていません')
    selected=getattr(gate,'delivery_slots',tuple(SLOTS))
    if not selected or not set(selected)<=set(SLOTS):raise ValueError('送付対象枠が不正です')
    for row in rows:
        if row['slot'] not in selected:continue
        payload=json.loads(row['payload'])
        if (fingerprint(payload)!=row['digest'] or payload.get('scheduled_at') is not None or payload['source'].get(KEY)!=marker(gate)
                or (payload.get('contract_version'),payload.get('target_date'),payload.get('platform'),payload.get('slot'))!=(VERSION,TARGET,row['platform'],row['slot'])):raise ValueError('準備カードの版が不正です')
        if row['state']=='sent':continue
        if row['state']!='prepared':raise ValueError('Discord送付の結果照合が必要です')
        gate.check(sending=True);assets_valid(payload['assets'],Path(base));verify(payload)
        files=[Path(a['path']) for a in payload['assets']]
        from datetime import datetime
        from .domain import JST
        original_time=datetime.fromisoformat(payload['original_scheduled_at']).astimezone(JST).strftime('%m/%d %H:%M JST')
        content=f"【準備専用・公開停止】{TARGET} {LABELS[row['slot']]} / {row['platform']}\n公開時刻：未定。元の予定は{original_time}。遅延公開は未承認です。\n{payload['body']}"
        message={'content':content,'allowed_mentions':{'parse':[]},'embeds':[{'image':{'url':'attachment://'+p.name}} for p in files]}
        with gate.store.transaction() as db:
            gate.check(db,sending=True)
            if db.execute("UPDATE current_day_cards SET state='sending' WHERE recovery_uuid=? AND platform=? AND slot=? AND state='prepared' AND digest=? AND payload=?",(gate.approval['recovery_uuid'],row['platform'],row['slot'],row['digest'],row['payload'])).rowcount!=1:raise ValueError('送付claimが変わりました')
        try:
            from .external_guard import guard
            token=guard.set(lambda:gate.check(sending=True))
            try:result=discord.send_files(message,files)
            finally:guard.reset(token)
            if not isinstance(result,dict) or not isinstance(result.get('id'),str) or not result['id'].isdigit():raise ValueError('Discord受付IDが不明です')
        except BaseException as error:
            from .clients import DiscordAttachmentRejected
            state='rejected' if isinstance(error,DiscordAttachmentRejected) else 'unknown'
            with gate.store.transaction() as db:
                db.execute('CREATE TABLE IF NOT EXISTS current_day_card_failures(id INTEGER PRIMARY KEY,recovery_uuid TEXT NOT NULL,platform TEXT NOT NULL,slot TEXT NOT NULL,digest TEXT NOT NULL,state TEXT NOT NULL,http_status INTEGER,discord_code INTEGER,response_sha256 TEXT)')
                db.execute('INSERT INTO current_day_card_failures(recovery_uuid,platform,slot,digest,state,http_status,discord_code,response_sha256) VALUES(?,?,?,?,?,?,?,?)',(gate.approval['recovery_uuid'],row['platform'],row['slot'],row['digest'],state,getattr(error,'http_status',None),getattr(error,'discord_code',None),getattr(error,'response_sha256',None)))
                db.execute('UPDATE current_day_cards SET state=? WHERE recovery_uuid=? AND platform=? AND slot=?',(state,gate.approval['recovery_uuid'],row['platform'],row['slot']))
            raise
        with gate.store.transaction() as db:db.execute("UPDATE current_day_cards SET state='sent',message_id=? WHERE recovery_uuid=? AND platform=? AND slot=?",(result['id'],gate.approval['recovery_uuid'],row['platform'],row['slot']))
