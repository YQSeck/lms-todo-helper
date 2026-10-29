"""一次完整抓取：登录 -> 抓待办 -> 下附件 / 存作业说明 -> 维护表格。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import core_config
import core_http
import core_sheet
from core_http import CST, LoginError, safe_name


METHOD_LABEL = {
    "session": "登录态有效",
    "saved": "本地会话",
    "profile": "浏览器登录态",
    "auto": "自动登录",
    "manual": "手动登录",
}

DESC_FILE = "作业说明.txt"                    # 作业说明落成的文件名，放在每个作业自己的文件夹里
DESC_STATE = core_config.DATA / "desc_state.json"   # 说明正文指纹：内容没变就不重写


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _desc_file_text(item: dict, text: str, now: datetime) -> str:
    """txt 内容：先一段来源信息，空行分隔，然后说明正文。"""
    deadline = datetime.fromisoformat(item["deadline"]) if item.get("deadline") else None
    head = [
        item.get("title", ""),
        f"课程：{item.get('course_name', '')}",
        f"截止：{deadline:%Y-%m-%d %H:%M}" if deadline else "截止：—",
        f"抓取：{now:%Y-%m-%d %H:%M}",
        f"来源：{item.get('url', '')}",
        "-" * 46,
        "",
    ]
    return "\n".join(head) + text + "\n"


def _write_desc(dest: Path, item: dict, text: str, now: datetime, state: dict) -> bool:
    """写作业说明 txt；正文没变且文件还在就跳过。返回是否真的写入。"""
    key = str(item.get("id"))
    fingerprint = hashlib.sha1(text.encode("utf-8")).hexdigest()
    if dest.exists() and state.get(key) == fingerprint:
        return False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_desc_file_text(item, text, now), encoding="utf-8")
    except Exception:
        return False
    state[key] = fingerprint
    return True


def run_update(settings: core_config.Settings, log=print, should_stop=lambda: False) -> dict:
    """执行一次抓取，返回摘要。异常统一以 {"error": ...} 形式返回，不抛给调用方。"""
    now = datetime.now(CST)
    log(f"开始执行 · {now:%Y-%m-%d %H:%M}")

    sheet = settings.sheet_path
    try:
        sheet.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return {"error": f"保存位置不可用：{exc}"}

    client = core_http.LmsClient()
    try:
        sess = client.ensure(log=log)
    except LoginError as exc:
        return {"error": str(exc), "need_captcha": exc.need_captcha}
    except Exception as exc:
        return {"error": f"登录环节出错：{exc}"}

    log(f"登录方式：{METHOD_LABEL.get(sess.get('method'), sess.get('method'))}"
        f"（待办 {sess.get('n', 0)} 条）")

    try:
        items = client.fetch_todos()
    except Exception as exc:
        return {"error": f"抓取待办失败：{exc}"}
    log(f"取到 {len(items)} 条待办")

    downloaded, failed = [], []
    desc_written = 0
    kept = 0
    if settings.attach_enabled:
        state = _load_json(DESC_STATE)
        targets = [it for it in items if it.get("type") == "homework"]
        for idx, item in enumerate(targets, 1):
            if should_stop():
                log("已取消")
                break
            dest_dir = (settings.attach_path
                        / safe_name(item["course_name"], str(item["course_id"]))
                        / safe_name(item["title"], str(item["id"])))
            # 这个实验已经有文件夹、而且里面确实有东西，说明以前处理过：
            # 里面可能已经放了写好的作业，一律不下载、不覆盖，只把附件名单列进表格。
            # 空目录（上次建完就失败了）不算，会照常补齐。
            try:
                already = dest_dir.is_dir() and any(dest_dir.iterdir())
            except OSError:
                already = True

            try:
                atts = client.list_attachments(item["id"])
            except Exception:
                atts = []
            try:
                desc_text = core_http.html_to_text(client.fetch_description(item["id"]))
            except Exception:
                desc_text = ""

            if not atts and not desc_text:
                item["attachments"] = []
                continue

            if not already:
                for att in atts:
                    name = safe_name(att["name"], f"file_{att['upload_id']}")
                    if name.lower() == DESC_FILE.lower():
                        name = f"附件_{name}"   # 别和作业说明文件撞名，免得互相覆盖
                        att["name"] = name
                    dest = dest_dir / name
                    ok = False
                    try:
                        ok = client.download(att["upload_id"], dest)
                    except Exception as exc:
                        att["error"] = str(exc)
                    if ok:
                        att["path"] = str(dest)
                        downloaded.append(dest.name)
                    else:
                        att.setdefault("error", "下载失败")
                        failed.append(att["name"])
            else:
                kept += 1

            # 作业说明是我们自己生成的文件，老师改过就更新，不算覆盖别人的作业
            if desc_text:
                dest = dest_dir / DESC_FILE
                if _write_desc(dest, item, desc_text, now, state):
                    desc_written += 1
                atts.append({
                    "upload_id": f"desc_{item['id']}",
                    "name": DESC_FILE,
                    "path": str(dest),
                    "is_desc": True,
                })
            item["attachments"] = atts

            files = len([a for a in atts if not a.get("is_desc")])
            tags = []
            if files:
                tags.append(f"{files} 个附件")
            if desc_text:
                tags.append("含说明")
            if already:
                tags.append("已有文件夹，未下载")
            log(f"  附件 {idx}/{len(targets)}：{item['title']}"
                + (f"（{'、'.join(tags)}）" if tags else ""))
        _save_json(DESC_STATE, state)
    else:
        old = core_sheet.read_existing(sheet)
        for item in items:
            prev = old.get(core_sheet._key(item))
            item["attachments_skipped"] = True
            if prev:
                item["old_att_count"] = prev["att_count"]
                item["old_att_files"] = prev["att_files"]

    rep = core_sheet.update_sheet(sheet, items, now, log=log)
    if rep.get("error"):
        return {"error": rep["error"], "total": len(items)}

    summary = (f"{len(items)} 条待办 · 新增 {len(rep['added'])} · 移除 {len(rep['removed'])}")
    if settings.attach_enabled:
        if downloaded:
            summary += f" · 新下载附件 {len(downloaded)} 个"
        if kept:
            summary += f" · {kept} 个已有文件夹未动"
        if desc_written:
            summary += f" · 新说明 {desc_written} 篇"
    settings.last_run = now.isoformat(timespec="seconds")
    settings.last_summary = summary
    try:
        settings.save()
    except Exception:
        pass

    log(f"完成：{summary}")
    log(f"表格：{sheet}")
    return {
        "ok": True,
        "total": len(items),
        "added": len(rep["added"]),
        "removed": len(rep["removed"]),
        "downloaded": len(downloaded),
        "kept": kept,
        "desc_written": desc_written,
        "failed": failed,
        "sheet": str(sheet),
        "summary": summary,
        "items": items,
    }
