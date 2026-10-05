"""前台监听循环。

**没有常驻进程**：kv watch 是前台命令，Ctrl-C 或 q 结束。

轮询用 GetClipboardSequenceNumber —— 它**不打开剪切板**，所以永不与其他应用争用、
成本为零（实测 0.5 s 间隔下是每秒 2 次系统调用）。只有序号变了才真的去读。

**默认内存队列。** watch 会看到密码管理器复制的密码、2FA 验证码、带 token 的 URL；
占位符闸门拒绝的是模板，不是真凭据。自动保存会用你从没打算存的东西污染库，还会把
它们写进审计轨迹。所以候选只活在 RAM 里，唯一的磁盘写入是你明确接受的那些记录。
--spool 才落盘，而且是 DPAPI 加密后落盘。

**v1 没有日志文件。** 原始剪切板文本不以任何形式持久化：Candidate.span 记
"line 3"，打印出来的是掩码预览。这比一个脱敏过滤器是更强的保证 —— 没有东西需要脱敏。
"""

from __future__ import annotations

import hashlib
from collections import deque

from kv import PROGRAM, console
from kv.capture import window as windowmod
from kv.capture.clipboard import ClipboardBackend
from kv.capture.wipe import sweep_due_wipes
from kv.detect.corrections import CorrectionSet
from kv.ops import review as reviewops
from kv.parse import pipeline

DEFAULT_INTERVAL = 0.5
SEEN_RING_SIZE = 200
SWEEP_EVERY_TICKS = 20  # 每 ~10 s 清扫一次过期擦除，不必每 tick 都读库

KEYS_HELP = (
    ("a", "接受全部高置信候选（exact / high / manual）"),
    ("n", "逐条处理下一条（歧义的会走平台菜单）"),
    ("l", "列出队列"),
    ("c", "清空队列（丢弃，不落盘）"),
    ("h", "显示这个帮助"),
    ("q", "退出并丢弃队列"),
)


def _read_key() -> str:
    """非阻塞读一个按键。没有可用输入就返回空串。

    GUI 后台线程或 pythonw（无控制台）里 kbhit 可能抛错——
    监听循环不能因为按键探测而中断，一律按无输入处理。
    """
    try:
        import msvcrt
        if not msvcrt.kbhit():
            return ""
        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):
            msvcrt.getwch()
            return ""
        return char.lower()
    except Exception:
        return ""


def watch(store, backend: ClipboardBackend, *, interval: float = DEFAULT_INTERVAL,
          spool: bool = False, auto: bool = False, window_hint: bool = True,
          actor: str = "watch", max_ticks: int = 0,
          stop_event=None, on_event=None) -> int:
    """监听剪切板。max_ticks > 0 时跑够那么多轮就返回（测试用）。

    stop_event：threading.Event，置位即优雅退出（GUI 的停止按钮用）。
    on_event：每捕获一批候选回调一次，收一行掩码摘要（GUI 日志用）；
    不传则照旧打印到控制台。两者都不影响 spool/队列的落盘语义。
    """
    import threading

    if stop_event is None:
        stop_event = threading.Event()
    corrections = _load_corrections(store)
    queue: list[reviewops.PendingItem] = []
    seen: deque[str] = deque(maxlen=SEEN_RING_SIZE)
    ticks = 0
    captured = 0
    last_sequence = backend.sequence()

    if on_event is None:
        _print_banner(spool=spool, auto=auto, window_hint=window_hint)

    try:
        while True:
            ticks += 1
            if max_ticks and ticks > max_ticks:
                break
            if stop_event.wait(interval):
                break

            if ticks % SWEEP_EVERY_TICKS == 0:
                sweep_due_wipes(store, backend, actor=actor)

            key = _read_key()
            if key:
                action = _handle_key(key, queue, store, actor=actor, auto=auto)
                if action == "quit":
                    break
                if action == "reload":
                    corrections = _load_corrections(store)

            # _tick 返回它自己读到的序号。主循环末尾再读一次会引入竞态：
            # 两次读之间剪切板若变化，last_sequence 会被设成新值，那次变化就被吞掉了。
            count, last_sequence = _tick(store, backend, corrections, queue, seen,
                                         last_sequence, spool=spool, window_hint=window_hint,
                                         on_event=on_event)
            captured += count
    except KeyboardInterrupt:
        console.echo("")
        console.warn("已中断，队列丢弃（默认模式下什么都没落盘）")
        return 130

    if queue and not spool:
        console.warn(f"退出时队列里还有 {len(queue)} 条未处理，已丢弃")
    console.echo(f"共捕获 {captured} 个候选，落盘 {len(queue) if spool else 0} 条待审")
    return 0


