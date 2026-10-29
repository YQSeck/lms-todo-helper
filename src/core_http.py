"""荔园畅课（TronClass）纯 HTTP 客户端：登录、抓待办、下附件。

认证链：lms.szu.edu.cn -> identity.szu.edu.cn(Keycloak) -> authserver.szu.edu.cn(金智 CAS)

登录密码加密（从页面 encrypt.js 拆解 + 真实请求验证）：
    password 字段 = base64( AES-128-CBC( key=盐, iv=盐, pkcs7 )( 64个随机字符 + 明文密码 ) )
    盐来自登录页 #pwdEncryptSalt（16 字符）。
    表单字段名：用户名 username，密码 password（页面上 id 是 saltPassword），
    另有 cllt=userNameLogin / dllt=generalLogin / execution / _eventId=submit。

会话复用优先级：本地保存的 cookie -> 浏览器 profile 里的 cookie -> 自动登录 -> 手动登录。
"""

from __future__ import annotations

import base64
import json
import random
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import core_config
import core_cred
import core_cookies

BASE = "https://lms.szu.edu.cn"
ENTRY = f"{BASE}/user/index"
AUTH_LOGIN = "https://authserver.szu.edu.cn/authserver/login"
TODOS = "/api/todos?no-intercept=true"
CST = timezone(timedelta(hours=8))
SESSION_PATH = core_config.DATA / "session.dat"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0")
AES_CHARS = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"
UNSAFE = re.compile(r'[\\/:*?"<>|\r\n\t]')


class LoginError(Exception):
    """登录失败。need_captcha=True 表示需要图形验证码（只能手动登录）。"""

    def __init__(self, msg: str, need_captcha: bool = False):
        super().__init__(msg)
        self.need_captcha = need_captcha


def _randstr(n: int) -> str:
    return "".join(random.choice(AES_CHARS) for _ in range(n))


def encrypt_password(pwd: str, salt: str) -> str:
    data = (_randstr(64) + pwd).encode("utf-8")
    pad = 16 - len(data) % 16
    data += bytes([pad]) * pad
    kb = salt.encode("utf-8")[:32]
    iv = salt.encode("utf-8")[:16]
    cipher = Cipher(algorithms.AES(kb), modes.CBC(iv))
    enc = cipher.encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode()


def safe_name(name: str, fallback: str = "unnamed") -> str:
    cleaned = UNSAFE.sub("_", (name or "").strip()).strip(" .")
    return (cleaned or fallback)[:120]


def parse_end_time(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(CST)
    except ValueError:
        return None


# ---- 作业说明（富文本 -> 纯文本）----
_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article",
    "blockquote", "pre", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt",
}


