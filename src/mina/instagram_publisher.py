"""Instagramへ一回だけ送信し、受付と公開確定を分ける。"""

from datetime import UTC, datetime
import json
import os
import time

from .buffer_trial import ORGANIZATIONS, CHANNELS, ProbeError
from .clients import require_live
from .domain import instant
from .instagram_buffer_client import InstagramBufferClient
from .instagram_domain import HANDLE, InstagramCandidate, media_url, publication_url
from .instagram_media import verify_public

FIELDS = '''id text status sentAt externalLink schedulingType channel { id service }
 assets { source mimeType } metadata { ... on InstagramPostMetadata { type isAiGenerated shouldShareToFeed } }'''
CREATE = 'mutation($input:CreatePostInput!) { createPost(input:$input) { __typename ... on PostActionSuccess { post { ' + FIELDS + ' } } ... on MutationError { message } } }'
GET = 'query($input:PostInput!) { post(input:$input) { ' + FIELDS + ' } }'


def instagram_channel(client):
    organizations = client._request(ORGANIZATIONS)['account']['organizations']
    if not 1 <= len(organizations) <= 5:
        raise ProbeError('instagram_organization_unconfirmed')
    matches = []
    for organization in organizations:
        rows = client._request(CHANNELS, {'input': {'organizationId': organization['id']}})['channels']
        matches.extend(c for c in rows if c['service'] == 'instagram' and c['name'].lstrip('@').lower() == HANDLE)
    if len(matches) != 1:
        raise ProbeError('instagram_channel_unconfirmed')
    c = matches[0]
    if c['isDisconnected'] or c['isLocked'] or c['isQueuePaused'] or not c['id'] or not c['serviceId']:
        raise ProbeError('instagram_channel_not_ready')
    return c


def request_for(c, draft=False):
    from .scoped_preparation import blocked
    if blocked({'source':c.source}):raise ValueError('scoped_preparation_publication_forbidden')
    return {'channelId': c.channel_id, 'text': c.caption, 'assets': [{'image': {'url': a['url']}} for a in c.assets],
            'schedulingType': 'automatic', 'mode': 'addToQueue' if draft else 'shareNow',
            'saveToDraft': draft, 'needsApproval': False,
            'metadata': {'instagram': {'type': c.format, 'shouldShareToFeed': c.format == 'post',
                                       'isAiGenerated': c.is_ai_generated}}}


def validate_post(c, post, draft=False):
    if (not isinstance(post, dict) or not post.get('id') or post.get('text') != c.caption
            or post.get('channel') != {'id': c.channel_id, 'service': 'instagram'}
            or post.get('schedulingType') != 'automatic'):
        raise ProbeError('instagram_post_binding_changed')
    metadata = post.get('metadata') or {}
    if (metadata.get('type') != c.format or metadata.get('isAiGenerated') != c.is_ai_generated
            or metadata.get('shouldShareToFeed') != (c.format == 'post')):
        raise ProbeError('instagram_post_metadata_changed')
    assets = post.get('assets', [])
    if len(assets) != len(c.assets) or any(a.get('source') != b['url'] for a, b in zip(assets, c.assets)):
        raise ProbeError('instagram_post_assets_changed')
    if draft:
        if post.get('status') != 'draft' or post.get('sentAt') or post.get('externalLink'):
            raise ProbeError('instagram_draft_not_private')
        return True
    if post.get('status') in ('scheduled', 'sending') and not post.get('sentAt') and not post.get('externalLink'):
        return None
    if post.get('status') != 'sent' or not post.get('sentAt'):
        raise ProbeError('instagram_delivery_unconfirmed')
    return publication_url(post['externalLink'], c.format)


