"""密钥详情弹窗：卡片布局（徽章 + 分区字段网格 + 操作历史）。

从 secrets 页拆出——它只依赖取好的数据（row/fps/history），不依赖页面。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from kv.gui import theme
from kv.gui.controls import RoundButton, SlimScrollbar

def detail_rows(row, fps: list, history: list) -> list[tuple[str, str, list]]:
    """详情内容组织为 (区块标题, 字段列表, 尾注) 的顺序结构。"""
    fields = [
        ("平台", row.platform), ("状态", row.status), ("置信度", row.confidence),
        ("种类", row.kind), ("键名", row.key_name or "—"), ("预览", row.preview("*")),
        ("标签", ", ".join(row.tags) or "—"), ("别名", ", ".join(row.aliases) or "—"),
        ("备注", row.note or "—"), ("来源", f"{row.origin} / {row.source_url or '—'}"),
        ("创建", (row.created_at or "")[:19]), ("更新", (row.updated_at or "")[:19]),
        ("过期", row.expires_at or "—"), ("最后使用", (row.last_used_at or "")[:19] or "—"),
        ("值长度", str(row.value_len)), ("sha256", row.sha256),
    ]
    sections = [("基本信息", fields, "")]
    if row.evidence:
        sections.append(("判定依据", [("证据", row.evidence)], ""))
    if fps:
        lines = [f"{fp['sha256'][:16]}  {fp['status']}  {fp['first_seen_at'][:19]}" for fp in fps]
        sections.append(("指纹历史", [(f"#{i + 1}", line) for i, line in enumerate(lines)], ""))
    if history:
        lines = [f"{e['ts'][:19]}  {e['event']}  {e['actor']}" for e in history]
        sections.append(("操作历史", [(f"#{i + 1}", line) for i, line in enumerate(lines)],
                         "完整轨迹见 安全 → 审计日志"))
    return sections


def build_detail_window(row, fps: list, history: list) -> None:
    import tkinter as tk
    from tkinter import ttk

    from kv.gui import theme
    from kv.gui.controls import RoundButton, SlimScrollbar

    win = tk.Toplevel()
    win.title(f"详情 — {row.name}")
    win.geometry("640x640")
    win.transient()
    win.configure(bg=theme.SURFACE)

    head = tk.Frame(win, bg=theme.SURFACE)
    head.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 0))
    tk.Label(head, text=row.name, font=theme.FONT_HEADING,
             bg=theme.SURFACE, fg=theme.TEXT).pack(side="left")
    badge = tk.Label(
        head, text=f" {row.platform} · {row.status} ", font=theme.FONT_SUBTITLE,
        bg=theme.ACCENT_SOFT, fg=theme.ACCENT,
    )
    badge.pack(side="left", padx=(theme.PAD, 0))

    body = tk.Canvas(win, bg=theme.SURFACE, highlightthickness=0, bd=0)
    body.pack(fill="both", expand=True, padx=theme.PAD, pady=(theme.GAP, 0))
    inner = tk.Frame(body, bg=theme.SURFACE)
    win_id = body.create_window(0, 0, window=inner, anchor="nw")

    grid = ttk.Frame(inner, style="TFrame", padding=(0, theme.PAD_SM, 0, 0))
    grid.pack(fill="x")
    for section, fields, tail in detail_rows(row, fps, history):
        ttk.Label(inner, text=section, style="Sub.TLabel").pack(
            anchor="w", padx=1, pady=(theme.GAP, theme.PAD_SM // 2),
        )
        sep = ttk.Separator(inner, orient="horizontal")
        sep.pack(fill="x", pady=(0, theme.PAD_SM // 2))
        for i, (label, value) in enumerate(fields):
            ttk.Label(grid, text=label, style="Muted.TLabel").grid(
                row=i, column=0, sticky="nw", padx=(0, theme.GAP), pady=2,
            )
            tk.Label(
                grid, text=str(value), font=theme.FONT_MONO, bg=theme.SURFACE,
                fg=theme.TEXT, justify="left", wraplength=380,
            ).grid(row=i, column=1, sticky="w", pady=2)
        grid = ttk.Frame(inner, style="TFrame", padding=(0, 0, 0, 0))
        grid.pack(fill="x")
        if tail:
            ttk.Label(inner, text=tail, style="Muted.TLabel").pack(anchor="w", padx=1)

    def _sync(_event=None) -> None:
        body.configure(scrollregion=body.bbox("all"))
        body.itemconfigure(win_id, width=body.winfo_width() - 4)

    inner.bind("<Configure>", _sync)
    SlimScrollbar(win, body).pack(side="right", fill="y", before=body)
    bar = tk.Frame(win, bg=theme.SURFACE)
    bar.pack(fill="x", pady=theme.PAD)
    RoundButton(bar, "关闭", win.destroy).pack()
