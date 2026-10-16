"""Bufferの適合性を隔離台帳と非公開下書きだけで検査する。予約・公開機能は持たない。"""

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from datetime import UTC,datetime
from contextlib import nullcontext
from .audit import OperationalFailure


EXPECTED_ACCOUNT = 'EatsLog88161'
TEST_TEXT = '【非公開の接続テスト】本文・URLの保持を確認する下書きです。公開しません。\nhttps://www.log-eats.com/'
FIELDS = 'id text status dueAt sentAt externalLink channel { id service }'
ORGANIZATIONS = 'query { account { organizations { id } } }'
CHANNELS = '''query($input: ChannelsInput!) {
 channels(input: $input) { id name service serviceId externalLink timezone isDisconnected isLocked isQueuePaused }
}'''
CREATE = '''mutation($input: CreatePostInput!) {
 createPost(input: $input) { __typename
 ... on PostActionSuccess { post { ''' + FIELDS + ''' } }
 ... on MutationError { message }
 }
}'''
GET_POST = 'query($input: PostInput!) { post(input: $input) { ' + FIELDS + ' } }'


class ProbeError(RuntimeError):
    """外部の例外文や秘密値を含めない静的な失敗分類。"""
    def __init__(self,code,http_status=None):
        self.http_status=http_status
        self.audit_reason=({'buffer_local_quota_stop':'buffer_quota','buffer_key_missing_or_invalid':'authentication',
            'buffer_http_401':'authentication','buffer_http_403':'authentication','buffer_http_429':'buffer_quota',
            'buffer_response_invalid':'response_invalid','buffer_graphql_error':'response_invalid'}.get(code,'buffer_unavailable'))
        super().__init__(code)


class TransientBufferRead(OperationalFailure):
    """送信前の接続queryに限る一時通信障害。公開要求には使わない。"""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class BufferTrialClient:
    def __init__(self, token, transport=None, on_request=None):
        if not isinstance(token, str) or not token.strip() or '\n' in token or '\r' in token:
            raise ProbeError('buffer_key_missing_or_invalid')
        self._token = token
        self._transport = transport
        self._on_request = on_request
        self.calls = 0
        self.connection_deadline=None
        self.connection_clock=lambda:datetime.now(UTC)
        self.audit_context=None

    def _request(self, query, variables=None):
        connection=query in (ORGANIZATIONS,CHANNELS)
        stage='connection' if connection else 'submission' if query==CREATE else 'delivery'
        operation='channel_organizations' if query==ORGANIZATIONS else 'channel_list' if query==CHANNELS else 'post_create' if query==CREATE else 'post_lookup'
        context=nullcontext()
        if self.audit_context:
            audit,target,slot,revision=self.audit_context
            context=audit.operation('publication',target,slot,revision,stage,{'operation':operation})
        with context:
            return self._perform_request(query,variables,connection)

    def _perform_request(self, query, variables, connection):
        """固定宛先へ一回だけ送る。GraphQLの200内エラーも拒否する。"""
        timeout=20
        can_retry=connection and self.connection_deadline is not None
        if can_retry:
            remaining=(self.connection_deadline-self.connection_clock()).total_seconds()
            if remaining<=0:raise OperationalFailure('retry_deadline','connection')
            timeout=min(timeout,remaining)
        if self._on_request is not None:
            self._on_request(query)
        self.calls += 1
        try:
            if self._transport is not None:
                result = self._transport(query, variables or {})
            else:
                request = Request('https://api.buffer.com',
                    data=json.dumps({'query': query, 'variables': variables or {}}, ensure_ascii=False).encode(),
                    headers={'Authorization': 'Bearer ' + self._token,
                             'Content-Type': 'application/json', 'Cache-Control': 'no-store'})
                with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
                    raw = response.read(262145)
                if len(raw) > 262144:
                    raise ProbeError('buffer_response_too_large')
                result = json.loads(raw)
        except ProbeError:
            raise
        except HTTPError as error:
            code = error.code
            if can_retry and code in (502,503,504):
                raise TransientBufferRead('connection_http_transient','connection',code) from None
            raise ProbeError('buffer_http_' + str(code),code) from None
        except (URLError, TimeoutError, OSError) as error:
            if can_retry:
                timed=isinstance(error,TimeoutError) or isinstance(getattr(error,'reason',None),TimeoutError)
                raise TransientBufferRead('connection_timeout' if timed else 'connection_network','connection') from None
            raise ProbeError('buffer_response_unknown') from None
        except ValueError:
            raise ProbeError('buffer_response_invalid') from None
        if not isinstance(result, dict) or result.get('errors') or not isinstance(result.get('data'), dict):
            raise ProbeError('buffer_graphql_error')
        return result['data']

    def channel(self):
        organizations = self._request(ORGANIZATIONS).get('account', {}).get('organizations')
        if not isinstance(organizations, list) or not 1 <= len(organizations) <= 5:
            raise ProbeError('buffer_organization_unconfirmed')
        matches = []
        for organization in organizations:
            channels = self._request(CHANNELS, {'input': {'organizationId': organization['id']}}).get('channels')
            if not isinstance(channels, list):
                raise ProbeError('buffer_channels_unconfirmed')
            matches += [c for c in channels if isinstance(c, dict) and c.get('service') == 'twitter'
                        and c.get('name', '').lstrip('@').lower() == EXPECTED_ACCOUNT.lower()]
        if len(matches) != 1:
            raise ProbeError('buffer_target_unconfirmed')
        channel = matches[0]
        if (channel.get('isDisconnected') is not False or channel.get('isLocked') is not False
                or not channel.get('id') or not channel.get('serviceId')):
            raise ProbeError('buffer_channel_unavailable')
        return channel

    def create_draft(self, channel_id):
        """入力を固定し、公開できるモードや日時を受け付けない。"""
        result = self._request(CREATE, {'input': {'channelId': channel_id, 'text': TEST_TEXT,
            'assets': [], 'schedulingType': 'automatic', 'mode': 'addToQueue',
            'saveToDraft': True, 'needsApproval': False}}).get('createPost')
        if not isinstance(result, dict) or result.get('__typename') != 'PostActionSuccess':
            raise ProbeError('buffer_draft_not_confirmed')
        post = result.get('post')
        if not isinstance(post, dict) or not post.get('id'):
            raise ProbeError('buffer_draft_id_unknown')
        return post

    def post(self, post_id):
        return self._request(GET_POST, {'input': {'id': post_id}}).get('post')


