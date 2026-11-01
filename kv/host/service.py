"""宿主请求分发：扩展消息 → 现有管线的薄封装。

设计定位：宿主不发明任何安全语义，只换输入源——
- 捕获 = pipeline.parse + resolve（复用 watch._capture 的流程，第 5 级
  信号从「前台窗口标题」换成「页面标题」——浏览器标签页标题本来就会
  出现在 OS 窗口标题里，语义完全一致）→ 全量 spool 进 inbox（静息态
  只有 DPAPI 密文，页面 URL 等非敏感上下文随行进 context_json）；
- 网页提示条只对 promptable（exact/high/manual 且平台非 unknown，与
  PendingItem.auto_acceptable 同一口径）的候选出现，approve 才真正入库；
  其余留在 inbox 由桌面 GUI / kv review 消化——「歧义绝不猜厂商」不变；
- 列表只出掩码行（永不带 value_blob/明文）；
- 复制走 copy_secret（排除 Win+V 历史 + 30s 自动擦除），与桌面端同一条路。
"""

from __future__ import annotations

import json
import sqlite3

from kv import __version__, clock, errors
from kv.core import repo
from kv.core.vault import VaultStore
from kv.detect.corrections import CorrectionSet
from kv.model import Candidate, Verdict
from kv.ops import review as reviewops
from kv.ops import save as saveops

MAX_TEXT_CHARS = 200_000
MAX_FIELD_CHARS = 2048
PROMPTABLE = ("manual", "exact", "high")


def handle(store: VaultStore | None, request: dict, *, backend=None) -> dict:
    """分发一条已解析的请求。任何失败都变成 {"error": ...} 响应——协议通道
    上不允许异常冒泡（异常文本可能含本机路径，也不该打断会话）。"""
    try:
        kind = request.get("type")
        handlers = {
            "ping": _ping,
            "capture": _capture,
            "approve": _approve,
            "reject": _reject,
            "list": _list,
            "copy": _copy,
        }
        handler = handlers.get(kind)
        if handler is None:
            return {"error": f"未知请求类型：{kind!r}"}
        if store is None and kind != "ping":
            return {"error": "vault 未初始化——先在桌面端运行 kv init 或在 GUI 里初始化"}
        return handler(store, request, backend=backend)
    except errors.KvError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 —— 通道上不允许异常逃逸
        from kv.host import log

        log.warning("请求处理异常：%r", exc)
        return {"error": "宿主内部错误（详见宿主日志）"}


# ---------------------------------------------------------------- 各请求

def _ping(store, request, *, backend=None) -> dict:
    return {"pong": True, "version": __version__, "initialized": store is not None}


def _capture(store, request, *, backend=None) -> dict:
    text = _field(request, "text", required=True, max_chars=MAX_TEXT_CHARS)
    source_url = _field(request, "source_url", max_chars=MAX_FIELD_CHARS)
    title = _field(request, "title", max_chars=MAX_FIELD_CHARS)

    from kv.parse import pipeline

    result = pipeline.parse(text, source_hint="extension")
    with store.read() as conn:
        corrections = CorrectionSet.from_rows(repo.list_corrections(conn))
    responses = []
    for cand in result.candidates:
        verdict = reviewops.resolve_with_window(cand, corrections, title or None)
        responses.append(
            _capture_one(store, cand, verdict, source_url=source_url, title=title)
        )
    return {"type": "capture", "results": responses}


def _capture_one(store, cand: Candidate, verdict: Verdict, *,
                 source_url: str, title: str) -> dict:
    """单个候选 → inbox。返回给扩展的行只含掩码与判定元数据。"""
    item = reviewops.PendingItem(
        candidate=cand, verdict=verdict, captured_at=clock.now_iso())
    if store.by_sha256(cand.sha256()) is not None:
        status, inbox_id = "existing", None
    else:
        status, inbox_id = _spool_or_pending(store, item, source_url, title)
    return {
        "inbox_id": inbox_id,
        "status": status,
        "platform": verdict.platform,
        "confidence": verdict.confidence,
        "preview": cand.preview(_mask_char(), *verdict.mask_style),
        "key_name": cand.key_name or "",
        "promptable": bool(item.auto_acceptable),
    }


def _spool_or_pending(store, item: PendingItem, source_url: str, title: str
                      ) -> tuple[str, int | None]:
    """spool 进 inbox。同值已有 pending 行（idx_inbox_pending 部分唯一索引）
    不视为错误——找回那一行，让扩展能对同一条候选 approve/reject。"""
    pending_id = _find_pending(store, item.candidate.sha256())
    if pending_id is not None:
        return "already_pending", pending_id
    try:
        inbox_id = reviewops.spool(store, item, context=_capture_context(
            item, source_url, title))
    except sqlite3.IntegrityError:  # 并发窗口：另一进程刚好 spool 了同值
        pending_id = _find_pending(store, item.candidate.sha256())
        return ("already_pending", pending_id) if pending_id else ("pending", None)
    return "pending", inbox_id


