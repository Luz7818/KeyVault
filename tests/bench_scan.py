"""kv scan 大规模性能基准：造 N 条密钥的 vault + M 个文件的目录树，给扫描计时。

用法（仓库根）：
    python tests/bench_scan.py                          # 默认 10,000 条 / 200 文件
    python tests/bench_scan.py --count 2000 --files 50
    python tests/bench_scan.py --fake                   # 用 PlaintextProtector 跳过 DPAPI

不参与 unittest discover（文件名不以 test 开头，discover 的 pattern 是 test*.py）。
默认用真 DPAPI：10,000 次 protect 就是真实库的构建成本，基准不能挑便宜的算。
数字口径：scan 只计时 scan_directory 本身（含全部指纹解密 + 目录遍历 + 逐行匹配）；
目录内容约 2 MB 纯文本 + 5 处故意埋入的命中，走一遍命中路径。
"""

from __future__ import annotations

import argparse
import hashlib
import random
import shutil
import string
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kv.core import db
from kv.core.vault import VaultStore
from kv.crypto.dpapi import DpapiProtector
from kv.model import Candidate, Verdict
from kv.ops import save as saveops
from kv.ops import scan as scanops

# 七种真实平台的形态（前缀 + 主体长度），总长互不相同 —— 长度桶数直接影响
# _scan_line 的循环层数，单一长度的基准会低估真实库的成本。
SHAPES = (
    ("sk-", 44),            # openai-compatible 形态
    ("sk-ant-api03-", 40),  # anthropic
    ("ghp_", 36),           # github
    ("AKIA", 16),           # aws access key id
    ("xoxb-", 40),          # slack
    ("glpat-", 20),         # gitlab
    ("", 64),               # 裸 hex64
)


def make_value(index: int) -> str:
    """确定性地造第 index 个唯一密钥。hexdigest 逐条不同，去重不会误伤。"""
    body = hashlib.sha256(f"kv-bench-{index}".encode()).hexdigest()
    prefix, length = SHAPES[index % len(SHAPES)]
    return prefix + body[:length]


def build_vault(vault_dir: Path, count: int, protector) -> VaultStore:
    """建库并入库 count 条。返回 store。"""
    vault_dir.mkdir(parents=True, exist_ok=True)
    db_path = vault_dir / "vault.db"
    db.initialize(db_path, canary_blob=protector.protect(b"kv-canary"),
                  canary_text="kv-canary")
    store = VaultStore(db_path, protector=protector)
    for i in range(count):
        value = make_value(i).encode("utf-8")
        cand = Candidate(value=value, kind="token")
        verdict = Verdict(
            platform="deepseek", confidence="manual", source="bench",
            evidence="bench", mask_style=(3, 4), kind="token",
        )
        saveops.commit(cand, verdict, store=store,
                       policy=saveops.SavePolicy(name=f"bench-{i:05d}", actor="bench"))
    return store


def build_tree(tree_dir: Path, files: int, lines_per_file: int, plant: list[str]) -> int:
    """造目录树。返回总字节数。最后几处埋入真实密钥，让命中路径也被走到。"""
    rng = random.Random(20261012)
    alphabet = string.ascii_letters + string.digits + " =_.-/:"
    tree_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    planted = 0
    for f in range(files):
        chunks = []
        for _ in range(lines_per_file):
            line = "".join(rng.choices(alphabet, k=rng.randint(60, 140)))
            chunks.append(line + "\n")
        if plant and planted < len(plant) and f % max(1, files // len(plant)) == 0:
            chunks.append(f"API_KEY = '{plant[planted]}'\n")
            planted += 1
        text = "".join(chunks)
        (tree_dir / f"mod{f:04d}.txt").write_text(text, encoding="utf-8")
        total += len(text.encode("utf-8"))
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="kv scan 性能基准")
    parser.add_argument("--count", type=int, default=10000, help="vault 里密钥条数")
    parser.add_argument("--files", type=int, default=200, help="扫描目录里的文件数")
    parser.add_argument("--lines", type=int, default=60, help="每个文件行数")
    parser.add_argument("--fake", action="store_true",
                        help="用 PlaintextProtector（跳过 DPAPI，只看扫描本身）")
    args = parser.parse_args()

    protector = None
    if args.fake:
        from tests.fakes import PlaintextProtector

        protector = PlaintextProtector()
    else:
        protector = DpapiProtector()

    tmp = Path(tempfile.mkdtemp(prefix="kv-bench-"))
    try:
        print(f"加密        {'PlaintextProtector（--fake）' if args.fake else '真 DPAPI'}")
        print(f"记录数      {args.count}")

        t0 = time.perf_counter()
        store = build_vault(tmp / "vault", args.count, protector)
        t_setup = time.perf_counter() - t0

        plant = [make_value(i * 997) for i in range(5)]
        t0 = time.perf_counter()
        tree_bytes = build_tree(tmp / "tree", args.files, args.lines, plant)
        t_tree = time.perf_counter() - t0

        print(f"建库+入库   {t_setup:.1f} s（{args.count / t_setup:.0f} 条/s）")
        print(f"造目录树    {t_tree:.1f} s（{args.files} 文件 / {tree_bytes / 1e6:.1f} MB）")

        for round_no in (1, 2):
            t0 = time.perf_counter()
            result = scanops.scan_directory(store, str(tmp / "tree"))
            elapsed = time.perf_counter() - t0
            print(
                f"scan 第{round_no}轮  {elapsed:.2f} s"
                f"（{result.scanned} 文件 / {tree_bytes / 1e6:.1f} MB / {args.count} 指纹，"
                f"命中 {len(result.hits)}）"
            )
        if len(result.hits) != len(plant):
            print(f"[警告] 命中 {len(result.hits)} 与埋入 {len(plant)} 不符，基准树有问题")
            return 1
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
