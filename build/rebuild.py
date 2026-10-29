"""重新打包（改完 src/ 里的代码后跑一次）。

    .venv\\Scripts\\python.exe build\\rebuild.py

成品直接落在项目根目录，和 data\\ 同级：

    荔园畅课助手.exe
    _internal\\          运行库（不要删）
    data\\              学号密码、表格、附件（程序自己建）
    使用说明.txt
    src\\ build\\ .venv\\ 源码与构建环境（日常不用管）

为什么这样放：程序按「exe 所在目录」找 data，双层目录会让配置和程序分家，
      双击 dist 里的 exe 等于换了台新电脑，得重填一次学号密码。

注意：程序开着时打包会失败，脚本会先检测并提示，不会写坏产物。
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYI = ROOT / ".venv" / "Scripts" / "pyinstaller.exe"
ICON = ROOT / "build" / "app.ico"
TMP = ROOT / "build" / "dist_tmp"          # PyInstaller 的临时输出，搬完就删
NAME = "荔园畅课助手"

CMD = [
    str(PYI), "--noconfirm", "--windowed", "--onedir",
    "--name", NAME,
    "--icon", str(ICON),
    "--paths", str(ROOT / "src"),
    "--distpath", str(TMP),
    "--workpath", str(ROOT / "build" / "pyi"),
    "--specpath", str(ROOT / "build"),
    "--exclude-module", "pytest",
    "--exclude-module", "numpy",
    "--exclude-module", "PIL",
    str(ROOT / "src" / "app.py"),
]

EXE = ROOT / f"{NAME}.exe"
INTERNAL = ROOT / "_internal"


def exe_locked() -> bool:
    """exe 正在运行（被占用）时返回 True。"""
    if not EXE.exists():
        return False
    try:
        with open(EXE, "r+b"):
            return False
    except OSError:
        return True


def install(built: Path) -> None:
    """把 PyInstaller 的产物摆到项目根目录。"""
    shutil.copy2(built / f"{NAME}.exe", EXE)
    if INTERNAL.exists():
        shutil.rmtree(INTERNAL)
    shutil.move(str(built / "_internal"), str(INTERNAL))


def main() -> int:
    if not PYI.exists():
        print(f"找不到 PyInstaller：{PYI}")
        return 1
    if exe_locked():
        print("程序正在运行，exe 被占用。请先关掉「荔园畅课助手.exe」再打包。")
        return 2

    print("打包中…")
    subprocess.run(CMD, check=True, cwd=ROOT)

    built = TMP / NAME
    install(built)
    shutil.rmtree(TMP, ignore_errors=True)

    size = sum(f.stat().st_size for f in INTERNAL.rglob("*") if f.is_file())
    size = (size + EXE.stat().st_size) / 1048576
    print(f"完成：{EXE}  (exe + _internal 共 {size:.0f} MB)")
    print(f"data 仍在 {ROOT / 'data'}，配置不受影响。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
