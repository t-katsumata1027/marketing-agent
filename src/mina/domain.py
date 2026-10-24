"""みなの固定投稿枠、成果物の版、承認前の品質条件。"""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ACCOUNT = "EatsLog88161"
SLOTS = {"breakfast": time(7, 30), "lunch": time(12, 15),
         "dinner": time(19, 15), "daily": time(21, 30)}
LABELS = {"breakfast": "朝食", "lunch": "昼食", "dinner": "夕食", "daily": "1日まとめ"}
FIXED_MARKERS = ("#AI食事解析", "#LogEats", "#食事記録", "@EatsLog88161")


def instant(value: datetime) -> datetime:
    """タイムゾーンのない時刻を拒否し、UTCへ変換する。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("タイムゾーン付き時刻が必要です")
    return value.astimezone(UTC)


def slot_time(target: date, slot: str) -> datetime:
    """JSTの固定投稿時刻をUTCで返す。"""
    return datetime.combine(target, SLOTS[slot], JST).astimezone(UTC)


def approval_time(target: date) -> datetime:
    """前日20時に翌日分の承認依頼を送る。"""
    return datetime.combine(target - timedelta(days=1), time(20), JST).astimezone(UTC)


@dataclass(frozen=True)
class Candidate:
    """API固定文と画像・共有の検査結果を含む承認対象。"""

    target_date: str
    slot: str
    comment: str
    api_text: str
    share_url: str
    menu: str
    nutrition: dict
    image_sha256: str
    ogp_sha256: str
    api_quality: str
    secondary_review: str
    ogp_verified: bool
    history_verified: bool
    legacy_schedule_clear: bool
    source_commit: str
    meal_count: int = 1
    account: str = ACCOUNT
    publish_text: str | None = None
    approval_source: dict | None = None

    @property
    def scheduled_at(self) -> datetime:
        if self.approval_source and self.approval_source.get('publish_after_approval_until'):
            return instant(datetime.fromisoformat(self.approval_source['publish_after_approval_until']))
        return slot_time(date.fromisoformat(self.target_date), self.slot)

    @property
    def source_content(self) -> str:
        """M1は完成本文をそのまま返す。旧候補の結合は公開保証の対象外。"""
        base = f"{self.comment}\n\n{self.api_text}"
        if self.publish_text is not None:
            return self.publish_text
        return base if self.share_url in self.api_text else f"{base}\n{self.share_url}"

    @property
    def content(self) -> str:
        """承認版へ固定した計測設定だけを公開本文へ反映する。"""
        from .tracking import render
        return render(self)

    @property
    def published_share_url(self) -> str:
        from .tracking import published_url
        return published_url(self)

    @property
    def digest(self) -> str:
        encoded = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def validate(self, allowed_host: str, now: datetime, recent_comments=(), weekly_comments=None) -> None:
        """独立レビュー済みの成果物だけを承認待ちへ進める。"""
        if self.account != ACCOUNT or self.slot not in SLOTS:
            raise ValueError("公開先または投稿枠が不正です")
        if self.scheduled_at <= instant(now):
            raise ValueError("予定時刻を過ぎた候補です")
        if self.approval_source and self.approval_source.get('publish_after_approval_until'):
            day = date.fromisoformat(self.target_date)
            following = {'breakfast': 'lunch', 'lunch': 'dinner', 'dinner': 'daily'}.get(self.slot)
            if (not following or now.astimezone(JST).date() != day or
                    not slot_time(day,self.slot) <= instant(now) < self.scheduled_at < slot_time(day,following)):
                raise ValueError('本人指定の当日食事だけを次枠より前に臨時公開できます')
        if not self.comment.strip() or len(self.comment) > 120 or any(c in self.comment for c in "\r\n"):
            raise ValueError("一言は改行なし120文字以内です")
        if self.publish_text is not None:
            expected = f'{self.comment}\n\n{self.api_text}\n{self.share_url}'
            if self.publish_text != expected:
                raise ValueError('サーバー完成本文と承認対象の固定文が一致しません')
        if isinstance(self.approval_source,dict) and self.approval_source.get('contract_version') in ('mina-replace-v1','mina-rescue-v1'):
            from .replacement_source import validate_binding
            validate_binding(self)
        elif self.approval_source is not None:
            from .m1_contract import version
            from .clients import MealClient
            if not isinstance(self.approval_source, dict) or self.publish_text is None:
                raise ValueError('M1承認元の証跡が不正です')
            try:
                if not isinstance(self.approval_source['request_key'], str):
                    raise ValueError()
                MealClient.request_key(self.approval_source['request_key'])
                version(self.approval_source['state_version'], 'sv1')
                version(self.approval_source['input_hash'], 'ih1')
                if 'result_version' in self.approval_source:
                    version(self.approval_source['result_version'], 'rv1')
                    version(self.approval_source['ogp_source_version'], 'sv1' if self.slot == 'daily' else 'rv1')
                    if self.approval_source['operation'] not in (('daily_share',) if self.slot == 'daily' else ('meal_register', 'meal_correction')):
                        raise ValueError()
                    if not isinstance(self.approval_source['ogp_revision'], str) or not 1 <= len(self.approval_source['ogp_revision']) <= 200:
                        raise ValueError()
                    if self.slot != 'daily' and not re.fullmatch(r'[0-9a-f]{64}', self.approval_source['stored_image_sha256']):
                        raise ValueError()
                if not re.fullmatch(r'[0-9a-f]{64}', self.approval_source['result_sha256']):
                    raise ValueError()
                if 'ogp_image_url' in self.approval_source:
                    image = urlsplit(self.approval_source['ogp_image_url'])
                    if image.scheme != 'https' or image.hostname != allowed_host or image.username or image.password or image.port not in (None, 443):
                        raise ValueError()
                    if not re.fullmatch(r'[0-9a-f]{64}', self.approval_source['ogp_image_sha256']):
                        raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise ValueError('M1承認元の識別子・版・ハッシュが不正です') from None
        if any(word in self.comment for word in ("食べた", "美味しかった", "作った", "お腹いっぱい", "必ず痩せ", "治療", "疾病予防")):
            raise ValueError("実食または健康効果の断定を含みます")
        if self.comment in recent_comments:
            raise ValueError("一言コメントが重複しています")
        if "気になります" in self.comment and any("気になります" in c for c in (recent_comments if weekly_comments is None else weekly_comments)):
            raise ValueError("気になりますの週次上限です")
        for marker in FIXED_MARKERS:
            if marker not in self.api_text:
                raise ValueError("API固定文に必須の表記がありません")
        urls = re.findall(r"https?://[^\s]+", self.content)
        if self.published_share_url not in urls:
            raise ValueError("共有URLが本文にありません")
        for value in urls:
            parsed = urlsplit(value)
            if parsed.scheme != "https" or parsed.hostname != allowed_host or parsed.username or parsed.password or parsed.port not in (None, 443):
                raise ValueError("正式なLogEats共有URLのみ使用できます")
        if not urlsplit(self.share_url).path.startswith("/s/"):
            raise ValueError("共有URLの形式が不正です")
        # Xの標準文字数はURLを23文字、ASCII外を保守的に2文字で見積もる。
        from .x_text import weighted_length,PostLengthError
        length=weighted_length(self.content)
        if length>280:
            raise PostLengthError(self.slot,length)
        if self.api_quality != "pass" or self.secondary_review != "publishable":
            raise ValueError("API品質または二次レビューが不合格です")
        if any(value is not True for value in (self.ogp_verified, self.history_verified, self.legacy_schedule_clear)):
            raise ValueError("OGP・履歴・既存予約の確認が必要です")
        if self.slot == "daily" and self.meal_count != 3:
            raise ValueError("日次まとめには確認済み3食が必要です")
        if not self.menu.strip() or not re.fullmatch(r"[0-9a-f]{40}", self.source_commit):
            raise ValueError("献立と実装commitの証跡が必要です")
        for value in (self.image_sha256, self.ogp_sha256):
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("画像・OGPのハッシュが必要です")
        if set(self.nutrition) != {"calories", "protein", "fat", "carbs"}:
            raise ValueError("カロリーとPFCが必要です")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in self.nutrition.values()):
            raise ValueError("栄養値が不正です")
        n = self.nutrition
        converted = 4 * n["protein"] + 9 * n["fat"] + 4 * n["carbs"]
        difference = abs(converted - n["calories"])
        if difference > 50 and difference > max(n["calories"], 1) * .2:
            raise ValueError("カロリーとPFCが一致しません")
