"""X用の完成成果物からInstagram用の画像・承認候補を作る。"""

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
import json
import os
from pathlib import Path
import time

from .clients import DiscordClient, MealClient, await_checked_at
from .domain import JST, SLOTS, slot_time
from .drive import DriveClient
from .instagram_buffer_client import InstagramBufferClient
from .instagram_domain import LAYOUT_VERSION, InstagramCandidate, digest
from .instagram_media import HOSTS, host_asset
from .instagram_publisher import draft_once, instagram_channel, publish_once
from .instagram_render import grams, render_bundle, value
from .instagram_source import load_bundle, verify_bundle
from .instagram_store import InstagramStore


def meal_client():
    return MealClient(os.getenv('MINA_MEAL_API_BASE_URL', ''), os.getenv('MINA_AGENT_API_TOKEN', ''), max_clock_ahead=5)


def save_json(path, data):
    """不変な成果物を追記し、違う内容を上書きしない。"""
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2).encode()
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('別の版の成果物が保存済みです')
        return
    with path.open('xb') as stream:
        stream.write(raw)


def caption(bundle, slot):
    """毎日の本文は食事記録にし、宣伝文や外部リンクを定型追加しない。"""
    n = bundle['total'] if slot == 'daily' else bundle['meals'][slot]['nutrition']
    if slot == 'daily':
        body = '今日の食事記録\n' + '\n'.join(bundle['meals'][s]['menu'] for s in ('breakfast', 'lunch', 'dinner'))
    else:
        meal = bundle['meals'][slot]
        body = meal['comment'] + '\n' + meal['menu']
    return (body + f"\n{value(n['calories'])} kcal｜P {grams(n['protein'])}g / F {grams(n['fat'])}g / C {grams(n['carbs'])}g")


