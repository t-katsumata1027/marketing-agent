"""既存の料理写真と正本の数値を固定位置へ合成する。生成APIは使用しない。"""

from decimal import Decimal, ROUND_HALF_UP
import hashlib
import io
import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .instagram_domain import LAYOUT_VERSION, digest

ASSETS = Path(__file__).resolve().parents[2] / 'assets' / 'instagram'
BG, INK, SAGE, CARD = '#FBF8F1', '#253B2E', '#729978', '#EEF0E5'
LABELS = {'breakfast': '朝ごはん', 'lunch': '昼ごはん', 'dinner': '夜ごはん'}


def value(number):
    return str(Decimal(str(number)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def grams(number):
    return str(Decimal(str(number)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)).rstrip('0').rstrip('.')


class Canvas:
    def __init__(self, height):
        self.image = Image.new('RGB', (1080, height), BG)
        self.draw = ImageDraw.Draw(self.image)

    def font(self, size, bold=False):
        return ImageFont.truetype(str(ASSETS / ('MPLUSRounded1c-Bold.ttf' if bold else 'MPLUSRounded1c-Medium.ttf')), size)

    def text(self, text, x, y, size=40, bold=False, centered=False, width=None):
        font = self.font(size, bold)
        if width is not None and self.draw.textlength(text, font=font) > width:
            raise ValueError('文字がテンプレートの幅を超えています')
        if centered:
            x -= self.draw.textlength(text, font=font) / 2
        self.draw.text((x, y), text, font=font, fill=INK, stroke_width=0)

    def lines(self, text, x, y, width, size, max_lines=2, centered=False):
        """文字を切り捨てず、入り切らない場合は下書きに戻す。"""
        result, line = [], ''
        # 読点で区切れる献立名は料理単位で改行し、食品名の途中切れを避ける。
        tokens = re.findall(r'[^、・\n]+[、・]?|\n', text)
        for token in tokens:
            if token == '\n':
                if line:
                    result.append(line)
                    line = ''
                continue
            if self.draw.textlength(token, font=self.font(size)) > width:
                if line:
                    result.append(line)
                    line = ''
                for char in token:
                    if self.draw.textlength(line + char, font=self.font(size)) > width:
                        result.append(line)
                        line = ''
                    line += char
            elif line and self.draw.textlength(line + token, font=self.font(size)) > width:
                result.append(line)
                line = token
            else:
                line += token
        if line:
            result.append(line)
        if len(result) > max_lines:
            raise ValueError('献立が長いため意味を保った短い表示名が必要です')
        for index, line in enumerate(result):
            self.text(line, x, y + index * (size + 12), size, centered=centered)

    def box(self, rectangle, color=CARD, radius=28):
        self.draw.rounded_rectangle(rectangle, radius=radius, fill=color)

    def photo(self, meal, rectangle):
        path = Path(meal['image_path'])
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != meal['image_sha256']:
            raise ValueError('合成前に料理画像が変わっています')
        with Image.open(io.BytesIO(raw)) as original:
            photo = ImageOps.exif_transpose(original).convert('RGB')
        x, y, w, h = rectangle
        # 全周を残すため中央切抜きせず、写真全体を枠内へ収める。
        photo = ImageOps.contain(photo, (w, h), Image.Resampling.LANCZOS)
        self.image.paste(photo, (x + (w - photo.width) // 2, y + (h - photo.height) // 2))

    def brand(self, y):
        with Image.open(ASSETS / 'logeats-icon.png') as icon:
            icon = icon.convert('RGBA').resize((68, 68), Image.Resampling.LANCZOS)
        self.image.paste(icon, (350, y), icon)
        self.text('Log-Eats', 436, y + 2, 53, True)

    def macros(self, n, y, height=132):
        for i, (key, label, mark) in enumerate((('protein', 'たんぱく質', 'P'), ('fat', '脂質', 'F'), ('carbs', '炭水化物', 'C'))):
            x = 60 + i * 326
            self.box((x, y, x + 308, y + height))
            self.text(f'{mark} {grams(n[key])}g', x + 154, y + 15, 47, True, True)
            self.text(label, x + 154, y + 84, 27, centered=True)

    def save(self, path, role):
        raw = io.BytesIO()
        self.image.save(raw, 'JPEG', quality=94, optimize=True, subsampling=0)
        content = raw.getvalue()
        if path.exists() and path.read_bytes() != content:
            raise ValueError('既存の完成画像を上書きできません')
        path.write_bytes(content)
        return {'path': str(path), 'sha256': hashlib.sha256(content).hexdigest(), 'role': role,
                'width': 1080, 'height': self.image.height, 'mime_type': 'image/jpeg', 'size': len(content)}


def meal_image(bundle, slot, path, story=True):
    c = Canvas(1920 if story else 1350)
    meal = bundle['meals'][slot]
    n = meal['nutrition']
    c.text('みなの食事記録', 540, 80 if story else 35, 38 if story else 31, True, True)
    c.text(LABELS[slot], 540, 150 if story else 93, 116 if story else 84, True, True)
    c.photo(meal, (40, 340 if story else 245, 1000, 890 if story else 570))
    c.lines(meal['menu'], 540, 1260 if story else 840, 960, 34 if story else 31, centered=True)
    cal_y = 1405 if story else 965
    c.box((60, cal_y, 1020, cal_y + 116))
    c.text(value(n['calories']) + ' kcal', 540, cal_y + 6, 78, True, True)
    c.macros(n, 1545 if story else 1100, 132)
    c.brand(1748 if story else 1253)
    return c.save(path, slot + ('_story' if story else '_feed'))


def daily_cover(bundle, path):
    c = Canvas(1350)
    c.text('みなの食事記録', 540, 35, 32, True, True)
    c.text('今日の3食', 540, 93, 88, True, True)
    for i, slot in enumerate(LABELS):
        y = 230 + i * 260
        meal = bundle['meals'][slot]
        n = meal['nutrition']
        c.box((45, y, 1035, y + 240), '#F2F1E8')
        c.photo(meal, (55, y + 10, 340, 220))
        c.text(LABELS[slot], 425, y + 14, 36, True)
        c.lines(meal['menu'], 425, y + 68, 575, 26, max_lines=3)
        c.text(f"{value(n['calories'])} kcal", 425, y + 166, 41, True)
        c.text(f"P {grams(n['protein'])}g  F {grams(n['fat'])}g  C {grams(n['carbs'])}g", 425, y + 207, 24)
    c.text('1日合計  ' + value(bundle['total']['calories']) + ' kcal', 540, 1040, 61, True, True)
    c.text('目標達成率は最後のページへ', 540, 1140, 32, centered=True)
    c.brand(1245)
    return c.save(path, 'daily_cover')


def daily_goals(bundle, path):
    c = Canvas(1350)
    c.text('みなの食事記録', 540, 35, 32, True, True)
    c.text('1日の振り返り', 540, 105, 75, True, True)
    c.box((60, 260, 1020, 410))
    c.text('1日合計', 540, 275, 34, True, True)
    c.text(value(bundle['total']['calories']) + ' kcal', 540, 325, 66, True, True)
    c.macros(bundle['total'], 435)
    c.text('目標達成率', 65, 620, 46, True)
    for i, (key, label, unit) in enumerate((('calories', 'カロリー', 'kcal'), ('protein', 'P たんぱく質', 'g'),
                                           ('fat', 'F 脂質', 'g'), ('carbs', 'C 炭水化物', 'g'))):
        y = 710 + i * 116
        total, target = bundle['total'][key], bundle['targets'][key]
        c.text(label, 65, y, 30, True)
        if target is None:
            c.text('目標未設定', 745, y, 30, width=270)
            continue
        rate = Decimal(str(total)) / Decimal(str(target)) * 100
        shown = value if key == 'calories' else grams
        c.text(f'{shown(total)} / {shown(target)} {unit}', 345, y, 29)
        c.text(value(rate) + '%', 905, y, 35, True)
        c.box((65, y + 56, 1015, y + 76), '#E1E6DA', 10)
        length = round(950 * min(1, float(rate / 100)))
        if length:
            c.box((65, y + 56, 65 + length, y + 76), SAGE, 10)
    c.brand(1245)
    return c.save(path, 'daily_goals')


def render_bundle(bundle, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    files = {slot: [meal_image(bundle, slot, directory / (slot + '-story.jpg'))] for slot in LABELS}
    files['daily'] = [daily_cover(bundle, directory / 'daily-01.jpg')]
    for i, slot in enumerate(LABELS, 2):
        files['daily'].append(meal_image(bundle, slot, directory / f'daily-{i:02}.jpg', False))
    files['daily'].append(daily_goals(bundle, directory / 'daily-05.jpg'))
    manifest = {'layout_version': LAYOUT_VERSION, 'bundle_sha256': bundle['bundle_sha256'], 'assets': files,
                'font_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in ASSETS.glob('*.ttf')}}
    target = directory / 'rendered.json'
    if target.exists() and json.loads(target.read_text(encoding='utf-8')) != manifest:
        raise ValueError('別の正本・フォントで既存画像を上書きできません')
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest
