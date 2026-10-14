"""密钥页仪表盘：指标卡行 + 平台分布环形图。

数据聚合是纯函数（collect_metrics / platform_distribution），渲染是
StatBar——refresh() 时 update(store) 重算重画，缩放时经 LISTENERS 重绘。
环形图用 Canvas arc + 粗描边画圆环段，零位图、零依赖。
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from typing import Iterable

from kv.gui import icons, theme
from kv.gui.controls import Card

# 环形图色板：主色打头，其余与语义色错开、饱和度接近
DONUT_COLORS = (theme.ACCENT, "#0e9f8a", "#d97706", "#8b5cf6", "#98a2b3")

STATUS_LABELS = {"active": "启用", "revoked": "已吊销", "expired": "已过期"}


@dataclass(frozen=True)
class Metrics:
    total: int
    active: int
    expiring: int
    flagged: int


def collect_metrics(rows: Iterable, expiring_names: Iterable[str]) -> Metrics:
    """行集 + 临期名单 → 四个指标。纯函数，rows 只读 status/name。"""
    rows = list(rows)
    expiring = set(expiring_names)
    active = [r for r in rows if r.status == "active"]
    return Metrics(
        total=len(rows),
        active=len(active),
        expiring=sum(1 for r in active if r.name in expiring),
        flagged=sum(1 for r in rows if r.status in ("revoked", "expired")),
    )


def platform_distribution(rows: Iterable, top: int = 4) -> list[tuple[str, int]]:
    """启用记录按平台计数，降序取前 N，其余并入「其他」。空表返回空。"""
    counts: dict[str, int] = {}
    for r in rows:
        if r.status == "active":
            counts[r.platform] = counts.get(r.platform, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    if len(ordered) <= top:
        return ordered
    rest = sum(n for _, n in ordered[top:])
    merged = ordered[:top]
    if rest:
        merged.append(("其他", rest))
    return merged


def _px(base: int) -> int:
    return max(round(base * theme.SCALE), 1)


class StatCard(Card):
    """一张指标卡：左侧语义色条 + 图标芯片 + 大数字 + 小标签。"""

    def __init__(self, parent, label: str, draw_icon, color: str) -> None:
        super().__init__(parent, height=_px(88), width=1)
        self._label = label
        self._draw_icon = draw_icon
        self._color = color
        self._value_var = tk.StringVar(value="0")
        self._build()
        theme.LISTENERS.append(self._rebuild)
        self.bind("<Destroy>", lambda _e: self._detach_listener())

    def _detach_listener(self) -> None:
        if self._rebuild in theme.LISTENERS:
            theme.LISTENERS.remove(self._rebuild)

    def set_value(self, value: int) -> None:
        self._value_var.set(str(value))

    def _rebuild(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        self.configure(height=_px(88))
        self._build()

    def _build(self) -> None:
        body = self.body
        strip = tk.Frame(body, bg=self._color, width=_px(4))
        strip.pack(side="left", fill="y")
        chip = tk.Canvas(
            body, width=_px(34), height=_px(34), bg=theme.SURFACE,
            highlightthickness=0, bd=0,
        )
        chip.pack(side="left", padx=(_px(10), 0), pady=(_px(12), 0))
        self._draw_icon(chip, _px(17), _px(17), _px(17), self._color)
        text = tk.Frame(body, bg=theme.SURFACE)
        text.pack(side="left", padx=(_px(8), 0), pady=(_px(10), 0))
        tk.Label(
            text, textvariable=self._value_var, font=theme.FONT_HEADING,
            bg=theme.SURFACE, fg=theme.TEXT,
        ).pack(anchor="w")
        tk.Label(
            text, text=self._label, font=theme.FONT_SMALL,
            bg=theme.SURFACE, fg=theme.TEXT_SECONDARY,
        ).pack(anchor="w")


class DonutCard(Card):
    """平台分布环形图：圆环段 + 图例（色点 + 平台 + 数量）。"""

    def __init__(self, parent) -> None:
        super().__init__(parent, height=_px(88), width=1)
        self._canvas = tk.Canvas(
            self.body, width=_px(64), height=_px(64), bg=theme.SURFACE,
            highlightthickness=0, bd=0,
        )
        self._legend = tk.Frame(self.body, bg=theme.SURFACE)
        theme.LISTENERS.append(self._rebuild)
        self.bind("<Destroy>", lambda _e: self._detach_listener())

    def _detach_listener(self) -> None:
        if self._rebuild in theme.LISTENERS:
            theme.LISTENERS.remove(self._rebuild)

    def set_segments(self, segments: list[tuple[str, int]]) -> None:
        self._segments = segments
        self._render()

    def _rebuild(self) -> None:
        self.configure(height=_px(88))
        self._render()

    def _render(self) -> None:
        segments = getattr(self, "_segments", [])
        for w in self.body.winfo_children():
            w.destroy()
        self._canvas = tk.Canvas(
            self.body, width=_px(64), height=_px(64), bg=theme.SURFACE,
            highlightthickness=0, bd=0,
        )
        self._canvas.pack(side="left", padx=(_px(8), 0), pady=_px(10))
        self._legend = tk.Frame(self.body, bg=theme.SURFACE)
        self._legend.pack(side="left", padx=(_px(6), 0), pady=_px(12), fill="x")
        self._draw_donut(segments)
        self._draw_legend(segments)

    def _draw_donut(self, segments: list[tuple[str, int]]) -> None:
        c = self._canvas
        size = _px(64)
        ring = _px(9)
        box = (_px(6), _px(6), size - _px(6), size - _px(6))
        total = sum(n for _, n in segments)
        if not segments or total == 0:
            c.create_arc(*box, start=0, extent=359.9,
                         outline=theme.BORDER, width=ring, style="arc")
            return
        start = 90.0
        for (_, count), color in zip(segments, DONUT_COLORS):
            extent = 360.0 * count / total
            c.create_arc(
                *box, start=start, extent=max(extent - 2, 1),
                outline=color, width=ring, style="arc",
            )
            start -= extent

    def _draw_legend(self, segments: list[tuple[str, int]]) -> None:
        for i, (label, count) in enumerate(segments[:5]):
            color = DONUT_COLORS[i % len(DONUT_COLORS)]
            row = tk.Frame(self._legend, bg=theme.SURFACE)
            row.pack(anchor="w", pady=1)
            dot = tk.Canvas(row, width=_px(8), height=_px(8),
                            bg=theme.SURFACE, highlightthickness=0, bd=0)
            dot.pack(side="left")
            dot.create_oval(1, 1, _px(7), _px(7), fill=color, outline=color)
            # 长平台名截断 —— 图例宽受卡片约束，Label 会居中裁掉两端，更难看
            shown = label if len(label) <= 10 else label[:9] + "…"
            tk.Label(
                row, text=shown, font=theme.FONT_SMALL, bg=theme.SURFACE,
                fg=theme.TEXT_SECONDARY,
            ).pack(side="left", padx=(_px(5), 0))
            tk.Label(
                row, text=str(count), font=theme.FONT_SMALL, bg=theme.SURFACE,
                fg=theme.TEXT,
            ).pack(side="left", padx=(_px(4), 0))


class StatBar(tk.Frame):
    """一行仪表盘：四张指标卡 + 分布环形图。update(store) 全量重算。"""

    def __init__(self, parent):
        super().__init__(parent, bg=theme.CANVAS)
        self._cards: list[StatCard] = []
        specs = (
            ("全部", icons.draw_layers, theme.TEXT, "total"),
            ("启用", icons.draw_key, theme.SUCCESS, "active"),
            ("30 天内到期", icons.draw_bolt, theme.WARNING, "expiring"),
            ("已吊销 / 过期", icons.draw_shield, theme.ERROR, "flagged"),
        )
        for label, draw, color, attr in specs:
            card = StatCard(self, label, draw, color)
            card.pack(side="left", fill="x", expand=True, padx=(0, theme.GAP))
            card._attr = attr  # noqa: SLF001 —— 行内规格表驱动，见 update()
            self._cards.append(card)
        self._donut = DonutCard(self)
        self._donut.pack(side="left", fill="x", expand=True)

    def update(self, store) -> None:  # noqa: A003 —— 与 Tk 习惯一致
        """从 store 重算指标并重画。store 为 None 时全归零。"""
        if store is None:
            for card in self._cards:
                card.set_value(0)
            self._donut.set_segments([])
            return
        from kv.ops.health import health_report

        rows = store.list_secrets()
        metrics = collect_metrics(rows, health_report(store).expiring)
        for card in self._cards:
            card.set_value(getattr(metrics, card._attr))
        self._donut.set_segments(platform_distribution(rows))
