"""Instagram専用の明示操作。既存X常駐処理へ生成を追加しない。"""

import argparse
import json
import os

from .instagram_runtime import InstagramRuntime


def main():
    parser = argparse.ArgumentParser(description='Instagram用の画像合成・下書き・個別承認・配信')
    parser.add_argument('command', choices=('status', 'render', 'artwork', 'accept', 'prepare', 'prepare-next', 'draft', 'notify', 'reconcile', 'tick', 'daemon'))
    parser.add_argument('--date')
    parser.add_argument('--allow-public-media', action='store_true')
    args = parser.parse_args()
    runtime = InstagramRuntime(os.getenv('MINA_DATABASE', '/var/lib/mina-agent/mina.sqlite3'),
                               os.getenv('MINA_DAILY_DIRECTORY', '/var/lib/mina-agent/daily'),
                               os.getenv('MINA_INSTAGRAM_DIRECTORY', '/var/lib/mina-agent/instagram'))
    if args.command in ('render', 'artwork', 'accept', 'prepare', 'draft', 'notify') and not args.date:
        parser.error('対象日を指定してください')
    try:
        if args.command == 'status':
            result = {'candidates': [{'date': r['target_date'], 'slot': r['slot'], 'state': r['state']} for r in runtime.store.rows(args.date)]}
        elif args.command == 'render':
            bundle, manifest, _ = runtime.render(args.date)
            result = {'rendered': sum(len(a) for a in manifest['assets'].values()), 'source_unchanged': True}
        elif args.command == 'prepare':
            result = {'candidates': len(runtime.prepare(args.date, args.allow_public_media)), 'published': False}
        elif args.command == 'artwork':
            result = {'artworks': len(runtime.artwork(args.date, args.allow_public_media)), 'published': False}
        elif args.command == 'accept':
            result = runtime.accept(args.date, args.allow_public_media)
        elif args.command == 'draft':
            result = runtime.draft(args.date)
        elif args.command == 'prepare-next':
            result = runtime.prepare_next()
        elif args.command == 'notify':
            result = {'notices_sent': runtime.notify(args.date)}
        elif args.command == 'tick':
            result = runtime.tick()
        elif args.command == 'reconcile':
            from .instagram_publisher import reconcile_known
            result = {'confirmed': reconcile_known(runtime.store, runtime.buffer()), 'resubmitted': False}
        else:
            runtime.daemon()
            return
        print(json.dumps(result, ensure_ascii=False))
    except Exception:
        print('{"ok":false,"reason":"instagram_operation_held"}')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