class InstagramRuntime:
    def __init__(self, database, daily_directory, directory, store=None, api=None, meal=None):
        self.database, self.daily_directory = database, daily_directory
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.store = store or InstagramStore(database)
        self.api = api
        self.meal = meal

    def buffer(self):
        return self.api or InstagramBufferClient(self.store)

    def state(self, target):
        return await_checked_at((self.meal or meal_client()).day_state(target), max_ahead=5)

    def render(self, target):
        state = self.state(target)
        bundle = load_bundle(self.database, self.daily_directory, target, state)
        destination = self.directory / target / LAYOUT_VERSION / bundle['bundle_sha256']
        destination.mkdir(parents=True, exist_ok=True)
        save_json(destination / 'bundle.json', bundle)
        manifest = render_bundle(bundle, destination)
        return bundle, manifest, destination

    def artwork(self, target, allow_public=False):
        """公開候補を登録せず、完成画像と下書き用の対応を作る。"""
        bundle, manifest, destination = self.render(target)
        channel = instagram_channel(self.buffer())
        drive = DriveClient.from_env()
        candidates = []
        for slot in SLOTS:
            assets = [host_asset(a, drive, self.store, allow_public=allow_public) for a in manifest['assets'][slot]]
            c = InstagramCandidate(target, slot, channel['id'], channel['serviceId'],
                                   'post' if slot == 'daily' else 'story', caption(bundle, slot), assets,
                                   {k: bundle[k] for k in ('bundle_sha256', 'state_version', 'x_digests')})
            c.validate(datetime.now(UTC), HOSTS, require_future=False)
            candidates.append(c)
        save_json(destination / 'candidates.json', [asdict(c) for c in candidates])
        return candidates

    def prepare(self, target, allow_public=False):
        if slot_time(date.fromisoformat(target), 'breakfast') <= datetime.now(UTC):
            raise ValueError('4枠が未来の対象日を指定してください')
        candidates = self.artwork(target, allow_public)
        for c in candidates:
            self.store.save(c, datetime.now(UTC), HOSTS)
        return candidates

    def accept(self, target, allow_public=False):
        """既存日の実データでも非公開下書きだけを作り、二つの形式を受入検証する。"""
        candidates = {c.slot: c for c in self.artwork(target, allow_public)}
        for slot in ('breakfast', 'daily'):
            c = candidates[slot]
            self.verify(c)
            draft_once(self.store, c, self.buffer())
        return {'story_draft_verified': True, 'carousel_draft_verified': True, 'published': False}

    def prepare_next(self):
        target = (datetime.now(JST).date() + timedelta(days=1)).isoformat()
        rows = self.store.rows(target)
        if len(rows) != 4:
            if os.getenv('MINA_INSTAGRAM_PREPARE_ENABLED', 'false').lower() != 'true':
                raise ValueError('Instagram翌日準備が無効です')
            try:
                self.prepare(target, allow_public=True)
            except Exception:
                try:
                    self.store.queue_preparation_hold(target)
                    self.send_notices()
                except Exception:
                    # 通知障害で準備処理の元の例外を失わない。未送信分は常駐処理が扱う。
                    pass
                raise
        return {'target_date': target, 'notices_sent': self.notify(target)}

    def verify(self, candidate):
        destination = self.directory / candidate.target_date / candidate.layout_version / candidate.source['bundle_sha256']
        bundle = json.loads((destination / 'bundle.json').read_text(encoding='utf-8'))
        manifest = json.loads((destination / 'rendered.json').read_text(encoding='utf-8'))
        if manifest['bundle_sha256'] != candidate.source['bundle_sha256']:
            raise ValueError('画像と食事正本の版が一致しません')
        expected = manifest['assets'][candidate.slot]
        if (len(expected) != len(candidate.assets) or any(a['sha256'] != b['sha256'] for a, b in zip(expected, candidate.assets))
                or candidate.caption != caption(bundle, candidate.slot)):
            raise ValueError('画像または本文が正本と一致しません')
        source = {k: bundle[k] for k in ('bundle_sha256', 'state_version', 'x_digests')}
        from .instagram_recovery import base_source, publication_window
        if 'recovery' in candidate.source:
            row = next(r for r in self.store.rows(candidate.target_date) if r['slot'] == candidate.slot)
            publication_window(self.store, row, candidate)
        if source != base_source(candidate) or digest({k: v for k, v in bundle.items() if k != 'bundle_sha256'}) != bundle['bundle_sha256']:
            raise ValueError('食事正本の記録が変わっています')
        return verify_bundle(bundle, self.database, self.daily_directory, self.state(candidate.target_date), datetime.now(UTC))

    def draft(self, target):
        rows = {r['slot']: r for r in self.store.rows(target)}
        for slot in ('breakfast', 'daily'):
            candidate = InstagramCandidate(**json.loads(rows[slot]['payload']))
            self.verify(candidate)
            draft_once(self.store, candidate, self.buffer())
        return {'story_draft_verified': True, 'carousel_draft_verified': True, 'published': False}

    def notify(self, target):
        if os.getenv('MINA_INSTAGRAM_APPROVAL_ENABLED', 'false').lower() != 'true':
            raise ValueError('Instagram承認受付が無効です')
        for row in self.store.rows(target):
            if row['state'] == 'approval_pending':
                self.store.queue_approval(row)
        return self.send_notices()

    def recover(self, target, slot, until, instruction=None, recorded_at=None):
        """未送信保留の一枠を再点検し、新カードまたは本人の明示依頼で復旧する。"""
        from .clients import require_live
        from .instagram_recovery import prepare_recovery, approve_recovery_instruction, check_instruction
        from .instagram_media import verify_public
        require_live()
        if os.getenv('MINA_INSTAGRAM_APPROVAL_ENABLED', 'false').lower() != 'true':
            raise ValueError('Instagram承認受付が無効です')
        if instruction is not None or recorded_at is not None:
            check_instruction(instruction, recorded_at, datetime.now(UTC), until)
        row = next(r for r in self.store.rows(target) if r['slot'] == slot)
        recovered = prepare_recovery(self.store, row, datetime.now(UTC), until, HOSTS, self.verify, verify_public)
        if instruction is None:
            self.store.queue_approval(recovered)
        else:
            approve_recovery_instruction(self.store, recovered, datetime.now(UTC), instruction, recorded_at)
        return {'target_date': target, 'slot': slot, 'revision': recovered['revision'], 'published': False}

    def send_notices(self):
        if os.getenv('MINA_INSTAGRAM_NOTIFY_ENABLED', 'false').lower() != 'true':
            return 0
        return self.store.send_notices(DiscordClient(os.getenv('DISCORD_BOT_TOKEN', ''), os.getenv('DISCORD_ALLOWED_CHANNEL_ID', '')))

    def tick(self):
        from .instagram_publisher import reconcile_known
        reconciled = reconcile_known(self.store, scheduled=True, client_factory=self.buffer)
        if os.getenv('MINA_INSTAGRAM_PUBLISH_ENABLED', 'false').lower() != 'true':
            return {'publishing_enabled': False, 'reconciled': reconciled, 'notices_sent': self.send_notices()}
        row = self.store.claim_due(datetime.now(UTC))
        if row:
            try:
                publish_once(self.store, row, self.verify, HOSTS, client=self.buffer())
            except Exception:
                # 機密を含み得るAPI応答・例外原文を常駐ログへ出さない。
                pass
        return {'processed': bool(row), 'reconciled': reconciled, 'notices_sent': self.send_notices()}

    def daemon(self):
        self.store.hold_interrupted()
        while True:
            try:
                self.tick()
            except Exception:
                print('{"event":"instagram_cycle_held"}', flush=True)
            time.sleep(15)
