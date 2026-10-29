"""便携配置：全部数据都在程序所在目录的 data/ 下，整个文件夹拷走即可用。

目录约定（打包后 exe 同级）：
    程序目录/
      data/settings.json   设置（学号、保存位置等）
      data/cred.dat        密码（DPAPI 加密，仅本机当前 Windows 账户可解）
      data/profile/        浏览器 profile（手动登录时用）
      data/附件/           默认附件目录
      data/荔园畅课待办.xlsx 默认表格
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path


def app_dir() -> Path:
    """程序所在目录（打包后为 exe 所在目录，开发时为项目根）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    d = app_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


DATA = data_dir()
SETTINGS_PATH = DATA / "settings.json"
CRED_PATH = DATA / "cred.dat"
PROFILE_DIR = DATA / "profile"
SHEET_NAME = "荔园畅课待办.xlsx"


@dataclass
class Settings:
    student_id: str = ""
    save_dir: str = ""
    attach_enabled: bool = True
    attach_dir: str = ""          # 空 = 保存位置/附件
    remember_password: bool = True
    last_run: str = ""
    last_summary: str = ""

    # ---------- 派生路径 ----------
    @property
    def sheet_path(self) -> Path:
        base = Path(self.save_dir) if self.save_dir else DATA
        return base / SHEET_NAME

    @property
    def attach_path(self) -> Path:
        if self.attach_dir:
            return Path(self.attach_dir)
        base = Path(self.save_dir) if self.save_dir else DATA
        return base / "附件"

    # ---------- 读写 ----------
    @classmethod
    def load(cls) -> "Settings":
        if not SETTINGS_PATH.exists():
            return cls()
        try:
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return cls()
        s = cls()
        for key, val in (raw or {}).items():
            if hasattr(s, key):
                setattr(s, key, val)
        return s

    def save(self) -> Path:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return SETTINGS_PATH

    def is_ready(self) -> bool:
        """至少填了学号。"""
        return bool(self.student_id.strip())


def edge_path() -> str | None:
    """找系统 Edge 可执行文件。"""
    cands = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for c in cands:
        if c and Path(c).exists():
            return c
    return None
