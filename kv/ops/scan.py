"""扫描目录树，匹配已存指纹。

把 vault 里每条指纹（含已吊销的）解密后当搜索模式，在目标目录树里逐行匹配。
值只活在内存里，函数返回后全部归零 —— 和 reveal() 同样的纪律。

退出码由调用方（commands/）决定：0=干净，3=命中，5=闸门失败。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from kv.core import repo
from kv.model import ScanHit

# 50 MB 以上跳过 —— 日志、打包产物、数据库通常在这个量级。
MAX_FILE_SIZE = 50 * 1024 * 1024
# 探测头 8 KB 里有没有 \\x00。和 git/ripgrep 同一套路。
BINARY_CHECK_SIZE = 8192


@dataclass(frozen=True)
class ScanResult:
    hits: tuple[ScanHit, ...]
    scanned: int
    skipped_binary: int
    skipped_large: int


@dataclass
class _FpInfo:
    """一条指纹的元数据。value 是解密后的明文，扫描完归零。"""

    value: str
    secret_id: int
    name: str
    platform: str
    status: str
    sha256: str


def _load_fingerprints(store) -> list[_FpInfo]:
    """从 vault 加载全部指纹并解密。调用方负责用完后归零。"""
    with store.read() as conn:
        rows = repo.scan_fingerprints_with_blobs(conn)
    result: list[_FpInfo] = []
    for row in rows:
        try:
            plain = store.protector.unprotect(bytes(row["value_blob"]))
        except Exception:
            continue
        try:
            result.append(_FpInfo(
                value=plain.decode("utf-8", errors="ignore"),
                secret_id=int(row["secret_id"]),
                name=row["name"],
                platform=row["platform"],
                status=row["status"],
                sha256=row["sha256"],
            ))
        finally:
            buf = bytearray(plain)
            for i in range(len(buf)):
                buf[i] = 0
            del buf
            del plain
    return result


def _build_index(fps: list[_FpInfo]) -> dict[int, dict[str, list[_FpInfo]]]:
    """按值长度建索引。搜索时只切同样长的子串，避免 O(fingerprints × line)。"""
    by_len: dict[int, dict[str, list[_FpInfo]]] = {}
    for fp in fps:
        if not fp.value:
            continue
        vlen = len(fp.value)
        bucket = by_len.setdefault(vlen, {})
        bucket.setdefault(fp.value, []).append(fp)
    return by_len


def _is_binary(path: str) -> bool:
    """头 8 KB 有 \\x00 就当二进制。"""
    try:
        data = Path(path).read_bytes()[:BINARY_CHECK_SIZE]
        return b"\x00" in data
    except OSError:
        return True


def _scan_file(path: str, index: dict) -> list[ScanHit]:
    """逐行扫描一个文本文件，返回命中列表。编码失败 → 跳过。"""
    hits: list[ScanHit] = []
    try:
        with open(path, "r", encoding="utf-8", errors="strict") as fh:
            for line_no, line in enumerate(fh, 1):
                _scan_line(line, path, line_no, index, hits)
    except (UnicodeDecodeError, OSError):
        pass
    return hits


def _scan_line(line: str, path: str, line_no: int, index: dict, hits: list) -> None:
    """在一行里切出各长度的子串，查索引。"""
    for vlen, value_map in index.items():
        if vlen > len(line):
            continue
        for start in range(len(line) - vlen + 1):
            needle = line[start : start + vlen]
            if needle in value_map:
                for fp in value_map[needle]:
                    hits.append(ScanHit(
                        path=path, line_no=line_no, secret_id=fp.secret_id,
                        name=fp.name, platform=fp.platform, status=fp.status,
                        sha256_prefix=fp.sha256[:12],
                    ))


def _walk_directory(
    root: str, index: dict,
) -> tuple[list[ScanHit], int, int, int]:
    """遍历目录树。返回 (hits, scanned, skipped_binary, skipped_large)。"""
    hits: list[ScanHit] = []
    scanned = 0
    skipped_binary = 0
    skipped_large = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            try:
                size = os.path.getsize(filepath)
            except OSError:
                continue
            if size > MAX_FILE_SIZE:
                skipped_large += 1
                continue
            if _is_binary(filepath):
                skipped_binary += 1
                continue
            scanned += 1
            hits.extend(_scan_file(filepath, index))
    return hits, scanned, skipped_binary, skipped_large


def scan_directory(store, root: str) -> ScanResult:
    """扫描目录树，返回结构化结果。值在返回前全部归零。"""
    fps = _load_fingerprints(store)
    try:
        index = _build_index(fps)
        hits, scanned, skipped_binary, skipped_large = _walk_directory(root, index)
    finally:
        for fp in fps:
            buf = list(fp.value)
            for i in range(len(buf)):
                buf[i] = 0
            del buf
        fps.clear()
    return ScanResult(
        hits=tuple(hits), scanned=scanned,
        skipped_binary=skipped_binary, skipped_large=skipped_large,
    )
