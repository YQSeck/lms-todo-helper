"""直接读取系统 Edge / Chromium profile 里的 cookie（Windows DPAPI + AES-GCM）。

用途：手动登录时启动系统 Edge 指向我们的 profile，用户登完关窗后，
这里把 session cookie 取出来，后续全部用普通 HTTP 请求完成 —— 不需要任何浏览器驱动。

格式说明（实测本机 Edge 156）：
  Local State → os_crypt.encrypted_key = "DPAPI" + DPAPI(key)
  Cookies.encrypted_value       = b"v10" + nonce(12) + AES-256-GCM(明文 + tag)
  明文前面可能带 32 字节 SHA256(host_key) 做域绑定，需去掉。
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import shutil
import sqlite3
import tempfile
from ctypes import wintypes
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)


class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_unprotect(blob: bytes) -> bytes:
    buf = ctypes.create_string_buffer(blob, len(blob))
    pin = _BLOB(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    pout = _BLOB()
    if not _crypt32.CryptUnprotectData(
        ctypes.byref(pin), None, None, None, None, 0, ctypes.byref(pout)
    ):
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData 失败")
    try:
        return ctypes.string_at(pout.pbData, pout.cbData)
    finally:
        _k32.LocalFree(pout.pbData)


def local_state_key(profile: Path) -> bytes:
    data = json.loads((Path(profile) / "Local State").read_text(encoding="utf-8"))
    enc = base64.b64decode(data["os_crypt"]["encrypted_key"])
    if enc[:5] != b"DPAPI":
        raise ValueError("cookie 用的是 app-bound 加密，暂不支持")
    return _dpapi_unprotect(enc[5:])


def _decrypt_value(value: bytes, key: bytes, host: str) -> str:
    if value[:3] not in (b"v10", b"v11"):
        return _dpapi_unprotect(value).decode("utf-8", "replace")
    nonce, payload = value[3:15], value[15:]
    plain = AESGCM(key).decrypt(nonce, payload, None)
    if len(plain) > 32 and plain[:32] == hashlib.sha256(host.encode()).digest():
        plain = plain[32:]
    return plain.decode("utf-8", "replace")


def read_cookies(profile: Path, hosts: tuple[str, ...] = ("szu.edu.cn",)) -> dict[str, str]:
    """返回 {cookie名: 值}。取不到（profile 不存在/浏览器没退出/格式不支持）时返回空 dict。"""
    profile = Path(profile)
    out: dict[str, str] = {}
    ck = profile / "Default" / "Network" / "Cookies"
    if not ck.exists():
        ck = profile / "Default" / "Cookies"
    if not ck.exists() or not (profile / "Local State").exists():
        return out

    tmp = Path(tempfile.mkdtemp()) / "c.db"
    try:
        shutil.copy2(ck, tmp)
        con = sqlite3.connect(tmp)
        try:
            rows = con.execute(
                "select host_key, name, encrypted_value, value from cookies"
            ).fetchall()
        finally:
            con.close()
    except Exception:
        return out

    key = None
    for host, name, enc, raw in rows:
        if not any(h in host for h in hosts):
            continue
        if raw:
            out[name] = raw
            continue
        if not enc:
            continue
        if key is None:
            try:
                key = local_state_key(profile)
            except Exception:
                return {}
        try:
            out[name] = _decrypt_value(enc, key, host)
        except Exception:
            continue
    return out
