"""截取指定标题窗口（用于检查界面布局）。纯 ctypes + GDI。"""
import os
import struct
import sys
import tempfile
import time
from ctypes import wintypes as wt

u32 = __import__("ctypes").windll.user32
g32 = __import__("ctypes").windll.gdi32


def find_window(keyword: str):
    hits = []

    @__import__("ctypes").WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _):
        if u32.IsWindowVisible(h):
            n = u32.GetWindowTextLengthW(h)
            if n:
                buf = __import__("ctypes").create_unicode_buffer(n + 1)
                u32.GetWindowTextW(h, buf, n + 1)
                if keyword in buf.value:
                    hits.append((h, buf.value))
        return True

    u32.EnumWindows(cb, 0)
    return hits


class RECT(__import__("ctypes").Structure):
    _fields_ = [("l", wt.LONG), ("t", wt.LONG), ("r", wt.LONG), ("b", wt.LONG)]


def capture(hwnd, out: str) -> str:
    u32.SetForegroundWindow(hwnd)
    time.sleep(0.4)
    rect = RECT()
    u32.GetWindowRect(hwnd, __import__("ctypes").byref(rect))
    w, h = rect.r - rect.l, rect.b - rect.t
    hdc = u32.GetWindowDC(hwnd)
    mdc = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    g32.SelectObject(mdc, bmp)
    g32.BitBlt(mdc, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)

    class BITMAPINFOHEADER(__import__("ctypes").Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                    ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    bi = BITMAPINFOHEADER()
    bi.biSize = 40
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0
    stride = w * 4
    size = stride * h
    buf = __import__("ctypes").create_string_buffer(size)
    g32.GetDIBits(mdc, bmp, 0, h, buf, __import__("ctypes").byref(bi), 0)

    with open(out, "wb") as f:
        f.write(b"BM" + struct.pack("<IHHI", size + 54, 0, 0, 54))
        f.write(struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, size, 0, 0, 0, 0))
        f.write(buf.raw)
    g32.DeleteObject(bmp)
    g32.DeleteDC(mdc)
    u32.ReleaseDC(hwnd, hdc)
    return f"{out}  {w}x{h}"


if __name__ == "__main__":
    kw = sys.argv[1] if len(sys.argv) > 1 else "荔园畅课"
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(tempfile.gettempdir(), "gui.bmp")
    hits = find_window(kw)
    if not hits:
        print("没找到窗口")
        raise SystemExit(1)
    for h, t in hits:
        print("窗口:", t)
    print(capture(hits[0][0], out))
