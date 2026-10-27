#!/usr/bin/env python3
"""netspeed GUI — speed test + manually configured AI summary."""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from engine import (DEFAULT_CONFIG, config_path, format_report, list_models,
                    llm_summary, load_config, run_all, save_config, test_api)

BG = "#14181d"
FG = "#e6edf3"
MUTED = "#8b949e"
ACCENT = "#2f81f7"
OK = "#3fb950"
ERR = "#f85149"
CARD = "#0d1117"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("netspeed · 节点测速")
        self.geometry("980x760")
        self.minsize(820, 620)
        self.configure(bg=BG)
        self.cfg = load_config()
        self.report = None
        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self.worker = None
        self._build()
        self.after(100, self._poll)

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=16, pady=(12, 2))
        tk.Label(header, text="netspeed", bg=BG, fg=FG, font=("Segoe UI", 18, "bold")).pack(side="left")
        tk.Label(header, text="真实字节 · 失败不隐藏 · 节点延迟/吞吐", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left", padx=10, pady=(6, 0))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12, pady=8)
        self.tab_test = tk.Frame(self.nb, bg=BG)
        self.tab_api = tk.Frame(self.nb, bg=BG)
        self.nb.add(self.tab_test, text="测速")
        self.nb.add(self.tab_api, text="API 设置")
        self._build_test()
        self._build_api()

    def _build_test(self) -> None:
        bar = tk.Frame(self.tab_test, bg=BG)
        bar.pack(fill="x", pady=6)
        self.start_btn = tk.Button(bar, text="开始测速", command=self.start, bg=ACCENT, fg="white",
                                   activebackground="#1f6feb", relief="flat",
                                   font=("Segoe UI", 10, "bold"), padx=16, pady=5, cursor="hand2")
        self.start_btn.pack(side="left")
        self.save_btn = tk.Button(bar, text="保存 JSON", command=self.save, bg="#21262d", fg=FG,
                                  activebackground="#30363d", relief="flat", padx=12, pady=5,
                                  state="disabled", cursor="hand2")
        self.save_btn.pack(side="left", padx=6)
        self.status = tk.Label(bar, text="就绪", bg=BG, fg=MUTED, font=("Consolas", 9))
        self.status.pack(side="right")

        self.progress = ttk.Progressbar(self.tab_test, maximum=100)
        self.progress.pack(fill="x", pady=(2, 6))

        wrap = tk.Frame(self.tab_test, bg=BG)
        wrap.pack(fill="both", expand=True)
        self.text = tk.Text(wrap, bg=CARD, fg=FG, insertbackground=FG, relief="flat",
                            font=("Consolas", 9), wrap="none", padx=10, pady=8)
        self.text.pack(fill="both", expand=True, side="left")
        scroll = tk.Scrollbar(wrap, command=self.text.yview, relief="flat")
        scroll.pack(side="right", fill="y")
        self.text.configure(yscrollcommand=scroll.set)
        self.text.tag_configure("ok", foreground=OK)
        self.text.tag_configure("err", foreground=ERR)
        self.text.tag_configure("muted", foreground=MUTED)
        self._append("提示: 测速会消耗真实流量；平台节点无公开大文件时只测延迟，不编造下载速度。\n", "muted")
        self._append("AI 总结需要在「API 设置」里手动填写本地或云端 API。\n\n", "muted")

    def _build_api(self) -> None:
        pad = {"padx": 10, "pady": 5}
        row = tk.Frame(self.tab_api, bg=BG)
        row.pack(fill="x", **pad)
        self.api_enabled = tk.BooleanVar(value=bool(self.cfg["api"].get("enabled")))
        tk.Checkbutton(row, text="启用 AI 总结", variable=self.api_enabled, bg=BG, fg=FG,
                       selectcolor=BG, activebackground=BG, activeforeground=FG).pack(side="left")

        mode_row = tk.Frame(self.tab_api, bg=BG)
        mode_row.pack(fill="x", **pad)
        self.mode = tk.StringVar(value=self.cfg["api"].get("mode") or "local")
        tk.Label(mode_row, text="类型", bg=BG, fg=MUTED, width=10, anchor="w").pack(side="left")
        tk.Radiobutton(mode_row, text="本地模型（Ollama 等）", value="local", variable=self.mode,
                       bg=BG, fg=FG, selectcolor=BG, activebackground=BG, activeforeground=FG,
                       command=self._mode_changed).pack(side="left")
        tk.Radiobutton(mode_row, text="云端 API", value="cloud", variable=self.mode,
                       bg=BG, fg=FG, selectcolor=BG, activebackground=BG, activeforeground=FG,
                       command=self._mode_changed).pack(side="left", padx=8)

        self.base_var = tk.StringVar(value=self.cfg["api"].get("base_url") or "")
        self.key_var = tk.StringVar(value=self.cfg["api"].get("api_key") or "")
        self.model_var = tk.StringVar(value=self.cfg["api"].get("model") or "")

        self._field("Base URL", self.base_var, 0)
        self._field("API Key", self.key_var, 1, show="*")
        self._field("模型名", self.model_var, 2)

        hint = ("本地示例: http://127.0.0.1:11434/v1  key 填 ollama  model 填本地模型名\n"
                "云端示例: https://api.openai.com/v1 或 https://api.deepseek.com/v1  key/model 按服务商填\n"
                "两者都必须是 OpenAI 兼容的 /chat/completions 接口；不填就不会调用 AI。")
        tk.Label(self.tab_api, text=hint, bg=BG, fg=MUTED, justify="left", anchor="w").pack(fill="x", padx=12, pady=6)

        btns = tk.Frame(self.tab_api, bg=BG)
        btns.pack(fill="x", padx=10, pady=10)
        tk.Button(btns, text="测试连接", command=self.test_conn, bg="#21262d", fg=FG,
                  relief="flat", padx=12, pady=5, cursor="hand2").pack(side="left")
        tk.Button(btns, text="测试总结", command=self.test_summary, bg="#21262d", fg=FG,
                  relief="flat", padx=12, pady=5, cursor="hand2").pack(side="left", padx=6)
        tk.Button(btns, text="保存设置", command=self.save_api, bg=ACCENT, fg="white",
                  relief="flat", padx=12, pady=5, cursor="hand2").pack(side="left", padx=6)
        self.api_status = tk.Label(self.tab_api, text=f"配置文件: {config_path()}", bg=BG, fg=MUTED)
        self.api_status.pack(fill="x", padx=12, pady=4)

    def _field(self, label: str, var: tk.StringVar, row: int, show: str = "") -> None:
        f = tk.Frame(self.tab_api, bg=BG)
        f.pack(fill="x", padx=10, pady=3)
        tk.Label(f, text=label, bg=BG, fg=MUTED, width=10, anchor="w").pack(side="left")
        tk.Entry(f, textvariable=var, bg=CARD, fg=FG, insertbackground=FG, relief="flat",
                 show=show, width=80).pack(side="left", fill="x", expand=True)

    def _mode_changed(self) -> None:
        if self.mode.get() == "local":
            if not self.base_var.get().strip():
                self.base_var.set("http://127.0.0.1:11434/v1")
            if not self.key_var.get().strip():
                self.key_var.set("ollama")
        else:
            if self.base_var.get().startswith("http://127.0.0.1"):
                self.base_var.set("")

    # ------------------------------------------------------------------ actions
    def save_api(self) -> None:
        self.cfg["api"].update({
            "enabled": bool(self.api_enabled.get()),
            "mode": self.mode.get(),
            "base_url": self.base_var.get().strip(),
            "api_key": self.key_var.get().strip(),
            "model": self.model_var.get().strip(),
        })
        try:
            save_config(self.cfg)
            self.api_status.configure(text=f"已保存: {config_path()}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("netspeed", f"保存失败: {exc}")

    def test_conn(self) -> None:
        self.save_api()
        names, err = list_models(self.cfg)
        if err:
            self.api_status.configure(text="连接失败: " + err)
            return
        if not self.model_var.get().strip() and names:
            chat = [n for n in names if "embed" not in n.lower()]
            self.model_var.set((chat or names)[0])
            self.save_api()
        self.api_status.configure(text="连接成功，模型: " + (", ".join(names[:8]) if names else "(未返回列表)"))

    def test_summary(self) -> None:
        self.save_api()
        dummy = {
            "latency": [{"target": "test", "loss_pct": 0, "ttfb_ms": {"median": 20.0, "jitter": 1.0}}],
            "downloads": [{"target": "test", "category": "general", "mbps": 100.0, "megabytes": 50,
                           "seconds": 4, "streams": 2, "failed_streams": 0, "errors": [], "note": ""}],
            "upload": {"mbps": 20.0},
        }
        text, err = llm_summary(self.cfg, dummy)
        if err:
            self.api_status.configure(text="总结失败: " + err)
            messagebox.showerror("netspeed", f"总结失败:\n{err}")
        elif text:
            self.api_status.configure(text="总结成功")
            messagebox.showinfo("netspeed", text)
        else:
            self.api_status.configure(text="未启用 AI 总结（请先勾选并填写 API）")

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.save_api()
        self.report = None
        self.save_btn.configure(state="disabled")
        self.start_btn.configure(state="disabled", text="测速中…")
        self.progress["value"] = 0
        self.text.delete("1.0", "end")
        self._append("开始测速，请保持网络空闲…\n\n", "muted")

        def progress(phase: str, pct: float) -> None:
            self.queue.put(("progress", phase, pct))

        def job() -> None:
            try:
                self.queue.put(("done", run_all(self.cfg, progress)))
            except Exception as exc:  # noqa: BLE001
                self.queue.put(("error", str(exc)))

        self.worker = threading.Thread(target=job, daemon=True)
        self.worker.start()

    def _poll(self) -> None:
        try:
            while True:
                item = self.queue.get_nowait()
                if item[0] == "progress":
                    _, phase, pct = item
                    self.progress["value"] = pct
                    self.status.configure(text=f"{phase}  {pct:.0f}%")
                elif item[0] == "done":
                    self._finish(item[1])
                elif item[0] == "error":
                    self._append(f"\n测速失败: {item[1]}\n", "err")
                    self.status.configure(text="失败")
                    self.start_btn.configure(state="normal", text="开始测速")
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _finish(self, report: dict) -> None:
        self.report = report
        self.progress["value"] = 100
        self.status.configure(text="完成")
        self.start_btn.configure(state="normal", text="重新测速")
        self.save_btn.configure(state="normal")
        self._append(format_report(report) + "\n")

    def save(self) -> None:
        if not self.report:
            return
        path = filedialog.asksaveasfilename(title="保存测速报告", defaultextension=".json",
                                            filetypes=[("JSON", "*.json")],
                                            initialfile=f"netspeed-{time.strftime('%Y%m%d-%H%M%S')}.json")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.report, fh, ensure_ascii=False, indent=2)
            messagebox.showinfo("netspeed", f"已保存:\n{os.path.abspath(path)}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("netspeed", f"保存失败: {exc}")

    def _append(self, s: str, tag: str = "") -> None:
        self.text.insert("end", s, tag)
        self.text.see("end")


def main() -> None:
    try:
        App().mainloop()
    except Exception:
        import traceback
        base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
        try:
            with open(os.path.join(base, "crash.log"), "w", encoding="utf-8") as fh:
                fh.write(traceback.format_exc())
        except Exception:
            pass
        try:
            messagebox.showerror("netspeed", "启动失败，详情见 crash.log")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
