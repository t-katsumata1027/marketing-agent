"""供給側の最終引継ぎ情報を、外部接続せず形式・版・証跡種別で確認する。"""

import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit

BASE_EVIDENCE = ('isolation', 'ci', 'independent_review', 'acceptance', 'cleanup')
PRODUCTION_EVIDENCE = ('migration', 'release', 'postdeploy', 'mina_activation',
                       'production_acceptance', 'production_cleanup')


def inspect_handoff(packet, contract_file, expected_commit):
    """報告の自己申告を実証済みや接続開始許可へ変換しない。"""
    problems = []
    if not isinstance(packet, dict):
        packet = {}
    permitted = {'format', 'contract_version', 'code_commit', 'contract_sha256',
                 'environment', 'api_base_url', 'credential_reference', 'evidence', 'runbook_reference'}
    if set(packet) - permitted:
        problems.append('unknown_fields_or_secret_values')
    if packet.get('format') != 'mina-handoff-v1' or packet.get('contract_version') != 'mina-write-v1':
        problems.append('contract_format_mismatch')
    if not isinstance(expected_commit, str) or not re.fullmatch(r'[0-9a-f]{40}', expected_commit) or packet.get('code_commit') != expected_commit:
        problems.append('code_commit_mismatch')
    try:
        path = Path(contract_file)
        if path.is_symlink() or path.stat().st_size > 1024 * 1024:
            raise ValueError()
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if packet.get('contract_sha256') != actual:
            problems.append('contract_hash_mismatch')
    except (OSError, ValueError, TypeError):
        problems.append('contract_file_unavailable')
    environment = packet.get('environment')
    if environment not in ('isolated_qa', 'production'):
        problems.append('environment_unrecognized')
    try:
        url = urlsplit(packet.get('api_base_url', ''))
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.port not in (None, 443) or url.query or url.fragment or url.path not in ('', '/'):
            raise ValueError()
        # URL名だけでQAの隔離は証明できないが、既知の本番流用は拒否する。
        if environment == 'isolated_qa' and url.hostname in ('log-eats.com', 'www.log-eats.com'):
            problems.append('production_url_in_qa')
    except (ValueError, TypeError, AttributeError):
        problems.append('api_url_invalid')
    reference = packet.get('credential_reference')
    if not isinstance(reference, dict) or set(reference) != {'provider', 'project', 'config', 'secret_name'} or reference.get('provider') != 'doppler' or reference.get('secret_name') != 'MINA_AGENT_API_TOKEN' or any(not isinstance(reference.get(k), str) or not reference[k].strip() or len(reference[k]) > 100 for k in ('project', 'config')):
        problems.append('credential_reference_invalid')
    elif environment == 'isolated_qa' and reference['project'] == 'marketing-agent' and reference['config'] == 'dev':
        problems.append('production_credentials_in_qa')
    if not isinstance(packet.get('runbook_reference'), str) or not packet['runbook_reference'].strip():
        problems.append('runbook_reference_missing')
    evidence = packet.get('evidence')
    evidence = evidence if isinstance(evidence, dict) else {}
    required = BASE_EVIDENCE + (PRODUCTION_EVIDENCE if environment == 'production' else ())
    missing = []
    for name in required:
        item = evidence.get(name)
        # 同じ供給コードとの対応は報告側で明示し、結果原文・IDをログへ出さない。
        if not isinstance(item, dict) or set(item) != {'code_commit', 'result', 'kind', 'reference'} or item.get('code_commit') != expected_commit or item.get('result') != 'passed' or item.get('kind') != 'real' or not isinstance(item.get('reference'), str) or not item['reference'].strip():
            missing.append(name)
    if missing:
        problems.append('real_evidence_missing_or_wrong_version')
    return {'packet_structure_valid': not problems, 'problems': problems,
            'missing_real_evidence': missing, 'evidence_contents_verified': False,
            'producer_code_compatibility_verified': False, 'production_start_allowed': False,
            'external_calls_performed': False,
            'next_steps': ['証跡原文とcommit対応を担当者が確認', '固定した最終供給コードで契約結合試験',
                           '運用設定と停止・復旧手順の照合', '承認された実受入後に接続開始を判断']}
