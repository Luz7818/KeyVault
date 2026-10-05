"""检测页：文本/文件检测 + 规则自测。"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from kv.gui import theme
from kv.gui.widgets import DataTable

DET_COLS = ("platform", "confidence", "preview", "evidence")
DET_HEADS = ("平台", "置信度", "预览", "依据")
DET_WIDTHS = (120, 80, 200, 240)


class DetectPage(ttk.Frame):
    def __init__(self, parent: tk.Widget, *, app) -> None:
        super().__init__(parent, padding=theme.PAD)
        self._app = app
        self._build()

    def _build(self) -> None:
        ttk.Label(self, text="检测", font=theme.FONT_HEADING).pack(anchor="w")
        input_frame = ttk.LabelFrame(self, text="输入文本", padding=theme.PAD_SM)
        input_frame.pack(fill="x", pady=(theme.GAP, 0))
        self._text = tk.Text(input_frame, height=6, font=theme.FONT_MONO, wrap="word")
        self._text.pack(fill="x")
        bar = ttk.Frame(input_frame)
        bar.pack(fill="x", pady=(theme.GAP, 0))
        ttk.Button(bar, text="检测文本", command=self._detect_text).pack(side="left")
        ttk.Button(bar, text="检测文件", command=self._detect_file).pack(side="left", padx=theme.GAP)
        ttk.Button(bar, text="清空", command=lambda: self._text.delete("1.0", "end")).pack(side="left")
        self._table = DataTable(self, DET_COLS, headings=DET_HEADS, widths=DET_WIDTHS)
        self._table.pack(fill="both", expand=True, pady=(theme.GAP, 0))
        st_frame = ttk.LabelFrame(self, text="规则自测", padding=theme.PAD_SM)
        st_frame.pack(fill="x", pady=(theme.GAP, 0))
        st_bar = ttk.Frame(st_frame)
        st_bar.pack(fill="x")
        ttk.Button(st_bar, text="运行 selftest --golden", command=self._run_selftest).pack(side="left")
        self._gate_label = ttk.Label(st_bar, text="", font=theme.FONT_SMALL)
        self._gate_label.pack(side="left", padx=theme.PAD)

    def on_show(self) -> None:
        self._update_gate()

    def _update_gate(self) -> None:
        from kv.selftest import gate_is_current

        store = self._app.store
        if store is None:
            self._gate_label.config(text="vault 未初始化")
            return
        ok = gate_is_current(store)
        self._gate_label.config(
            text="Gate: 通过" if ok else "Gate: 未通过（scan 会被拒绝）",
            foreground=theme.SUCCESS if ok else theme.WARNING,
        )

    def _detect_text(self) -> None:
        text = self._text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("提示", "请输入要检测的文本。", parent=self)
            return
        self._app.set_status("检测中…")
        self._app.run_async(_do_detect, text, on_done=self._show_results)

    def _detect_file(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="选择文件")
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception as exc:
            messagebox.showerror("读取失败", str(exc), parent=self)
            return
        self._text.delete("1.0", "end")
        self._text.insert("1.0", text[:4096])
        self._app.set_status("检测中…")
        self._app.run_async(_do_detect, text, on_done=self._show_results)

    def _show_results(self, results: list[dict[str, Any]]) -> None:
        self._table.clear()
        for r in results:
            self._table.insert_row((r["platform"], r["confidence"], r["preview"], r["evidence"]))
        self._app.set_status(f"检测到 {len(results)} 个候选", color=theme.SUCCESS if results else "")

    def _run_selftest(self) -> None:
        from kv.selftest import run_golden

        self._app.set_status("运行金标自测…")
        self._app.run_async(run_golden, on_done=self._show_selftest)

    def _show_selftest(self, result) -> None:
        self._update_gate()
        if result.ok:
            self._app.set_status(
                f"selftest 通过：{result.passed}/{result.total}", color=theme.SUCCESS,
            )
            messagebox.showinfo(
                "selftest", f"全部通过：{result.passed}/{result.total}", parent=self,
            )
        else:
            fails = "\n".join(f.case_id for f in result.failures[:10])
            self._app.set_status(
                f"selftest 失败：{result.passed}/{result.total}", color=theme.ERROR,
            )
            messagebox.showerror("selftest 失败", f"失败用例：\n{fails}", parent=self)


def _do_detect(text: str) -> list[dict[str, Any]]:
    from kv.detect import report, resolve
    from kv.detect.corrections import CorrectionSet
    from kv.parse import pipeline

    parsed = pipeline.parse(text, source_hint="gui")
    results: list[dict[str, Any]] = []
    for cand in parsed.candidates:
        verdict = resolve.resolve(cand, CorrectionSet.empty())
        results.append({
            "platform": verdict.platform,
            "confidence": verdict.confidence,
            "preview": cand.preview("*"),
            "evidence": report.summarize(verdict),
        })
    return results