def validate_draft(post, channel_id, post_id):
    if not isinstance(post, dict) or post.get('id') != post_id:
        raise ProbeError('buffer_draft_read_unknown')
    if post.get('status') != 'draft' or post.get('sentAt') is not None or post.get('externalLink'):
        raise ProbeError('buffer_draft_not_private')
    if post.get('text') != TEST_TEXT:
        raise ProbeError('buffer_text_changed')
    if post.get('channel') != {'id': channel_id, 'service': 'twitter'}:
        raise ProbeError('buffer_draft_target_changed')


def probe(client, journal_path, create_draft=False):
    """対象照合後、明示指定時だけ非公開下書きを一度作る。既存運用DBは開かない。"""
    channel = client.channel()
    report = {'target_account_matches': True, 'channel_connected': True,
              'channel_timezone_is_jst': channel.get('timezone') == 'Asia/Tokyo',
              'queue_paused': channel.get('isQueuePaused'), 'draft_created_this_run': False,
              'draft_verified': False, 'scheduled': False, 'published': False}
    if not create_draft:
        return {**report, 'api_calls': client.calls}
    path = Path(journal_path)
    if path.name != 'buffer-trial.sqlite3':
        raise ProbeError('isolated_trial_database_required')
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256((channel['id'] + '\n' + channel['serviceId'] + '\n' + TEST_TEXT).encode()).hexdigest()
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        if os.name != 'nt':
            path.chmod(0o600)
        db.execute('CREATE TABLE IF NOT EXISTS trial (singleton INTEGER PRIMARY KEY CHECK(singleton=1), digest TEXT NOT NULL, state TEXT NOT NULL, post_id TEXT)')
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT digest,state,post_id FROM trial WHERE singleton=1').fetchone()
        if row is None:
            db.execute("INSERT INTO trial VALUES (1,?,'attempting',NULL)", (digest,))
            db.commit()
            try:
                post = client.create_draft(channel['id'])
            except Exception:
                db.execute("UPDATE trial SET state='outcome_unknown' WHERE singleton=1")
                raise
            # 応答内のIDを先に保全し、再照会失敗後も新規作成へ戻らない。
            post_id = post['id']
            db.execute("UPDATE trial SET state='readback_required',post_id=? WHERE singleton=1", (post_id,))
            report['draft_created_this_run'] = True
            validate_draft(post, channel['id'], post_id)
        else:
            db.commit()
            if row[0] != digest:
                raise ProbeError('trial_input_changed')
            if not row[2]:
                raise ProbeError('trial_reconciliation_required')
            post_id = row[2]
        validate_draft(client.post(post_id), channel['id'], post_id)
        db.execute("UPDATE trial SET state='draft_verified' WHERE singleton=1")
    return {**report, 'draft_verified': True, 'api_calls': client.calls}


def main():
    parser = argparse.ArgumentParser(description='Bufferの非公開接続試験。公開・予約・取消は実装しない。')
    parser.add_argument('--journal', required=True)
    parser.add_argument('--create-private-draft', action='store_true')
    args = parser.parse_args()
    try:
        report = probe(BufferTrialClient(os.getenv('BUFFER_API_KEY')), args.journal, args.create_private_draft)
    except ProbeError as error:
        report = {'ok': False, 'reason': str(error), 'automatic_retry': False}
    except Exception:
        report = {'ok': False, 'reason': 'buffer_probe_unclassified', 'automatic_retry': False}
    print(json.dumps(report, ensure_ascii=False))
    return 1 if report.get('ok') is False else 0


if __name__ == '__main__':
    raise SystemExit(main())
