"""备份与恢复：口令加密的全量明文导出（.kvb）。

为什么存「口令加密的明文」而不是 DPAPI 密文：DPAPI blob 绑定当前用户与
机器，重装系统 / 换机后解不开——备份密文等于备份废铁。业界同款做法
（Bitwarden 加密导出）：口令是唯一防线，所以 KDF 用 scrypt(n=2^15) 抬
暴力破解成本，认证用 HMAC-SHA256（encrypt-then-MAC），流加密用
SHA256-CTR（stdlib 可组合的标准构造，nonce 16B 随机保证唯一）。

文件格式（.kvb）：
    b"KVBK1\\n" | salt(16) | nonce(16) | ct | tag(HMAC-SHA256, 32)
明文 = JSON：{"version": 1, "exported_at": ..., "records": [...]}，value
以 base64 存原始字节。恢复走 saveops.commit 正常入库（去重、审计链都在）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac as hmac_mod
import json
import os
from dataclasses import dataclass
from pathlib import Path

from kv import clock, errors
from kv.core import masking
from kv.core.vault import VaultStore
from kv.detect import resolve
from kv.detect.corrections import CorrectionSet
from kv.model import Candidate, Verdict
from kv.ops import save as saveops

MAGIC = b"KVBK1\n"
SALT_LEN = 16
NONCE_LEN = 16
TAG_LEN = 32
SCRYPT_N = 2**15
KDF_DKLEN = 64


@dataclass(frozen=True)
class BackupResult:
    path: Path
    count: int


@dataclass(frozen=True)
class RestoreResult:
    created: int
    deduped: int
    total: int


def _derive_keys(password: str, salt: bytes) -> tuple[bytes, bytes]:
    """scrypt → 64B，前 32B 流加密、后 32B 认证。

    n=2^15 r=8 需要 32MB，OpenSSL 默认 maxmem 恰好卡线——显式放宽到 64MB。
    """
    keys = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=8, p=1, dklen=KDF_DKLEN,
        maxmem=64 * 1024 * 1024,
    )
    return keys[:32], keys[32:]


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """SHA256-CTR：块 i = SHA256(key || nonce || i)。nonce 随机 16B 保证唯一。"""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest())
        counter += 1
    return bytes(out[:length])


def _mac(mac_key: bytes, salt: bytes, nonce: bytes, ct: bytes) -> bytes:
    return hmac_mod.new(mac_key, MAGIC + salt + nonce + ct, hashlib.sha256).digest()


def _collect_records(store: VaultStore) -> list[dict]:
    """导出全部记录的明文（value 解密后 base64）。revoked/expired 一并带走。"""
    records = []
    for row in store.list_secrets():
        with store.reveal(row.name, actor="backup", event="export") as buf:
            value = bytes(buf)
        records.append({
            "name": row.name,
            "value": base64.b64encode(value).decode("ascii"),
            "kind": row.kind,
            "platform": row.platform,
            "key_name": row.key_name or "",
            "tags": list(row.tags),
            "note": row.note,
            "expires_at": row.expires_at or "",
            "status": row.status,
        })
    return records


def export_backup(store: VaultStore, path: Path, password: str) -> BackupResult:
    """全量导出到 path（口令加密）。口令太弱直接拒绝——它是唯一防线。"""
    if len(password) < 8:
        raise errors.UsageError(
            "备份口令至少 8 位",
            hint="口令是备份的唯一防线，丢了密钥就没了，弱口令等于没加密",
        )
    payload = {
        "version": 1,
        "exported_at": clock.now_iso(),
        "records": _collect_records(store),
    }
    pt = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    salt, nonce = os.urandom(SALT_LEN), os.urandom(NONCE_LEN)
    enc_key, mac_key = _derive_keys(password, salt)
    ks = _keystream(enc_key, nonce, len(pt))
    ct = bytes(a ^ b for a, b in zip(pt, ks))
    blob = MAGIC + salt + nonce + ct + _mac(mac_key, salt, nonce, ct)
    Path(path).write_bytes(blob)
    store.record_audit("export", "backup", detail={"path": str(path), "count": len(payload["records"])})
    return BackupResult(Path(path), len(payload["records"]))


def restore_backup(
    store: VaultStore, path: Path, password: str, *, actor: str = "restore"
) -> RestoreResult:
    """从 .kvb 恢复：逐条走正常入库（去重 + 审计），重名同值 dedupe。"""
    blob = Path(path).read_bytes()
    head_len = len(MAGIC) + SALT_LEN + NONCE_LEN
    if len(blob) < head_len + TAG_LEN or not blob.startswith(MAGIC):
        raise errors.BackupError("不是有效的 KeyVault 备份文件（.kvb）")
    salt = blob[len(MAGIC):len(MAGIC) + SALT_LEN]
    nonce = blob[len(MAGIC) + SALT_LEN:head_len]
    ct = blob[head_len:-TAG_LEN]
    tag = blob[-TAG_LEN:]
    enc_key, mac_key = _derive_keys(password, salt)
    if not hmac_mod.compare_digest(_mac(mac_key, salt, nonce, ct), tag):
        raise errors.BackupAuthError(
            "口令错误，或备份文件已被篡改",
            hint="口令是备份的唯一凭据，没有找回机制",
        )
    ks = _keystream(enc_key, nonce, len(ct))
    pt = bytes(a ^ b for a, b in zip(ct, ks))
    try:
        payload = json.loads(pt.decode("utf-8"))
        records = payload["records"]
    except (ValueError, KeyError) as exc:
        raise errors.BackupError(f"备份内容损坏：{exc}") from exc

    created = deduped = 0
    for rec in records:
        value = base64.b64decode(rec["value"])
        cand = Candidate(value=value, kind=rec.get("kind") or "token",
                         source="restore", span="restore")
        verdict = Verdict(
            platform=rec.get("platform") or "unknown",
            confidence="manual", source="correction", evidence="备份恢复",
            mask_style=masking.infer_mask_style(value), kind=cand.kind,
        )
        result = saveops.commit(
            cand, verdict, store=store,
            policy=saveops.SavePolicy(
                name=rec.get("name") or "", tags=tuple(rec.get("tags") or ()),
                note=rec.get("note") or "", expires_at=rec.get("expires_at") or None,
                origin="restore", actor=actor, force=True,
            ),
        )
        if result.outcome == "created":
            created += 1
        elif result.outcome == "deduped":
            deduped += 1
    store.record_audit(
        "restore", actor,
        detail={"path": str(path), "created": created, "deduped": deduped},
    )
    return RestoreResult(created, deduped, len(records))
