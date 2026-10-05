"""M2 watch 循环逻辑测试：用 FakeClipboard 驱动 _tick，不碰真剪切板。

覆盖计划里的验收点：
* 序号没变 → 什么都不做（零 CPU 轮询的核心）
* 序号变了 → 读内容、过 SHA-256 去重环、跑检测
* 争用（read 返回 None）→ 跳过这一 tick 而不崩
* 同样的内容再复制一次 → seen 环过滤掉
* max_ticks 让循环正常终止
* spool 模式把候选 DPAPI 加密后写进 inbox
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401
from tests.fakes import FakeClipboard, PlaintextProtector

from kv.capture import watch as watchmod
from kv.core import db, repo
from kv.core.vault import VaultStore
from kv.detect.corrections import CorrectionSet

SECRET = "sk-" + "A" * 44 + "c31f"
DOTENV = (
    f"DEEPSEEK_API_KEY={SECRET}\n"
    "GITHUB_TOKEN=ghp_" + "B" * 36 + "\n"
)


def _make_store(vault_dir: Path) -> VaultStore:
    vault_dir.mkdir(parents=True, exist_ok=True)
    db_path = vault_dir / "vault.db"
    protector = PlaintextProtector()
    canary = protector.protect(b"kv-canary")
    db.initialize(db_path, canary_blob=canary, canary_text="kv-canary")
    return VaultStore(db_path, protector=protector)


class TestTick(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.store = _make_store(self.vault)
        self.addCleanup(self._tmp.cleanup)
        # _tick 会调 _announce → console.echo，需要重定向输出
        self._saved_stdout = sys.stdout
        sys.stdout = io.StringIO()
        self.addCleanup(self._restore_stdout)

    def _restore_stdout(self):
        sys.stdout = self._saved_stdout

    def test_sequence_unchanged_is_noop(self):
        """序号没变 → 什么都不做，这是零 CPU 轮询的核心。"""
        backend = FakeClipboard(script=[(1, "hello")])
        corrections = CorrectionSet.empty()
        queue, seen = [], []
        count, seq = watchmod._tick(
            self.store, backend, corrections, queue, seen,
            last_sequence=1, spool=False, window_hint=False,
        )
        self.assertEqual(count, 0)
        self.assertEqual(seq, 1)
        self.assertEqual(queue, [])

    def test_sequence_change_captures_candidates(self):
        """序号变了 → 读内容、跑检测、把候选推进队列。"""
        backend = FakeClipboard(script=[(1, DOTENV)])
        corrections = CorrectionSet.empty()
        queue, seen = [], []
        count, seq = watchmod._tick(
            self.store, backend, corrections, queue, seen,
            last_sequence=0, spool=False, window_hint=False,
        )
        self.assertGreater(count, 0)
        self.assertEqual(seq, 1)
        self.assertEqual(len(queue), count)

    def test_contention_skips_tick_without_crashing(self):
        """OpenClipboard 争用让 read() 返回 None → 跳过，绝不崩。"""
        backend = FakeClipboard(script=[(1, DOTENV)])
        backend.contention = True
        corrections = CorrectionSet.empty()
        queue, seen = [], []
        count, seq = watchmod._tick(
            self.store, backend, corrections, queue, seen,
            last_sequence=0, spool=False, window_hint=False,
        )
        self.assertEqual(count, 0)
        self.assertEqual(seq, 1)

    def test_seen_ring_deduplicates_same_content(self):
        """序号变了但内容没变 → seen 环过滤掉。"""
        backend = FakeClipboard(script=[(1, DOTENV), (2, DOTENV)])
        corrections = CorrectionSet.empty()
        queue, seen = [], []
        # First tick captures.
        watchmod._tick(self.store, backend, corrections, queue, seen,
                       last_sequence=0, spool=False, window_hint=False)
        first_count = len(queue)
        self.assertGreater(first_count, 0)
        # Second tick: sequence changed but content is the same.
        count, seq = watchmod._tick(
            self.store, backend, corrections, queue, seen,
            last_sequence=1, spool=False, window_hint=False,
        )
        self.assertEqual(count, 0)
        self.assertEqual(len(queue), first_count)

    def test_non_credential_text_produces_no_candidates(self):
        """剪切板里是普通文本 → 没有候选，队列保持空。"""
        backend = FakeClipboard(script=[(1, "hello world")])
        corrections = CorrectionSet.empty()
        queue, seen = [], []
        count, seq = watchmod._tick(
            self.store, backend, corrections, queue, seen,
            last_sequence=0, spool=False, window_hint=False,
        )
        self.assertEqual(count, 0)
        self.assertEqual(queue, [])


class TestWatchLoop(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.store = _make_store(self.vault)
        self.addCleanup(self._tmp.cleanup)
        self._saved_stdout = sys.stdout
        sys.stdout = io.StringIO()
        self.addCleanup(self._restore_stdout)

    def _restore_stdout(self):
        sys.stdout = self._saved_stdout

    def test_max_ticks_terminates_loop(self):
        """max_ticks > 0 让 watch() 跑够那么多轮就返回。"""
        backend = FakeClipboard(script=[(1, "nothing")])
        code = watchmod.watch(
            self.store, backend, interval=0.001, max_ticks=3,
            window_hint=False,
        )
        self.assertEqual(code, 0)

    def test_watch_captures_from_script(self):
        """watch 跑过脚本里的剪切板变化，捕获到候选。"""
        backend = FakeClipboard(script=[(0, ""), (1, DOTENV)])
        code = watchmod.watch(
            self.store, backend, interval=0.001, max_ticks=3,
            window_hint=False,
        )
        self.assertEqual(code, 0)


class TestSpoolMode(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.store = _make_store(self.vault)
        self.addCleanup(self._tmp.cleanup)
        self._saved_stdout = sys.stdout
        sys.stdout = io.StringIO()
        self.addCleanup(self._restore_stdout)

    def _restore_stdout(self):
        sys.stdout = self._saved_stdout

    def test_spool_writes_to_inbox_table(self):
        """spool=True 把候选 DPAPI 加密后写进 inbox 表。"""
        backend = FakeClipboard(script=[(1, DOTENV)])
        corrections = CorrectionSet.empty()
        queue, seen = [], []
        count, _ = watchmod._tick(
            self.store, backend, corrections, queue, seen,
            last_sequence=0, spool=True, window_hint=False,
        )
        self.assertGreater(count, 0)
        with self.store.read() as conn:
            inbox_rows = repo.list_inbox(conn, "pending")
        self.assertEqual(len(inbox_rows), count)


class TestGuiIntegration(unittest.TestCase):
    """GUI 剪贴板监控的调用形态：stop_event 停止、on_event 回调、spool 落盘。

    回归背景：GUI 曾向 watch() 传 stop_event/on_event，而签名里没有这两个
    参数，TypeError 后静默退化到一个只打日志、不写 inbox 的简化循环——
    检测到了却永远不落盘。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.store = _make_store(self.vault)
        self.addCleanup(self._tmp.cleanup)
        self._saved_stdout = sys.stdout
        sys.stdout = io.StringIO()
        self.addCleanup(self._restore_stdout)

    def _restore_stdout(self):
        sys.stdout = self._saved_stdout

    def test_on_event_receives_masked_summary(self):
        """on_event 收到掩码摘要，明文不出现在回调里。"""
        events: list[str] = []
        backend = FakeClipboard(script=[(1, DOTENV)])
        corrections = CorrectionSet.empty()
        queue, seen = [], []
        count, _ = watchmod._tick(
            self.store, backend, corrections, queue, seen,
            last_sequence=0, spool=True, window_hint=False,
            on_event=events.append,
        )
        self.assertGreater(count, 0)
        self.assertEqual(len(events), count)
        joined = "\n".join(events)
        self.assertIn("检测到", joined)
        self.assertNotIn(SECRET, joined)
        # 走了回调就不该再往控制台打 ⚡ 行
        self.assertNotIn("⚡", sys.stdout.getvalue())

    def test_preset_stop_event_exits_immediately(self):
        """stop_event 已置位 → watch() 立即返回，不死循环。"""
        import threading

        backend = FakeClipboard(script=[(1, "nothing")])
        stop = threading.Event()
        stop.set()
        code = watchmod.watch(
            self.store, backend, interval=0.001,
            window_hint=False, stop_event=stop,
        )
        self.assertEqual(code, 0)

    def test_gui_shape_spools_and_announces(self):
        """按 GUI 的实参形态跑 watch：检测到的候选落进审批队列（inbox）。"""
        import threading
        import time

        events: list[str] = []
        stop = threading.Event()
        backend = FakeClipboard()

        def producer():
            # 等 watch 起来并记下初始序号，再模拟一次真实复制
            time.sleep(0.05)
            backend.write(DOTENV)
            time.sleep(0.2)
            stop.set()

        t = threading.Thread(target=producer, daemon=True)
        t.start()
        code = watchmod.watch(
            self.store, backend, interval=0.01, spool=True, auto=False,
            window_hint=False, stop_event=stop,
            on_event=events.append,
        )
        t.join()
        self.assertEqual(code, 0)
        self.assertTrue(events)
        from kv.ops.review import pending_from_inbox

        pending = pending_from_inbox(self.store)
        self.assertGreater(len(pending), 0)
        self.assertEqual(len(events), len(pending))


if __name__ == "__main__":
    unittest.main()