def draft_once(store, c, client):
    """下書きの成否不明を反復しない。公開モードを使わない。"""
    require_live()
    from .scoped_preparation import blocked
    if blocked({'source':c.source}):raise ValueError('scoped_preparation_publication_forbidden')
    from .publication_hold import require_buffer_mutation
    require_buffer_mutation(store,c.target_date)
    client.publication_target = c.target_date
    from .instagram_media import HOSTS
    c.validate(datetime.now(UTC), HOSTS, require_future=False)
    channel = instagram_channel(client)
    if channel['id'] != c.channel_id or channel['serviceId'] != c.account_id:
        raise ProbeError('instagram_target_changed')
    with store.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS instagram_drafts(digest TEXT PRIMARY KEY,state TEXT NOT NULL,post_id TEXT)')
        previous = db.execute('SELECT * FROM instagram_drafts WHERE digest=?', (c.digest,)).fetchone()
        if not previous:
            db.execute("INSERT INTO instagram_drafts VALUES(?,'attempting',NULL)", (c.digest,))
    if previous:
        if previous['state'] not in ('verified', 'created') or not previous['post_id']:
            raise ProbeError('instagram_draft_reconciliation_required')
        post = client._request(GET, {'input': {'id': previous['post_id']}})['post']
        if post.get('id') != previous['post_id']:
            raise ProbeError('instagram_post_id_changed')
        validate_post(c, post, True)
    else:
        from .publication_hold import mutation_lease
        with mutation_lease(store, c.target_date, 'instagram_draft'):
            result = client._request(CREATE, {'input': request_for(c, True)})['createPost']
        if result.get('__typename') != 'PostActionSuccess' or not result.get('post', {}).get('id'):
            raise ProbeError('instagram_draft_unconfirmed')
        post = result['post']
        with store.transaction() as db:
            db.execute("UPDATE instagram_drafts SET state='created',post_id=? WHERE digest=?", (post['id'], c.digest))
        validate_post(c, post, True)
        readback = client._request(GET, {'input': {'id': post['id']}})['post']
        if readback.get('id') != post['id']:
            raise ProbeError('instagram_post_id_changed')
        validate_post(c, readback, True)
    with store.transaction() as db:
        db.execute("UPDATE instagram_drafts SET state='verified' WHERE digest=?", (c.digest,))
        db.execute('''CREATE TABLE IF NOT EXISTS instagram_acceptance
            (channel_id TEXT NOT NULL,format TEXT NOT NULL,layout_version TEXT NOT NULL,
             PRIMARY KEY(channel_id,format,layout_version))''')
        db.execute('INSERT OR IGNORE INTO instagram_acceptance VALUES(?,?,?)', (c.channel_id, c.format, c.layout_version))
    return post['id']


def publish_once(store, row, verify_source, hosts, client=None, clock=lambda: datetime.now(UTC),
                 sleep=time.sleep, verify_media=verify_public):
    c = InstagramCandidate(**json.loads(row['payload']))
    client = client or InstagramBufferClient(store)
    client.publication_target = c.target_date
    started = time.monotonic()
    attempted = False
    stage = 'authorization'
    try:
        require_live()
        if os.getenv('MINA_INSTAGRAM_PUBLISH_ENABLED', 'false').lower() != 'true':
            raise ProbeError('instagram_publishing_disabled')
        with store.transaction() as db:
            if (not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='instagram_acceptance'").fetchone()
                    or not db.execute('SELECT 1 FROM instagram_acceptance WHERE channel_id=? AND format=? AND layout_version=?',
                                      (c.channel_id, c.format, c.layout_version)).fetchone()):
                raise ProbeError('instagram_draft_acceptance_required')
        from .instagram_recovery import publication_window
        due, deadline = publication_window(store, row, c)
        if (not 0 <= (instant(clock()) - due).total_seconds() <= 120 or
                (deadline is not None and instant(clock()) >= deadline)):
            raise ProbeError('instagram_deadline_expired')
        stage = 'account'
        channel = instagram_channel(client)
        if channel['id'] != c.channel_id or channel['serviceId'] != c.account_id:
            raise ProbeError('instagram_target_changed')
        stage = 'source'
        verify_source(c)
        stage = 'media'
        for asset in c.assets:
            media_url(asset['url'], hosts)
            verify_media(asset)
        if (time.monotonic() - started > 120 or
                not 0 <= (instant(clock()) - due).total_seconds() <= 120 or
                (deadline is not None and instant(clock()) >= deadline)):
            raise ProbeError('instagram_preflight_expired')
        request = request_for(c)
        store.begin(row, request, clock())
        attempted = True
        stage = 'submission'
        from .publication_hold import require_claim
        require_claim(store, row, 'instagram_candidates')
        result = client._request(CREATE, {'input': request})['createPost']
        if result.get('__typename') != 'PostActionSuccess' or not result.get('post', {}).get('id'):
            raise ProbeError('instagram_submission_unknown')
        post = result['post']
        store.mark(row, 'accepted', post)
        stage = 'delivery'
        for attempt in range(7):
            url = validate_post(c, post)
            if url:
                sent = instant(datetime.fromisoformat(post['sentAt'].replace('Z', '+00:00')))
                if not 0 <= (sent - due).total_seconds() <= 120:
                    raise ProbeError('instagram_delivery_late')
                store.mark(row, 'published', post, url)
                return url
            if attempt == 6:
                break
            sleep(10)
            post_id = post['id']
            post = client._request(GET, {'input': {'id': post_id}})['post']
            if post.get('id') != post_id:
                raise ProbeError('instagram_post_id_changed')
        from .audit import OperationalFailure
        raise OperationalFailure('outcome_unknown', 'delivery')
    except Exception as error:
        from .audit import safe_failure
        reason, details = safe_failure(error)
        store.mark(row, 'outcome_unknown' if attempted else 'held', failure={'stage': stage, 'reason': reason, **details})
        raise


def reconcile_known(store, client=None, **options):
    """受付IDが保存された不明枠だけを読取照合する。createPostは使わない。"""
    from .instagram_reconcile import reconcile_known as reconcile
    return reconcile(store, client, **options)
