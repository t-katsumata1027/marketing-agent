"""復号用秘密鍵をサーバーへ置かず、復旧用ファイルを暗号化する。"""

import hashlib
import os
import struct
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .artifact_backup import checked_path

MAGIC = b'MINA-BACKUP-1\x00'
LIMIT = 256 * 1024 * 1024


def read_regular(path, limit):
    """リンク・特殊ファイル・過大ファイルを拒否する。"""
    import stat
    path = checked_path(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as reader:
        info = os.fstat(reader.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise ValueError('復旧用ファイルの形式・容量が不正です')
        raw = reader.read(limit + 1)
        after = os.fstat(reader.fileno())
        if len(raw) != info.st_size or (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('復旧用ファイルが読取中に変化しました')
    return raw


def save_new(path, raw):
    """既存ファイルを上書きせず、完成した内容だけを保護領域へ保存する。"""
    path = checked_path(path)
    if path.exists() or not path.parent.is_dir():
        raise ValueError('新規の保護された保存先が必要です')
    if os.name == 'posix' and path.parent.stat().st_mode & 0o077:
        raise ValueError('保存先に第三者のアクセス権があります')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as writer:
            writer.write(raw)
            writer.flush()
            os.fsync(writer.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def seal_file(source, output, public_key_file):
    """公開鍵で暗号化する。認証情報・鍵の本文を応答へ含めない。"""
    key = serialization.load_pem_public_key(read_regular(public_key_file, 16384))
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 3072:
        raise ValueError('3072bit以上のRSA公開鍵が必要です')
    content = read_regular(source, LIMIT)
    secret, nonce = AESGCM.generate_key(bit_length=256), os.urandom(12)
    wrapped = key.encrypt(secret, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=MAGIC))
    header = MAGIC + struct.pack('>H', len(wrapped)) + wrapped + nonce
    encrypted = header + AESGCM(secret).encrypt(nonce, content, header)
    save_new(output, encrypted)
    return {'encrypted_backup_created': True, 'bytes': len(encrypted),
            'sha256': hashlib.sha256(encrypted).hexdigest(),
            'recipient_sha256': hashlib.sha256(key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest(),
            'drive_upload_performed': False}


def unseal_file(source, output, private_key_file, password):
    """隔離復元先へだけ復号する。認証失敗では平文を作らない。"""
    try:
        if not isinstance(password, bytes) or not password:
            raise ValueError()
        key = serialization.load_pem_private_key(read_regular(private_key_file, 32768), password=password)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 3072:
            raise ValueError()
        raw = read_regular(source, LIMIT + 4096)
        if not raw.startswith(MAGIC):
            raise ValueError()
        offset = len(MAGIC)
        length = struct.unpack('>H', raw[offset:offset+2])[0]
        if length != key.key_size // 8:
            raise ValueError()
        boundary = offset + 2 + length
        wrapped, nonce = raw[offset+2:boundary], raw[boundary:boundary+12]
        header = raw[:boundary+12]
        secret = key.decrypt(wrapped, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=MAGIC))
        content = AESGCM(secret).decrypt(nonce, raw[boundary+12:], header)
        if not 0 < len(content) <= LIMIT:
            raise ValueError()
    except Exception:
        raise ValueError('バックアップの復号・認証を確認できません') from None
    save_new(output, content)
    return {'isolated_file_restored': True, 'bytes': len(content),
            'sha256': hashlib.sha256(content).hexdigest(), 'live_restore_performed': False}