def _find_pending(store, sha256: str) -> int | None:
    with store.read() as conn:
        for row in repo.list_inbox(conn, "pending"):
            if row["sha256"] == sha256:
                return int(row["id"])
    return None


def _approve(store, request, *, backend=None) -> dict:
    inbox_id = _int_field(request, "inbox_id", required=True)
    with store.read() as conn:
        row = repo.get_inbox(conn, inbox_id)
    if row is None or row["status"] != "pending":
        return {"error": f"inbox #{inbox_id} 不存在或不在待审状态"}
    item = _item_from_row(store, row)
    if not item.auto_acceptable:
        return {"error": "该候选置信度不足（歧义/未知平台），请到桌面端审批消歧"}

    context = _loads(row["context_json"])
    name = _field(request, "name", max_chars=120)
    result = saveops.commit(
        item.candidate, item.verdict, store=store,
        policy=saveops.SavePolicy(
            name=name, origin="extension", actor="host",
            source_url=str(context.get("source_url", "")),
        ),
    )
    if result.outcome == "rejected":
        return {"error": result.warning}
    reviewops.mark_inbox_decided(store, item, accepted=True)
    response = {"type": "approve", "ok": True, "outcome": result.outcome,
                "name": result.name}
    if result.merged_tags:
        response["merged_tags"] = list(result.merged_tags)
    if result.warning:
        response["warning"] = result.warning
    return response


def _reject(store, request, *, backend=None) -> dict:
    inbox_id = _int_field(request, "inbox_id", required=True)
    with store.read() as conn:
        row = repo.get_inbox(conn, inbox_id)
    if row is None or row["status"] != "pending":
        return {"error": f"inbox #{inbox_id} 不存在或不在待审状态"}
    reviewops.mark_inbox_decided(store, _item_from_row(store, row), accepted=False)
    return {"type": "reject", "ok": True}


def _list(store, request, *, backend=None) -> dict:
    limit = _int_field(request, "limit") or 20
    query = _field(request, "query", max_chars=MAX_FIELD_CHARS)
    rows = store.list_secrets(query=query, limit=max(1, min(limit, 100)))
    return {
        "type": "list",
        "rows": [
            {
                "name": row.name,
                "platform": row.platform,
                "status": row.status,
                "preview": row.preview(_mask_char()),
                "tags": list(row.tags),
                "updated_at": (row.updated_at or "")[:19],
            }
            for row in rows
        ],
    }


def _copy(store, request, *, backend=None) -> dict:
    from kv.capture.clipboard import Win32Clipboard
    from kv.capture.wipe import copy_secret

    name = _field(request, "name", required=True, max_chars=120)
    result = copy_secret(store, name, backend or Win32Clipboard(),
                         ttl=30, actor="host")
    return {"type": "copy", "ok": True, "ttl": result.ttl,
            "preview": result.preview, "excluded_history": result.excluded_from_history}


# ---------------------------------------------------------------- 内部件

def _item_from_row(store, row) -> PendingItem:
    """inbox 行 → PendingItem（pending_from_inbox 的单行版，context 还原
    hostnames 供「记住这个选择」按最窄范围持久化）。"""
    value = store.protector.unprotect(bytes(row["value_blob"]))
    context = _loads(row["context_json"])
    candidate = Candidate(
        value=value, kind=row["kind"], key_name=row["key_name"],
        source=row["source"], span="inbox",
        hostnames=tuple(context.get("hostnames", ())),
        context_hostnames=tuple(context.get("context_hostnames", ())),
    )
    return reviewops.PendingItem(
        candidate=candidate,
        verdict=Verdict(
            platform=row["platform"], confidence=row["confidence"],
            evidence=row["evidence"],
            candidates=tuple(_loads(row["candidates_json"])),
        ),
        captured_at=row["captured_at"], inbox_id=int(row["id"]),
    )


def _capture_context(item: PendingItem, source_url: str, title: str) -> dict:
    """随行上下文：approve 时 source_url 落进记录的 source_url 列（明文列，
    guard_metadata 把关），title 只活在 context 里供排查。"""
    return {"source_url": source_url, "title": title}


def _loads(text: str) -> list | dict:
    try:
        loaded = json.loads(text or "[]")
    except ValueError:
        return []
    return loaded if isinstance(loaded, (list, dict)) else []


def _field(request: dict, key: str, *, required: bool = False,
           max_chars: int = MAX_FIELD_CHARS) -> str:
    value = request.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise errors.UsageError(f"字段 {key} 必须是字符串")
    if required and not value.strip():
        raise errors.UsageError(f"缺少字段 {key}")
    if len(value) > max_chars:
        raise errors.UsageError(f"字段 {key} 超长（>{max_chars} 字符）")
    return value


def _int_field(request: dict, key: str, *, required: bool = False) -> int | None:
    value = request.get(key)
    if value is None or value == "":
        if required:
            raise errors.UsageError(f"缺少字段 {key}")
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise errors.UsageError(f"字段 {key} 必须是整数")
    return value


def _mask_char() -> str:
    from kv import console

    return console.mask_char()
