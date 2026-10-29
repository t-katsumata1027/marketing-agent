"""Discordの表示を外部画像取得へ依存させず、実添付を同IDで検証する。"""
import hashlib,io,copy,re,json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request,build_opener
from PIL import Image

def canonical_upload(message,asset):
    """embedへ取り込まれ添付一覧から消えたuploadを、検査用の明示viewへ写像する。"""
    if message.get('attachments') or len(message.get('embeds',[]))!=1:return message
    image=message['embeds'][0].get('image',{});u=urlsplit(image.get('url',''));name=Path(asset['path']).name;channel=message.get('channel_id','')
    if (not channel.isdigit() or u.scheme!='https' or u.hostname not in ('cdn.discordapp.com','media.discordapp.net') or u.username or u.password or u.fragment or u.port not in (None,443)
        or not re.fullmatch('/attachments/'+re.escape(channel)+'/[0-9]+/'+re.escape(name),u.path)):return message
    result=copy.deepcopy(message);result['attachments']=[{'filename':name,'url':image['url'],'size':asset['size'],'content_type':image.get('content_type','image/jpeg')}]
    result['_mina_attachment_representation']='embedded_upload'
    return result

def normalized_image(url,attachments):
    """Discordが置換した添付URLを、固定ファイル名へ正規化する。"""
    names=[a.get('filename') for a in attachments]
    if len(names)!=len(set(names)):raise ValueError('Discord添付名が重複しています')
    for a in attachments:
        if url in (a.get('url'),a.get('proxy_url')) and url is not None:return 'attachment://'+a['filename']
    return url

def validate_actual(message,asset,opener=None):
    """外部URL登録だけではなく、添付CDNの全文・JPEG・表示寸法を検査する。"""
    from .drive import NoRedirect
    attachments=message.get('attachments',[]);name=Path(asset['path']).name
    if len(attachments)!=1:raise ValueError('Discord実添付が一枚ではありません')
    a=attachments[0];u=urlsplit(a.get('url',''))
    if (a.get('filename')!=name or a.get('size')!=asset['size'] or a.get('content_type')!='image/jpeg'
        or u.scheme!='https' or u.hostname not in ('cdn.discordapp.com','media.discordapp.net') or u.username or u.password or u.port not in (None,443)):raise ValueError('Discord添付metadataが不一致です')
    images=[e.get('image',{}) for e in message.get('embeds',[])]
    if len(images)!=1 or normalized_image(images[0].get('url'),attachments)!='attachment://'+name or (images[0].get('width'),images[0].get('height'))!=(asset['width'],asset['height']):raise ValueError('Discord上の画像表示が未完成です')
    with (opener or build_opener(NoRedirect())).open(Request(a['url'],headers={'User-Agent':'Mina-Discord-Attachment-Review/1.0'}),timeout=20) as response:raw=response.read(4194305)
    if len(raw)!=asset['size'] or hashlib.sha256(raw).hexdigest()!=asset['sha256']:raise ValueError('Discord添付の実バイトが不一致です')
    with Image.open(io.BytesIO(raw)) as image:
        if image.format!='JPEG' or image.size!=(asset['width'],asset['height']):raise ValueError('Discord添付の画像形式が不一致です')
    return True

class AttachedDiscord:
    """限定一件のwireとJPEGだけをmultipartで送る。通常GETの検査も省略しない。"""
    def __init__(self,client,asset):self.client,self.asset=client,asset;self.channel=client.channel
    def identity(self):return self.client.identity()
    def history_since(self,started):return self.client.history_since(started)
    def send(self,wire):
        p=Path(self.asset['path']);expected=[{'id':0,'filename':p.name}]
        if wire.get('enforce_nonce') is not True or not isinstance(wire.get('nonce'),str) or not 1<=len(wire['nonce'])<=25:raise ValueError('台帳に固定した送信nonceが必要です')
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=self.asset['sha256'] or wire.get('attachments')!=expected or wire['embeds']!=[{'image':{'url':'attachment://'+p.name}}]:raise ValueError('直接添付の送信原本が不一致です')
        return canonical_upload(self.client.send_files(wire,[str(p)]),self.asset)
    def message(self,key):
        from .preparation import fingerprint
        raw=self.client.message(key)
        if len(json.dumps(raw,ensure_ascii=False).encode())>131072:raise ValueError('Discord画像点検の原本が過大です')
        value=canonical_upload(raw,self.asset);validate_actual(value,self.asset)
        image=raw.get('embeds',[{}])[0].get('image',{})
        value['_mina_attachment_proof']={'representation':value.get('_mina_attachment_representation','attachment'),'raw_attachments_count':len(raw.get('attachments',[])),'raw_get_sha256':fingerprint(raw),'raw_message':copy.deepcopy(raw),'api_image_content_type':image.get('content_type'),'validated_mime_type':'image/jpeg','image_sha256':self.asset['sha256'],'bytes':self.asset['size'],'width':self.asset['width'],'height':self.asset['height']}
        return value
