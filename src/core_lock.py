"""查谁占着表格，并在必要时释放占用。

为什么需要这个：WorkBuddy 自带的表格预览内核（editor_sdk.exe）打开 xlsx 后会一直持有
只读共享锁 —— 表现为「我没打开 Excel，但文件被占用」。本模块负责识别占用方，
并且只自动关闭这个纯只读的预览内核；Excel / WPS 这类可能带未保存修改的一律不动。

纯 ctypes 实现，不依赖第三方库。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes as wt
from pathlib import Path

# WorkBuddy 表格预览内核：纯只读渲染，关掉不丢数据（预览面板下次点开会自己重开）
PREVIEW_PROCS = {"editor_sdk.exe"}

# 可能带未保存修改的办公软件：只提示，不自动关
OFFICE_PROCS = {
    "excel.exe", "et.exe", "wps.exe", "wpp.exe", "ksolaunch.exe",
    "wpsoffice.exe", "winword.exe", "powerpnt.exe",
}

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_TERMINATE = 0x0001
ERROR_MORE_DATA = 234
CCH_RM_MAX_APP_NAME = 255
CCH_RM_MAX_SVC_NAME = 63

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


def _iter_procs() -> list[tuple[int, str]]:
    """枚举所有进程的 (pid, 映像名)。"""
    out: list[tuple[int, str]] = []
    _k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    _k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    _k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _k32.CloseHandle.argtypes = [wt.HANDLE]

    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == wt.HANDLE(-1).value or not snap:
        return out
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = _k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append((int(entry.th32ProcessID), entry.szExeFile))
            ok = _k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _k32.CloseHandle(snap)
    return out


def _terminate(pid: int) -> bool:
    _k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    _k32.OpenProcess.restype = wt.HANDLE
    _k32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    h = _k32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not h:
        return False
    try:
        return bool(_k32.TerminateProcess(h, 1))
    finally:
        _k32.CloseHandle(h)


def is_writable(path: Path) -> bool:
    """能否以写方式打开（不截断内容）。"""
    try:
        with open(path, "r+b"):
            return True
    except OSError:
        return False


# --------------------------- Restart Manager ---------------------------

class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]


class RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", wt.DWORD), ("ProcessStartTime", FILETIME)]


class RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [
        ("Process", RM_UNIQUE_PROCESS),
        ("strAppName", wt.WCHAR * (CCH_RM_MAX_APP_NAME + 1)),
        ("strServiceShortName", wt.WCHAR * (CCH_RM_MAX_SVC_NAME + 1)),
        ("ApplicationType", ctypes.c_int),
        ("AppStatus", wt.ULONG),
        ("TSSessionId", wt.DWORD),
        ("bRestartable", wt.BOOL),
    ]


def _rm_holders(path: Path) -> list[tuple[int, str]]:
    """用 Restart Manager 精确查占用方（可能返回空，某些进程不注册句柄）。"""
    try:
        rm = ctypes.WinDLL("rstrtmgr")
        rm.RmStartSession.argtypes = [ctypes.POINTER(wt.DWORD), wt.DWORD, ctypes.c_wchar_p]
        rm.RmStartSession.restype = wt.DWORD
        rm.RmRegisterResources.argtypes = [
            wt.DWORD, wt.UINT, ctypes.POINTER(ctypes.c_wchar_p),
            wt.UINT, ctypes.c_void_p, wt.UINT, ctypes.c_void_p,
        ]
        rm.RmRegisterResources.restype = wt.DWORD
        rm.RmGetList.argtypes = [
            wt.DWORD, ctypes.POINTER(wt.UINT), ctypes.POINTER(wt.UINT),
            ctypes.POINTER(RM_PROCESS_INFO), ctypes.POINTER(wt.DWORD),
        ]
        rm.RmGetList.restype = wt.DWORD
        rm.RmEndSession.argtypes = [wt.DWORD]
    except OSError:
        return []

    session = wt.DWORD()
    key = ctypes.create_unicode_buffer(257)
    if rm.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return []
    try:
        files = (ctypes.c_wchar_p * 1)(str(path))
        if rm.RmRegisterResources(session, 1, files, 0, None, 0, None) != 0:
            return []
        need = wt.UINT(0)
        count = wt.UINT(0)
        rm.RmGetList(session, ctypes.byref(need), ctypes.byref(count), None, None)
        if not need.value:
            return []
        arr = (RM_PROCESS_INFO * need.value)()
        count = wt.UINT(need.value)
        if rm.RmGetList(session, ctypes.byref(need), ctypes.byref(count), arr, None) != 0:
            return []
        return [(int(arr[i].Process.dwProcessId), arr[i].strAppName.strip())
                for i in range(count.value)]
    finally:
        rm.RmEndSession(session)


# ----------------------------- 对外接口 -----------------------------

def who_locks(path: Path) -> list[tuple[int, str]]:
    """返回可能占着文件的 [(pid, 进程名)]。识别不出来时返回空列表。"""
    path = Path(path)
    by_rm = _rm_holders(path)
    if by_rm:
        return by_rm
    # RM 对某些进程无效（实测 editor_sdk 就不注册），退化成按进程名筛
    known = PREVIEW_PROCS | OFFICE_PROCS
    return [(pid, name) for pid, name in _iter_procs() if name.lower() in known]


def release(path: Path, log=print, close_preview: bool = True,
            wait: float = 4.0) -> str | None:
    """确保 path 可写。成功返回 None，失败返回原因。

    close_preview=False 时不自动关预览内核（只报告占用方）。
    """
    path = Path(path)
    if not path.exists() or is_writable(path):
        return None

    holders = who_locks(path)

    if close_preview:
        closed = []
        for pid, name in holders:
            if name.lower() in PREVIEW_PROCS:
                if _terminate(pid):
                    closed.append(f"{name}(PID {pid})")
        if closed:
            log(f"  表格被 WorkBuddy 预览占用，已关闭预览内核释放: {', '.join(closed)}")
            deadline = time.time() + wait
            while time.time() < deadline:
                if is_writable(path):
                    return None
                time.sleep(0.15)

    if is_writable(path):
        return None

    office = sorted({name for _, name in holders if name.lower() in OFFICE_PROCS})
    if office:
        return f"{'、'.join(office)} 正打开着 {path.name}，请关闭后再重跑"
    if holders:
        names = sorted({name for _, name in holders})
        return f"{path.name} 被 {'、'.join(names)} 占用，请稍后重试"
    return f"{path.name} 被未知程序占用（可能是杀毒软件瞬时扫描），请几秒后重跑"
