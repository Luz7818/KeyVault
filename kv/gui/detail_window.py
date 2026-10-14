"""密钥详情弹窗：卡片布局（徽章 + 分区字段网格 + 操作历史）。

从 secrets 页拆出——它只依赖取好的数据（row/fps/history），不依赖页面。
sha256 / 掩码预览可点击复制（非敏感值；明文永远走 copy 命令的归零窗口）。
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


BADGE_STYLES = {
    "active": (theme.ACCENT_SOFT, theme.ACCENT),
    "revoked": (theme.ERROR_SOFT, theme.ERROR),
    "expired": ("#fdf0e0", theme.WARNING),
}
COPYABLE = ("sha256", "预览")  # 点击可复制的字段：都是非敏感值


def _fill_sections(inner: tk.Widget, row, fps: list, history: list,
                   on_copy) -> None:
    """把 detail_rows 的分区渲染成 标题 + 分隔线 + 两列字段网格。"""
    grid = ttk.Frame(inner, style="TFrame", padding=(0, theme.PAD_SM, 0, 0))
    grid.pack(fill="x")
    for section, fields, tail in detail_rows(row, fps, history):
        ttk.Label(inner, text=section, style="Sub.TLabel").pack(
            anchor="w", padx=1, pady=(theme.GAP, theme.PAD_SM // 2),
        )
        ttk.Separator(inner, orient="horizontal").pack(
            fill="x", pady=(0, theme.PAD_SM // 2),
        )
        for i, (label, value) in enumerate(fields):
            ttk.Label(grid, text=label, style="Muted.TLabel").grid(
                row=i, column=0, sticky="nw", padx=(0, theme.GAP), pady=2,
            )
            copyable = label in COPYABLE
            lbl = tk.Label(
                grid, text=str(value), font=theme.FONT_MONO, bg=theme.SURFACE,
                fg=theme.TEXT, justify="left", wraplength=380,
                cursor="hand2" if copyable else "",
            )
            lbl.grid(row=i, column=1, sticky="w", pady=2)
            if copyable:
                lbl.bind("<Button-1>",
                         lambda _e, w=label, t=str(value): on_copy(w, t))
        grid = ttk.Frame(inner, style="TFrame")
        grid.pack(fill="x")
        if tail:
            ttk.Label(inner, text=tail, style="Muted.TLabel").pack(anchor="w", padx=1)


def _build_bottom_bar(win: tk.Toplevel) -> tk.Label:
    """底栏：关闭按钮 + 复制反馈标签 + 操作提示。返回反馈标签供 _copy 写。"""
    bar = tk.Frame(win, bg=theme.SURFACE)
    bar.pack(fill="x", pady=theme.PAD)
    feedback = tk.Label(bar, text="", font=theme.FONT_SMALL, bg=theme.SURFACE,
                        fg=theme.SUCCESS)
    tk.Label(bar, text="点击 sha256 / 预览可复制（Esc 关闭）", font=theme.FONT_SMALL,
             bg=theme.SURFACE, fg=theme.TEXT_TERTIARY).pack(
        side="left", padx=(theme.PAD, 0))
    feedback.pack(side="left", padx=(theme.PAD, 0))
    RoundButton(bar, "关闭", win.destroy).pack(side="right", padx=(0, theme.PAD))
    return feedback


def build_detail_window(row, fps: list, history: list) -> None:
    win = tk.Toplevel()
    win.title(f"详情 — {row.name}")
    win.geometry("640x640")
    win.transient()
    win.configure(bg=theme.SURFACE)
    win.bind("<Escape>", lambda _e: win.destroy())

    head = tk.Frame(win, bg=theme.SURFACE)
    head.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 0))
    tk.Label(head, text=row.name, font=theme.FONT_HEADING,
             bg=theme.SURFACE, fg=theme.TEXT).pack(side="left")
    badge_bg, badge_fg = BADGE_STYLES.get(row.status, (theme.ACCENT_SOFT, theme.ACCENT))
    tk.Label(
        head, text=f" {row.platform} · {row.status} ", font=theme.FONT_SUBTITLE,
        bg=badge_bg, fg=badge_fg,
    ).pack(side="left", padx=(theme.PAD, 0))

    feedback = _build_bottom_bar(win)

    from kv.capture.clipboard import Win32Clipboard

    def _copy_value(what: str, text: str) -> None:
        written = Win32Clipboard().write(text, exclude_history=True)
        if written:
            feedback.config(text=f"已复制{what}", fg=theme.SUCCESS)
        else:
            feedback.config(text="复制失败：剪切板被占用", fg=theme.ERROR)

    body = tk.Canvas(win, bg=theme.SURFACE, highlightthickness=0, bd=0)
    body.pack(fill="both", expand=True, padx=theme.PAD, pady=(theme.GAP, 0))
    inner = tk.Frame(body, bg=theme.SURFACE)
    win_id = body.create_window(0, 0, window=inner, anchor="nw")
    _fill_sections(inner, row, fps, history, _copy_value)

    def _sync(_event=None) -> None:
        body.configure(scrollregion=body.bbox("all"))
        body.itemconfigure(win_id, width=body.winfo_width() - 4)

    inner.bind("<Configure>", _sync)
    SlimScrollbar(win, body).pack(side="right", fill="y", before=body)