class _HtmlToText(HTMLParser):
    """保留段落换行、列表项目符号与链接地址，丢掉所有样式。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._depth = 0
        self._href: str | None = None
        self._link: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("ul", "ol"):
            self._depth += 1
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("  " * max(self._depth - 1, 0) + "· ")
        elif tag == "a":
            self._href = (dict(attrs).get("href") or "").strip() or None
            self._link = []
        elif tag == "img":
            self.parts.append("[图片]")
        elif tag == "br":
            self.parts.append("\n")
        # 其余块级标签只在结束时换行，避免相邻 <div> 之间多出空行

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "img":
            self.parts.append("[图片]")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("ul", "ol"):
            self._depth = max(0, self._depth - 1)
            self.parts.append("\n")
        elif tag == "a":
            text = "".join(self._link).strip()
            href = self._href or ""
            # 链接文字不是地址本身时，把地址补在括号里，免得丢了网盘链接
            if href and not href.startswith("javascript") and href not in text:
                self.parts.append(f"（{href}）")
            self._href = None
            self._link = []
        elif tag == "li":
            # 列表项内部常包着 <p>，已经换过行就不再补，避免列表项之间空一行
            if not (self.parts and self.parts[-1].endswith("\n")):
                self.parts.append("\n")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._link.append(data)


def html_to_text(html: str) -> str:
    """把作业说明的 HTML 转成可读纯文本；空说明返回空串。"""
    if not html or not html.strip():
        return ""
    try:
        parser = _HtmlToText()
        parser.feed(html)
        parser.close()
        raw = "".join(parser.parts)
    except Exception:
        raw = re.sub(r"<[^>]+>", " ", html)

    lines: list[str] = []
    for piece in raw.replace("\xa0", " ").split("\n"):
        line = re.sub(r"[ \t]+", " ", piece).strip()
        # 连续空行压成一行，段首空行丢掉
        if line or (lines and lines[-1]):
            lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines).strip()


def _extract_error(html: str) -> str:
    """从登录失败页里抠出提示文案。"""
    for box in ("showErrorTip", "pwdErrorTip", "nameErrorTip", "captchaErrorTip", "msg"):
        m = re.search(rf'id="{box}"[^>]*>(.{{0,200}}?)</(?:span|div)>', html, re.S)
        if not m:
            continue
        text = re.sub(r"<[^>]+>", "", m.group(1))
        text = re.sub(r"\s+", "", text).strip()
        if text:
            return text
    return ""


class LmsClient:
    def __init__(self, profile_dir: Path | None = None):
        self.profile = Path(profile_dir) if profile_dir else core_config.PROFILE_DIR
        self.http = requests.Session()
        self.http.headers.update({
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "application/json, text/plain, */*",
        })
        self.user = ""

    # ---------------- 会话 ----------------
    def set_cookies(self, cookies: dict[str, str]) -> int:
        n = 0
        for name, value in cookies.items():
            if not value or not value.isascii():
                continue
            for domain in (".szu.edu.cn", "lms.szu.edu.cn"):
                self.http.cookies.set(name, value, domain=domain)
            n += 1
        return n

    def save_session(self) -> None:
        try:
            payload = json.dumps({
                "cookies": requests.utils.dict_from_cookiejar(self.http.cookies),
                "at": datetime.now(CST).isoformat(timespec="seconds"),
            }).encode("utf-8")
            SESSION_PATH.write_bytes(core_cred.protect_bytes(payload))
        except Exception:
            pass

    def load_saved_session(self) -> int:
        if not SESSION_PATH.exists():
            return 0
        try:
            raw = json.loads(core_cred.unprotect_bytes(SESSION_PATH.read_bytes()).decode("utf-8"))
            return self.set_cookies(raw.get("cookies") or {})
        except Exception:
            return 0

    def load_profile_cookies(self) -> int:
        try:
            return self.set_cookies(core_cookies.read_cookies(self.profile))
        except Exception:
            return 0

    def probe(self) -> dict:
        """探 /api/todos，判断登录态是否有效。"""
        try:
            r = self.http.get(f"{BASE}{TODOS}", timeout=30, allow_redirects=True)
            if r.status_code == 200 and "todo_list" in r.text:
                return {"ok": True, "n": len(r.json().get("todo_list", []))}
            return {"ok": False, "status": r.status_code}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ---------------- 登录 ----------------
    def login(self, user: str, password: str, log=print) -> dict:
        log("  正在自动登录…")
        r = self.http.get(ENTRY, timeout=45, allow_redirects=True)
        if "authserver" not in r.url:
            # 已经是登录态
            res = self.probe()
            if res.get("ok"):
                return {"ok": True, "method": "session", "n": res.get("n", 0)}
            r = self.http.get(AUTH_LOGIN, timeout=45, allow_redirects=True)

        html, login_url = r.text, r.url
        salt_m = re.search(r'id="pwdEncryptSalt"[^>]*value="([^"]*)"', html)
        if not salt_m:
            raise LoginError("登录页结构变了，拿不到加密盐")
        salt = salt_m.group(1)

        execs = re.findall(r'name="execution"\s+value="([^"]*)"', html) or \
                re.findall(r'id="execution"[^>]*value="([^"]*)"', html)
        execution = execs[-1] if execs else ""

        data = {
            "username": user,
            "password": encrypt_password(password, salt),
            "captcha": "",
            "cllt": "userNameLogin",
            "dllt": "generalLogin",
            "lt": "",
            "execution": execution,
            "_eventId": "submit",
            "rmShown": "1",
        }
        resp = self.http.post(login_url, data=data, timeout=45, allow_redirects=True)

        # CAS 可能停在中转页（Keycloak broker），再探一次待办才算数
        for _ in range(3):
            res = self.probe()
            if res.get("ok"):
                self.save_session()
                return {"ok": True, "method": "auto", "n": res.get("n", 0)}
            if "authserver" not in resp.url and "identity" not in resp.url:
                break
            break

        tip = _extract_error(resp.text)
        if not tip and "authserver" in resp.url:
            tip = "登录未成功（服务端未跳转）"
        need_cap = any(k in tip for k in ("验证码", "动态码"))
        raise LoginError(tip or "密码错误或账号异常", need_captcha=need_cap)

    def manual_login(self, log=print, timeout: int = 600) -> dict:
        """启动系统 Edge 打开我们的 profile，等用户登录完关闭窗口，再读 cookie。"""
        edge = core_config.edge_path()
        if not edge:
            raise LoginError("没找到 Microsoft Edge，请先安装或手动把浏览器路径告诉我")
        self.profile.mkdir(parents=True, exist_ok=True)
        log("  已打开浏览器，请用学号密码登录（若出现验证码就填上）。登录成功后**关闭浏览器窗口**。")
        proc = subprocess.Popen([
            edge,
            f"--user-data-dir={self.profile}",
            "--no-first-run", "--no-default-browser-check",
            "--start-maximized", ENTRY,
        ])
        deadline = time.time() + timeout
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.6)
        if proc.poll() is None:
            proc.terminate()
            time.sleep(1.5)

        for attempt in range(6):
            self.http.cookies.clear()
            n = self.load_profile_cookies()
            res = self.probe()
            if res.get("ok"):
                self.save_session()
                return {"ok": True, "method": "manual", "n": res.get("n", 0)}
            if attempt < 5:
                time.sleep(1.0)
        raise LoginError("浏览器登录后仍读不到有效登录态（可能没登录成功，或窗口没关）")

    def ensure(self, log=print, allow_auto: bool = True) -> dict:
        """保证可用会话：本地会话 → profile cookie → 自动登录 → 手动登录。"""
        res = self.probe()
        if res.get("ok"):
            return {"ok": True, "method": "session", "n": res.get("n", 0)}

        n = self.load_saved_session()
        if n:
            res = self.probe()
            if res.get("ok"):
                return {"ok": True, "method": "saved", "n": res.get("n", 0)}

        n = self.load_profile_cookies()
        if n:
            res = self.probe()
            if res.get("ok"):
                self.save_session()
                return {"ok": True, "method": "profile", "n": res.get("n", 0)}

        if allow_auto:
            cred = core_cred.load()
            if cred:
                user, pwd = cred
                try:
                    out = self.login(user, pwd, log=log)
                    out["method"] = "auto"
                    return out
                except LoginError as exc:
                    log(f"  自动登录失败：{exc}")
                    if not exc.need_captcha:
                        # 密码可能改了，仍然允许手动登录兜底
                        log("  将改为手动登录。")

        out = self.manual_login(log=log)
        return out

    # ---------------- 数据 ----------------
    def fetch_json(self, path: str) -> dict | None:
        url = path if path.startswith("http") else f"{BASE}{path}"
        r = self.http.get(url, timeout=45)
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    def fetch_todos(self) -> list[dict]:
        body = self.fetch_json(TODOS)
        if not isinstance(body, dict):
            raise LoginError("取待办失败（接口返回异常，可能登录态已失效）")
        items = []
        for t in body.get("todo_list", []):
            deadline = parse_end_time(t.get("end_time", ""))
            items.append({
                "id": t.get("id"),
                "course_id": t.get("course_id"),
                "course_name": t.get("course_name", ""),
                "title": t.get("title", ""),
                "type": t.get("type", ""),
                "deadline": deadline.isoformat() if deadline else None,
                "attachments": [],
                "url": f"{BASE}/course/{t.get('course_id')}/learning-activity#/{t.get('id')}",
            })
        items.sort(key=lambda x: (x["deadline"] is None, x["deadline"] or ""))
        return items

    def list_attachments(self, activity_id) -> list[dict]:
        body = self.fetch_json(f"/api/activities/{activity_id}/upload_references")
        refs = body.get("references", []) if isinstance(body, dict) else []
        out = []
        for r in refs:
            up = r.get("upload") or {}
            if not up.get("id") or up.get("deleted"):
                continue
            if up.get("allow_download") is False:
                continue
            out.append({
                "upload_id": up["id"],
                "name": up.get("name") or r.get("name") or f"file_{up['id']}",
                "size": up.get("size", 0),
            })
        return out

    def fetch_description(self, activity_id) -> str:
        """取作业说明原文（富文本 HTML）。没有说明返回空串。"""
        body = self.fetch_json(f"/api/activities/{activity_id}")
        if not isinstance(body, dict):
            return ""
        data = body.get("data") or {}
        desc = data.get("description") or ""
        return desc if isinstance(desc, str) else ""

    def download(self, upload_id, dest: Path) -> bool:
        body = self.fetch_json(f"/api/uploads/{upload_id}/url")
        url = body.get("url") if isinstance(body, dict) else None
        if not url:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with self.http.get(url, timeout=180, stream=True, allow_redirects=True) as r:
            if r.status_code != 200:
                return False
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(65536):
                    if chunk:
                        fh.write(chunk)
        tmp.replace(dest)
        return True