def _tick(store, backend: ClipboardBackend, corrections: CorrectionSet,
          queue: list, seen: deque, last_sequence: int, *,
          spool: bool, window_hint: bool, on_event=None) -> tuple[int, int]:
    """一轮轮询。返回 (这一轮新捕获的候选数, 本轮读到的序号)。

    序号没变就什么都不做 —— GetClipboardSequenceNumber 不打开剪切板，
    所以永不与其他应用争用，0.5 s 间隔下是每秒 2 次系统调用。
    """
    sequence = backend.sequence()
    if sequence == last_sequence:
        return 0, last_sequence

    text = backend.read()
    if text is None:
        return 0, sequence  # 争用：跳过这一 tick，绝不崩

    digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
    if digest in seen:
        return 0, sequence  # 序号变了但内容没变：你又复制了一遍同样的东西
    seen.append(digest)

    items = _capture(store, text, corrections, window_hint=window_hint)
    if not items:
        return 0, sequence

    for item in items:
        if spool:
            reviewops.spool(store, item)
        queue.append(item)
    _announce(items, spool=spool, on_event=on_event)
    return len(items), sequence


def _capture(store, text: str, corrections: CorrectionSet, *,
             window_hint: bool) -> list[reviewops.PendingItem]:
    """剪切板文本 → 待处理项。窗口标题在这里用完就丢。"""
    title = windowmod.foreground_title() if window_hint else ""
    result = pipeline.parse(text, source_hint="clipboard")
    items = []
    for candidate in result.candidates:
        if _already_stored(store, candidate.sha256()):
            continue
        verdict = reviewops.resolve_with_window(candidate, corrections, title)
        items.append(reviewops.PendingItem(candidate=candidate, verdict=verdict,
                                           captured_at=_now()))
    return items


def _already_stored(store, sha256: str) -> bool:
    return store.by_sha256(sha256) is not None


def _now() -> str:
    from kv import clock

    return clock.now_iso()


def _announce(items, *, spool: bool, on_event=None) -> None:
    """每个候选发一行摘要。**只打印掩码预览和平台，绝不打印剪切板原文。**

    传了 on_event（GUI 日志）就回调，否则打到控制台——两条路发同一份内容，
    只是格式一个面向列表框、一个面向终端。
    """
    for item in items:
        preview = item.candidate.preview(console.mask_char(), *item.verdict.mask_style)
        summary = _summarize(item)
        where = "已落盘待审" if spool else "已入内存队列"
        if on_event is not None:
            on_event(f"检测到：{summary}  {preview}（{where}）")
        else:
            console.echo(f"  ⚡ {preview}  ->  {summary}  [{where}]")


def _summarize(item) -> str:
    from kv.detect import report

    return report.summarize(item.verdict)


def _handle_key(key: str, queue: list, store, *, actor: str, auto: bool) -> str:
    if key == "q":
        return "quit"
    if key == "h" or key == "?":
        _print_keys()
        return ""
    if key == "l":
        reviewops.list_pending(queue)
        return ""
    if key == "c":
        count = len(queue)
        queue.clear()
        console.echo(f"  已丢弃 {count} 条")
        return ""
    if key == "a":
        targets = [i for i in queue if i.auto_acceptable]
        if not targets:
            console.echo("  没有可自动接受的高置信候选（按 n 逐条处理）")
            return ""
        outcome = reviewops.review(targets, store, interactive=False, auto=True, actor=actor)
        for item in targets:
            _discard(queue, item)
        reviewops.echo_outcome(outcome)
        return ""
    if key == "n":
        if not queue:
            console.echo("  队列为空")
            return ""
        item = queue[0]
        console.echo(reviewops.describe_item(1, len(queue), item))
        action, result = reviewops.decide(item, store, actor=actor, interactive=True)
        _discard(queue, item)
        if result and result.outcome == "created":
            console.ok(f"  已存 #{result.secret_id} {result.name}")
        elif result and result.outcome == "deduped":
            console.echo(f"  已在库里（#{result.secret_id} {result.name}）")
        # 记住选择会改纠正规则表，得重新加载
        return "reload" if action == "remember" else ""
    return ""


def _discard(queue: list, item) -> None:
    try:
        queue.remove(item)
    except ValueError:
        pass


def _load_corrections(store) -> CorrectionSet:
    from kv.core import repo

    try:
        with store.read() as conn:
            return CorrectionSet.from_rows(repo.list_corrections(conn))
    except Exception:  # noqa: BLE001 —— 监听循环不能因为读库失败而中断
        return CorrectionSet.empty()


def _print_banner(*, spool: bool, auto: bool, window_hint: bool) -> None:
    console.echo(f"{PROGRAM} watch —— 复制到凭据时自动识别。Ctrl-C 或 q 退出")
    console.echo(f"  间隔 {DEFAULT_INTERVAL}s  落盘 {'是（--spool，DPAPI 加密）' if spool else '否（内存队列）'}"
                 f"  自动接受 {'是（仅 exact/high/manual）' if auto else '否'}"
                 f"  窗口标题信号 {'开' if window_hint else '关'}")
    if window_hint:
        console.echo("  窗口标题只在内存里比对，原文绝不落盘、不进日志、不进审计")
    _print_keys()
    console.echo("")


def _print_keys() -> None:
    console.echo("  按键：" + "   ".join(f"{k} {help_text}" for k, help_text in KEYS_HELP))
