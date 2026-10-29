"""密码存储：Windows DPAPI 加密。

加密后的文件只有**本机当前 Windows 账户**能解开，拷到别的电脑或别的账户下都无效。
密码不写入 settings.json，也不写进日志。
"""

from __future__ import annotations

import ctypes
import json
from ctypes import wintypes
from pathlib import Path

from core_config import CRED_PATH

DESC = "lms-todo-credential"


class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)


def _blob(data: bytes) -> _BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _protect(data: bytes) -> bytes:
    out = _BLOB()
    desc = ctypes.c_wchar_p(DESC)
    ok = _crypt32.CryptProtectData(
        ctypes.byref(_blob(data)), desc, None, None, None, 0, ctypes.byref(out)
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptProtectData 失败")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _k32.LocalFree(out.pbData)


def _unprotect(data: bytes) -> bytes:
    out = _BLOB()
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(_blob(data)), None, None, None, None, 0, ctypes.byref(out)
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData 失败")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _k32.LocalFree(out.pbData)


def save(user: str, password: str, path: Path = CRED_PATH) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"u": user, "p": password}, ensure_ascii=False).encode("utf-8")
    path.write_bytes(_protect(payload))
    return path


def load(path: Path = CRED_PATH) -> tuple[str, str] | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        raw = json.loads(_unprotect(path.read_bytes()).decode("utf-8"))
        u, p = raw.get("u", ""), raw.get("p", "")
        return (u, p) if u and p else None
    except Exception:
        return None


def clear(path: Path = CRED_PATH) -> None:
    Path(path).unlink(missing_ok=True)


def exists(path: Path = CRED_PATH) -> bool:
    return Path(path).exists()


# 供其他模块加密任意本地数据（如会话 cookie）复用
def protect_bytes(data: bytes) -> bytes:
    return _protect(data)


def unprotect_bytes(data: bytes) -> bytes:
    return _unprotect(data)
