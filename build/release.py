"""打发布包：exe + 运行库 + 使用说明，不带源码和个人数据。

    .venv\\Scripts\\python.exe build\\release.py

产物落在项目根目录：

    荔园畅课助手-v1.2-win64.zip
      荔园畅课助手/
        荔园畅课助手.exe
        _internal/            运行库
        使用说明.txt

刻意不打包的东西：
    data/     学号、加密密码、登录态、抓下来的表格与附件 —— 全是个人数据
    src/      源码（发布包里只放能跑的成品）
    build/    .venv/  构建环境与中间产物

zip 内文件名用 UTF-8 存（zipfile 默认行为），Win10/11 自带解压能正确显示中文。
"""

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "荔园畅课助手"
EXE = ROOT / f"{NAME}.exe"
INTERNAL = ROOT / "_internal"
DOC = ROOT / "使用说明.txt"


def version() -> str:
    """版本号只认 app.py 里的那份，避免两处维护对不上。"""
    try:
        txt = (ROOT / "src" / "app.py").read_text(encoding="utf-8")
    except OSError:
        return "0.0"
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', txt, re.M)
    return m.group(1) if m else "0.0"


def exe_locked() -> bool:
    """程序运行时 exe 被占用，这时打出来的包可能是残的。"""
    if not EXE.exists():
        return False
    try:
        with open(EXE, "r+b"):
            return False
    except OSError:
        return True


def main() -> int:
    missing = [p.name for p in (EXE, INTERNAL, DOC) if not p.exists()]
    if missing:
        print(f"缺文件：{'、'.join(missing)}。先跑 build\\rebuild.py 打包。")
        return 1
    if exe_locked():
        print(f"「{NAME}.exe」正在运行，请先关掉再打包。")
        return 2

    ver = version()
    out = ROOT / f"{NAME}-v{ver}-win64.zip"
    if out.exists():
        out.unlink()

    top = NAME                      # 压缩包里的顶层文件夹
    count = 0
    print(f"打包 v{ver} …")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(EXE, f"{top}/{EXE.name}")
        count += 1
        for f in INTERNAL.rglob("*"):
            if f.is_file():
                z.write(f, f"{top}/_internal/{f.relative_to(INTERNAL).as_posix()}")
                count += 1
        z.write(DOC, f"{top}/{DOC.name}")
        count += 1

    size = out.stat().st_size / 1048576
    print(f"完成：{out}")
    print(f"      {count} 个文件 · {size:.1f} MB")

    # 自查：个人数据和源码一个都不能溜进去
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    bad = [n for n in names
           if "/data/" in n or n.endswith(("settings.json", "cred.dat", "session.dat",
                                           "荔园畅课待办.xlsx"))
           or "/src/" in n or ".venv" in n or "/附件/" in n]
    if bad:
        print(f"包里混进了不该有的东西：{bad[:5]}")
        return 3
    print("自查通过：不含 data/、src/、.venv/、附件/。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
