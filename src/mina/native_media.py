"""共有OGPの判定を変えず、別点検した添付画像を版と本文へ固定する。"""
import hashlib,io,json,re
from datetime import UTC,datetime
from pathlib import Path
from urllib.parse import urlsplit,parse_qsl
from urllib.request import Request,build_opener
from .preparation import fingerprint
from .instagram_media import HOSTS,verify_public

KEY='native_media'
VERSION='mina-agent-native-media-v1'
TARGET='2026-10-30'
REASONS={'share_ogp_mismatch','ogp_read_failed','ogp_delivery_unstable'}

def selected(candidate):return isinstance(candidate.approval_source,dict) and KEY in candidate.approval_source

def validate(candidate):
    """OGP合格の代用フラグではなく、画像原本・新独立点検の契約を検査する。"""
    from .instagram_domain import media_url
    try:
        source=candidate.approval_source;value=source[KEY]
        if set(value)!={'contract_version','asset','review','source_images','state_version','result_version','request_key','input_hash','result_sha256','body_sha256','legacy_ogp'}:raise ValueError()
        if candidate.target_date!=TARGET or value['contract_version']!=VERSION or candidate.ogp_verified is not False:raise ValueError()
        for key in ('state_version','result_version','request_key','input_hash','result_sha256'):
            if value[key]!=source[key]:raise ValueError()
        if value['body_sha256']!=hashlib.sha256(candidate.source_content.encode()).hexdigest():raise ValueError()
        images=value['source_images'];review=value['review'];asset=value['asset'];legacy=value['legacy_ogp']
        if set(images)!={'breakfast','lunch','dinner'} or any(not re.fullmatch('[a-f0-9]{64}',s) for s in images.values()):raise ValueError()
        if candidate.image_sha256!=(fingerprint(images) if candidate.slot=='daily' else images[candidate.slot]):raise ValueError()
        if set(review)!={'status','context_digest','image_sha256','source_images','receipt_sha256'} or review['status']!='publishable' or review['image_sha256']!=asset['sha256'] or review['source_images']!=images:raise ValueError()
        for key in ('context_digest','receipt_sha256'):
            if not re.fullmatch('[a-f0-9]{64}',review[key]):raise ValueError()
        if set(asset)!={'path','sha256','role','width','height','mime_type','size','url'} or asset['role']!='x_native_'+candidate.slot or asset['mime_type']!='image/jpeg' or (asset['width'],asset['height'])!=(1200,630) or type(asset['size']) is not int or not 0<asset['size']<=4194304 or not re.fullmatch('[a-f0-9]{64}',asset['sha256']):raise ValueError()
        media_url(asset['url'],HOSTS)
        if set(legacy)!={'state','reason_codes','revision','image_sha256'} or legacy['state'] not in ('hold','unverified') or not legacy['reason_codes'] or not set(legacy['reason_codes'])<=REASONS or legacy['revision']!=source['ogp_revision'] or legacy['image_sha256']!=source['ogp_image_sha256'] or not re.fullmatch('[a-f0-9]{64}',legacy['image_sha256']):raise ValueError()
        return value
    except (TypeError,KeyError,AttributeError,ValueError):raise ValueError('添付画像の独立契約・原本・本文版が不一致です') from None

def assets(candidate):return [{'image':{'url':validate(candidate)['asset']['url']}}] if selected(candidate) else []

def check_post(candidate,post):
    """Buffer応答で画像の欠落や差替えを許可しない。代理URLを同じ原本と仮定しない。"""
    value=validate(candidate);actual=post.get('assets')
    if not isinstance(actual,list) or len(actual)!=1 or actual[0].get('source')!=value['asset']['url'] or actual[0].get('mimeType')!='image/jpeg':raise ValueError('Buffer添付画像の数・順序・元URL・MIMEが不一致です')
    return True

def verify_bytes(candidate):
    value=validate(candidate);asset=value['asset'];p=Path(asset['path'])
    if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=asset['sha256']:raise ValueError('添付画像の原本が変わりました')
    verify_public(asset)
    return asset

def link_snapshot(url,host):
    """画像GETとは分けて、正式な共有HTMLと版を読む。画像不良を合格にしない。"""
    from .ogp import SameHostRedirect,MetaParser
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname!=host or parsed.username or parsed.password or parsed.port not in (None,443) or parsed.fragment:raise ValueError('共有HTMLの接続先が不正です')
    with build_opener(SameHostRedirect(host)).open(Request(url,headers={'User-Agent':'Twitterbot/1.0 LogEats-Mina-Review/1.0'}),timeout=15) as response:
        if 'text/html' not in response.headers.get('Content-Type',''):raise ValueError('共有先がHTMLではありません')
        raw=response.read(1048577);final=response.geturl()
    if len(raw)>1048576:raise ValueError('共有HTMLが過大です')
    parser=MetaParser();parser.feed(raw.decode())
    if any(not parser.meta.get(k) for k in ('og:title','og:description','og:image')):raise ValueError('共有HTMLの識別情報が不足しています')
    return {'meta':parser.meta,'page_url':final,'html_sha256':hashlib.sha256(raw).hexdigest()}

def verify_link(candidate,host,result):
    """計測URLの転送先とOGPの版文字列だけを検査。画素合格とは区別する。"""
    snapshots=[link_snapshot(candidate.share_url,host)]
    if candidate.published_share_url!=candidate.share_url:snapshots.append(link_snapshot(candidate.published_share_url,host))
    expected=result['ogp'];short=urlsplit(candidate.share_url);api=urlsplit(expected['image_url'])
    for snapshot in snapshots:
        image=urlsplit(snapshot['meta']['og:image']);page=urlsplit(snapshot['page_url'])
        for u in (image,page):
            if u.scheme!='https' or u.hostname!=host or u.username or u.password or u.port not in (None,443) or u.fragment:raise ValueError('共有HTMLが許可外へ転送されました')
        legitimate=(image.path==api.path or re.fullmatch(r'/share/(?:daily/)?[0-9a-fA-F-]{36}/og-image',image.path))
        if (not legitimate
            or parse_qsl(image.query)!=[('v',expected['revision'])] or parse_qsl(api.query)!=[('v',expected['revision'])]
            or page.path not in (short.path,image.path.removesuffix('/og-image'))):raise ValueError('共有HTMLの対象・版が不一致です')
        allowed=parse_qsl(urlsplit(candidate.published_share_url).query)
        if parse_qsl(page.query) not in ([],allowed):raise ValueError('共有HTMLの計測条件が変わりました')
    if any(s['meta']!=snapshots[0]['meta'] for s in snapshots[1:]):raise ValueError('計測URLの共有HTMLが正本と一致しません')
    return {'delivery':'native_image','legacy_ogp_state':validate(candidate)['legacy_ogp']['state'],'link_html_sha256':snapshots[0]['html_sha256']}

def decorate_card(card,candidate):
    value=validate(candidate);card['embeds'][0]['image']={'url':value['asset']['url']}
    card['embeds'][0]['fields']=[v for v in card['embeds'][0].get('fields',[]) if v.get('name')!='確認']
    card['embeds'][0]['fields'].append({'name':'投稿画像','value':'添付画像の独立点検：合格。画像を直接添付します。共有OGPの不具合は別途対応中です。'})
