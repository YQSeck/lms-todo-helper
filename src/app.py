"""荔园畅课待办助手 —— 图形界面主程序。

一个窗口里完成：填学号密码、选保存位置、点一下开始、看日志。
所有数据和配置都存在程序目录的 data/ 下，整个文件夹拷走就能用。
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import core_config
import core_cred
import core_http
import core_runner

APP_TITLE = "荔园畅课待办助手"
VERSION = "1.1"

# 打包成无控制台程序后 stdout 为 None，任何 print 都会炸，这里兜个底
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

try:  # 高分屏不要糊
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE}  v{VERSION}")
        self.geometry("780x660")
        self.minsize(700, 580)

        self.settings = core_config.Settings.load()
        self.logq: queue.Queue = queue.Queue()
        self.busy = False
        self.last_result: dict | None = None

        self._init_style()
        self._build()
        self._load_ui()
        self.after(120, self._drain)

        if not self.settings.is_ready():
            self.log("首次使用：请填写学号和密码，选好保存位置，然后点「开始」。")
            self.ent_user.focus_set()
        else:
            self.log(f"就绪 · 表格位置 {self.settings.sheet_path}", "dim")
            if not self.ent_pwd.get():
                self.log("密码没有保存，本次需要重新输入（勾选「记住密码」可自动填）。", "dim")

    # ---------------- 样式 ----------------
    def _init_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("vista")
        except Exception:
            pass
        base = ("Microsoft YaHei UI", 9)
        st.configure(".", font=base)
        st.configure("Hint.TLabel", foreground="#777777")
        st.configure("Title.TLabel", font=("Microsoft YaHei UI", 14, "bold"))
        st.configure("Sub.TLabel", foreground="#666666")
        st.configure("Ok.TLabel", foreground="#1a7f37")
        st.configure("Err.TLabel", foreground="#c62828")

    # ---------------- 界面 ----------------
    def _build(self):
        pad = {"padx": 16}

        head = ttk.Frame(self)
        head.pack(fill="x", pady=(14, 6), **pad)
        ttk.Label(head, text="荔园畅课待办助手", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="  抓待办 · 按 DDL 排序 · 存附件与作业说明 · 维护同一份表格",
                  style="Sub.TLabel").pack(side="left", pady=(6, 0))

        # --- 设置 ---
        box = ttk.LabelFrame(self, text=" 设置 ")
        box.pack(fill="x", pady=6, **pad)
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="学号").grid(row=0, column=0, sticky="e", padx=(14, 8), pady=(12, 4))
        self.ent_user = ttk.Entry(box, width=28)
        self.ent_user.grid(row=0, column=1, sticky="w", pady=(12, 4))

        ttk.Label(box, text="密码").grid(row=1, column=0, sticky="e", padx=(14, 8), pady=4)
        self.ent_pwd = ttk.Entry(box, width=28, show="•")
        self.ent_pwd.grid(row=1, column=1, sticky="w", pady=4)
        self.var_remember = tk.BooleanVar(value=self.settings.remember_password)
        ttk.Checkbutton(box, text="记住密码（本机加密保存）",
                        variable=self.var_remember).grid(row=1, column=2, sticky="w", padx=8)

        ttk.Label(box, text="保存位置").grid(row=2, column=0, sticky="e", padx=(14, 8), pady=4)
        self.ent_dir = ttk.Entry(box, width=52)
        self.ent_dir.grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Button(box, text="浏览…", width=8, command=self._pick_dir).grid(
            row=2, column=2, sticky="w", padx=8)

        ttk.Label(box, text="附件").grid(row=3, column=0, sticky="e", padx=(14, 8), pady=4)
        self.var_attach = tk.BooleanVar(value=self.settings.attach_enabled)
        ttk.Checkbutton(box, text="下载附件与作业说明（存到「保存位置\\附件」，说明存成 txt）",
                        variable=self.var_attach).grid(row=3, column=1, sticky="w", pady=(4, 12))

        # --- 按钮 ---
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(6, 4), **pad)
        self.btn_run = tk.Button(
            bar, text="开始", command=self._on_run, width=10, height=2,
            bg="#1a73e8", fg="white", activebackground="#1667cf", activeforeground="white",
            relief="flat", font=("Microsoft YaHei UI", 10, "bold"), cursor="hand2",
        )
        self.btn_run.pack(side="left")
        ttk.Button(bar, text="保存设置", width=10, command=self._on_save).pack(side="left", padx=6)
        ttk.Button(bar, text="手动登录", width=10, command=self._on_manual).pack(side="left")
        ttk.Button(bar, text="打开表格", width=10, command=self._open_sheet).pack(side="right")
        ttk.Button(bar, text="打开文件夹", width=10, command=self._open_dir).pack(side="right", padx=6)

        # --- 日志 ---
        logbox = ttk.LabelFrame(self, text=" 日志 ")
        logbox.pack(fill="both", expand=True, pady=6, **pad)
        wrap = ttk.Frame(logbox)
        wrap.pack(fill="both", expand=True, padx=8, pady=8)
        self.txt = tk.Text(wrap, height=14, wrap="word", relief="flat", borderwidth=0,
                           bg="#fbfbfb", fg="#222222", font=("Microsoft YaHei UI", 9),
                           padx=8, pady=6, state="disabled")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.txt.tag_configure("err", foreground="#c62828")
        self.txt.tag_configure("ok", foreground="#1a7f37")
        self.txt.tag_configure("dim", foreground="#888888")

        # --- 状态栏 ---
        self.status = ttk.Label(self, text="就绪", anchor="w")
        self.status.pack(fill="x", padx=16, pady=(0, 10))

    # ---------------- 数据 <-> 界面 ----------------
    def _load_ui(self):
        self.ent_user.insert(0, self.settings.student_id)
        self.ent_dir.insert(0, self.settings.save_dir or str(core_config.DATA))
        if self.settings.student_id:
            cred = core_cred.load()
            if cred and cred[0] == self.settings.student_id:
                self.ent_pwd.insert(0, cred[1])
        self._refresh_status()

    def _collect(self) -> core_config.Settings | None:
        user = self.ent_user.get().strip()
        pwd = self.ent_pwd.get()
        save_dir = self.ent_dir.get().strip()

        if not user:
            messagebox.showwarning("还差一步", "请先填写学号。")
            self.ent_user.focus_set()
            return None
        if not pwd:
            messagebox.showwarning("还差一步", "请先填写密码。")
            self.ent_pwd.focus_set()
            return None
        if not save_dir:
            save_dir = str(core_config.DATA)
            self.ent_dir.insert(0, save_dir)
        try:
            os.makedirs(save_dir, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("保存位置不可用", f"{save_dir}\n\n{exc}")
            return None

        s = self.settings
        s.student_id = user
        s.save_dir = save_dir
        s.attach_enabled = bool(self.var_attach.get())
        s.remember_password = bool(self.var_remember.get())
        return s

    def _persist(self, s: core_config.Settings, password: str) -> None:
        s.save()
        if s.remember_password and password:
            try:
                core_cred.save(s.student_id, password)
            except Exception as exc:
                self.log(f"密码保存失败：{exc}", "err")
        elif not s.remember_password:
            core_cred.clear()

    # ---------------- 日志 ----------------
    def log(self, msg: str, tag: str | None = None):
        self.logq.put(("log", (msg, tag)))

    def _append(self, msg: str, tag: str | None = None):
        self.txt.configure(state="normal")
        self.txt.insert("end", time.strftime("%H:%M:%S  ") + str(msg) + "\n", tag or ())
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _drain(self):
        try:
            while True:
                kind, payload = self.logq.get_nowait()
                if kind == "log":
                    msg, tag = payload
                    self._append(msg, tag)
                elif kind == "done":
                    self._on_done(payload)
        except queue.Empty:
            pass
        self.after(120, self._drain)

    # ---------------- 动作 ----------------
    def _set_busy(self, busy: bool):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_run.configure(state=state,
                               bg="#9bbdf0" if busy else "#1a73e8",
                               cursor="watch" if busy else "hand2")

    def _on_run(self):
        if self.busy:
            return
        s = self._collect()
        if not s:
            return
        self._persist(s, self.ent_pwd.get())
        self._set_busy(True)
        self.status.configure(text="正在执行…")

        def worker():
            try:
                res = core_runner.run_update(s, log=self.log)
            except Exception as exc:
                res = {"error": f"意外错误：{exc}"}
            self.logq.put(("done", res))

        threading.Thread(target=worker, daemon=True).start()

    def _on_done(self, res: dict):
        self._set_busy(False)
        if res.get("error"):
            self.log(f"失败：{res['error']}", "err")
            self.status.configure(text="执行失败")
            if res.get("need_captcha"):
                if messagebox.askyesno(
                    "需要验证码",
                    "学校系统这次要求输图形验证码，无法自动登录。\n\n"
                    "要现在打开浏览器手动登录一次吗？（登录成功后关闭浏览器窗口即可）"
                ):
                    self._on_manual()
            return
        self.last_result = res
        self._refresh_status()

    def _on_save(self):
        s = self._collect()
        if not s:
            return
        self._persist(s, self.ent_pwd.get())
        self.log(f"设置已保存：{core_config.SETTINGS_PATH}", "dim")
        self._refresh_status()

    def _on_manual(self):
        if self.busy:
            return
        s = self._collect()
        if not s:
            return
        self._persist(s, self.ent_pwd.get())
        self._set_busy(True)
        self.status.configure(text="等待浏览器登录…")
        self.log("即将打开浏览器，登录完成后请关闭浏览器窗口。")

        def worker():
            client = core_http.LmsClient()
            try:
                out = client.manual_login(log=self.log)
                res = {"ok": True, "manual": out}
            except Exception as exc:
                res = {"error": str(exc)}
            self.logq.put(("done", res))

        threading.Thread(target=worker, daemon=True).start()

    def _pick_dir(self):
        cur = self.ent_dir.get().strip() or str(core_config.DATA)
        picked = filedialog.askdirectory(title="选择保存位置", initialdir=cur)
        if picked:
            self.ent_dir.delete(0, "end")
            self.ent_dir.insert(0, picked)

    def _open_sheet(self):
        s = self.settings
        path = s.sheet_path
        if not path.exists():
            messagebox.showinfo("还没有表格", "还没生成表格，先点「开始」。")
            return
        os.startfile(str(path))  # noqa: S606

    def _open_dir(self):
        s = self.settings
        target = s.save_dir or str(core_config.DATA)
        try:
            os.startfile(target)  # noqa: S606
        except Exception as exc:
            messagebox.showerror("打不开", f"{target}\n\n{exc}")

    def _refresh_status(self):
        raw = self.settings.last_run
        if not raw:
            self.status.configure(text="就绪")
            return
        stamp = raw.replace("T", " ")
        try:
            from datetime import datetime
            stamp = datetime.fromisoformat(raw).strftime("%m-%d %H:%M")
        except Exception:
            pass
        self.status.configure(text=f"上次执行 {stamp} · {self.settings.last_summary}")


def _crash_hook(exc_type, exc, tb):
    """无控制台运行时，异常要落盘 + 弹窗，否则用户只看到窗口消失。"""
    import traceback

    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log_path = None
    try:
        log_path = core_config.DATA / "error.log"
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("\n===== %Y-%m-%d %H:%M:%S =====\n"))
            fh.write(text)
    except Exception:
        log_path = None
    try:
        messagebox.showerror(
            "程序出错了",
            f"{exc_type.__name__}: {exc}\n\n"
            + (f"详细信息已写入：\n{log_path}" if log_path else "无法写入日志文件"),
        )
    except Exception:
        pass


def main() -> int:
    sys.excepthook = _crash_hook
    app = App()
    app.report_callback_exception = _crash_hook
    if "--run" in sys.argv:
        # 供计划任务/快捷方式调用：启动后自动跑一次
        app.after(500, app._on_run)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
