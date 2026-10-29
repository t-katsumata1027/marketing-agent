"""既存の三写真と確定値からX用JPEGを決定論的に合成する。AIは呼ばない。"""
import hashlib,io
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont,ImageOps
from .instagram_render import ASSETS,BG,INK,CARD,value,grams,LABELS

def render(bundle,slot,path):
    image=Image.new('RGB',(1200,630),BG);draw=ImageDraw.Draw(image)
    def text(s,x,y,size=30,bold=False,center=False):
        font=ImageFont.truetype(str(ASSETS/('MPLUSRounded1c-Bold.ttf' if bold else 'MPLUSRounded1c-Medium.ttf')),size)
        if center:x-=draw.textlength(s,font=font)/2
        box=draw.textbbox((x,y),s,font=font)
        if box[0]<0 or box[2]>1200 or box[3]>630:raise ValueError('投稿画像の文字が画像枠を超えています')
        draw.text((x,y),s,font=font,fill=INK)
    def photo(meal,rectangle):
        p=Path(meal['image_path']);raw=p.read_bytes()
        if p.is_symlink() or hashlib.sha256(raw).hexdigest()!=meal['image_sha256']:raise ValueError('投稿画像の食事原本が変わりました')
        with Image.open(io.BytesIO(raw)) as source:source=ImageOps.exif_transpose(source).convert('RGB');source=ImageOps.contain(source,rectangle[2:],Image.Resampling.LANCZOS)
        x,y,w,h=rectangle;image.paste(source,(x+(w-source.width)//2,y+(h-source.height)//2))
    if slot=='daily':
        text('みなの食事記録｜1日のまとめ',30,8,36,True);text(bundle['target_date'],1000,18,22)
        for i,s in enumerate(('breakfast','lunch','dinner')):
            x=30+390*i;meal=bundle['meals'][s];text(LABELS[s],x+180,65,30,True,True);photo(meal,(x,110,360,310));text(value(meal['nutrition']['calories'])+' kcal',x+180,430,35,True,True)
        draw.rounded_rectangle((30,495,1170,610),18,fill=CARD);n=bundle['total'];text('合計 '+value(n['calories'])+' kcal',55,525,43,True)
        for i,k in enumerate(('protein','fat','carbs')):text(('P','F','C')[i]+' '+grams(n[k])+'g',640+i*175,540,27,True)
    else:
        meal=bundle['meals'][slot];n=meal['nutrition'];photo(meal,(30,40,560,550));text('みなの食事記録｜'+LABELS[slot],625,22,34,True);text(bundle['target_date'],625,70,23)
        # 料理名を文字幅で折返し、長い料理名でも原文を欠落させない。
        font=ImageFont.truetype(str(ASSETS/'MPLUSRounded1c-Medium.ttf'),26);lines=['']
        for c in meal['menu']:
            if draw.textlength(lines[-1]+c,font=font)>535:lines.append('')
            lines[-1]+=c
        if len(lines)>4:raise ValueError('投稿画像の料理名が過大です')
        for i,line in enumerate(lines):text(line,625,125+36*i,26)
        text(value(n['calories'])+' kcal',625,300,64,True)
        for i,(k,label) in enumerate((('protein','P たんぱく質'),('fat','F 脂質'),('carbs','C 炭水化物'))):text(label+'  '+grams(n[k])+' g',625,395+46*i,29)
        text('Log-Eats',625,563,32,True)
    output=io.BytesIO();image.save(output,'JPEG',quality=94,optimize=True,subsampling=0);raw=output.getvalue();path=Path(path)
    if path.exists() and path.read_bytes()!=raw:raise ValueError('既存の投稿画像を上書きしません')
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    return {'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'role':'x_native_'+slot,'width':1200,'height':630,'mime_type':'image/jpeg','size':len(raw)}
