#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mail — 邮件 app 直连源 (IMAP 授权码收件 + SMTP 发信) — 纯逻辑, 无 pygame 依赖.

设计要点:
- 纯标准库 imaplib/smtplib/email, 零第三方依赖 (商店版约束).
- 多账号: PROVIDERS 域名注册表预留 (163/126/yeah/qq/outlook/gmail),
  新邮箱 = 加一条 dict 即可, UI 层零改动; 账号 (含授权码明文) 存
  STATE_DIR/mail.json, net.save_json_file 0600 原子落盘.
- 短连接: 每操作 connect→login→操作→logout, 跑在 net.job 后台 worker.
- 全部顶层函数返回 {"ok": bool, "err": str, ...} 不抛异常 (net.job
  worker 吞异常丢详情, UI 只能报错误气泡).
- 163 坑: 登录后补发 IMAP ID 命令 (163 对无 ID 客户端偶发 Unsafe
  Login 拒连, 加了无害); 列表/正文一律 BODY.PEEK 不误标已读.
- 发信: SMTP_SSL 465 直发 (授权码同 IMAP), 成功后尽力 APPEND 已发送
  (163/126 类 SMTP 发出不自动存 Sent; 文件夹名 LIST 解码后按
  已发送/Sent 匹配, 失败静默不影响发送结果).
- 解析函数移植自家庭版 server/email-status-server.py (真机验证多年):
  RFC2047 头解码 / HTML 剥离 / 引言签名裁剪 / IMAP UTF-7.
"""

import base64
import email as email_mod
import email.utils
import imaplib
import os
import re
import smtplib
import time
from email.header import decode_header
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parsedate_to_datetime

import net as netmod

STATE_DIR = os.path.expanduser("~/.local/share/oneapp")
MAIL_FILE = os.path.join(STATE_DIR, "mail.json")   # {"accounts": [...], "last": email}

# IMAP 授权码登录: 域名 → 服务器 (新邮箱直接在此加条目, imap/smtp+端口)
PROVIDERS = {
    "163.com":      {"imap": "imap.163.com",      "port": 993,
                     "smtp": "smtp.163.com",      "sport": 465},
    "126.com":      {"imap": "imap.126.com",      "port": 993,
                     "smtp": "smtp.126.com",      "sport": 465},
    "yeah.net":     {"imap": "imap.yeah.net",     "port": 993,
                     "smtp": "smtp.yeah.net",     "sport": 465},
    "qq.com":       {"imap": "imap.qq.com",       "port": 993,
                     "smtp": "smtp.qq.com",       "sport": 465},
    "foxmail.com":  {"imap": "imap.qq.com",       "port": 993,
                     "smtp": "smtp.qq.com",       "sport": 465},
    "outlook.com":  {"imap": "outlook.office365.com", "port": 993,
                     "smtp": "smtp.office365.com",    "sport": 587},
    "hotmail.com":  {"imap": "outlook.office365.com", "port": 993,
                     "smtp": "smtp.office365.com",    "sport": 587},
    "gmail.com":    {"imap": "imap.gmail.com",    "port": 993,
                     "smtp": "smtp.gmail.com",    "sport": 465},
}
SMTP_PORT = 465                                     # smtp 默认口 (outlook 587)


def provider_for(addr):
    """按邮箱域名查注册表; 未知域回落 imap.<域名>:993 (预留扩展)."""
    dom = addr.rsplit("@", 1)[-1].strip().lower() if "@" in addr else ""
    p = PROVIDERS.get(dom)
    if p:
        return dict(p, domain=dom)
    return {"imap": "imap." + dom if dom else "", "port": 993,
            "smtp": "smtp." + dom if dom else "", "sport": SMTP_PORT,
            "domain": dom}


# ── 账号存储 (mail.json, 0600) ──────────────────────────────────────────────
def load_accounts():
    d = netmod.load_json_file(MAIL_FILE, None)
    if not isinstance(d, dict):
        d = {}
    d.setdefault("accounts", [])
    d.setdefault("last", "")
    return d


def save_accounts(d):
    netmod.save_json_file(MAIL_FILE, d)


def upsert_account(addr, auth):
    """新增/更新账号 (授权码变更同入口) → 落盘. 返回 {"ok","err"}."""
    addr = (addr or "").strip()
    auth = (auth or "").strip()
    if "@" not in addr or not auth:
        return {"ok": False, "err": "邮箱/授权码格式不对"}
    d = load_accounts()
    for it in d["accounts"]:
        if it.get("email", "").lower() == addr.lower():
            it["auth"] = auth
            d["last"] = it["email"]             # last 沿用存量写法
            break
    else:
        d["accounts"].append({"email": addr, "auth": auth})
        d["last"] = addr
    save_accounts(d)
    return {"ok": True}


def remove_account(addr):
    d = load_accounts()
    d["accounts"] = [it for it in d["accounts"]
                     if it.get("email", "").lower() != (addr or "").lower()]
    if d.get("last", "").lower() == (addr or "").lower():
        d["last"] = d["accounts"][0]["email"] if d["accounts"] else ""
    save_accounts(d)
    return {"ok": True}


# ── 解析 (移植自家庭版 server/email-status-server.py, 真机验证多年) ─────────
def _decode_imap_utf7(name):
    """IMAP modified UTF-7 → 文本 (&XXX- 段 base64 utf-16-be)."""
    if name == "INBOX":
        return name
    out, i = "", 0
    while i < len(name):
        if name[i] == "&":
            end = name.find("-", i)
            if end == -1:
                out += name[i:]
                break
            enc = name[i + 1:end]
            if enc == "":
                out += "&"
            else:
                b64 = enc.replace(",", "/")
                pad = 4 - len(b64) % 4
                if pad != 4:
                    b64 += "=" * pad
                try:
                    out += base64.b64decode(b64).decode("utf-16-be")
                except Exception:
                    out += name[i:end + 1]
            i = end + 1
        else:
            out += name[i]
            i += 1
    return out


def _utf7_chunk(s):
    b = s.encode("utf-16-be")
    return "&" + base64.b64encode(b).decode("ascii").rstrip("=").replace(
        "/", ",") + "-"


def _encode_imap_utf7(name):
    """文本 → IMAP modified UTF-7 (非 ASCII 连段编码, & 转义为 &-)."""
    out, buf = [], ""
    for ch in name:
        if 0x20 <= ord(ch) <= 0x7e:
            if buf:
                out.append(_utf7_chunk(buf))
                buf = ""
            out.append("&-" if ch == "&" else ch)
        else:
            buf += ch
    if buf:
        out.append(_utf7_chunk(buf))
    return "".join(out)


# 跳过的文件夹: 垃圾/广告/订阅/草稿 (2026-10-28 用户口径, utf7 名与中文名双判);
# QQ 是英文夹名: Junk=垃圾, Drafts=草稿 (Sent Messages/Deleted Messages 保留显示)
_SKIP_FOLDERS = frozenset({
    "&V4NXPpCuTvY-",  # 垃圾邮件
    "&Xn9USpCuTvY-",  # 广告邮件
    "&i6KWBZCuTvY-",  # 订阅邮件
    "&g0l6P3ux-",     # 草稿箱
})
_SKIP_NAMES = ("垃圾邮件", "广告邮件", "订阅邮件", "草稿箱", "Junk", "Drafts")


def should_skip(name):
    if not name or name.startswith("["):
        return True
    if name in _SKIP_FOLDERS or name in _SKIP_NAMES:
        return True
    return _decode_imap_utf7(name) in _SKIP_NAMES


def decode_subject(raw):
    """RFC2047 编码头 → 中文文本 (=?gbk?B?...?= 拼接段)."""
    if not raw:
        return "(无主题)"
    try:
        out = ""
        for part, enc in decode_header(str(raw)):
            if isinstance(part, bytes):
                out += part.decode(enc or "utf-8", errors="replace")
            else:
                out += part
        return out.strip() or "(无主题)"
    except Exception:
        return str(raw).strip()[:60] or "(无主题)"


def decode_from(raw, inbox=False):
    """From 头 → 显示名 (INBOX 里显示地址段, 其他夹显示名)."""
    if not raw:
        return ""
    try:
        dec = ""
        for part, enc in decode_header(str(raw)):
            if isinstance(part, bytes):
                dec += part.decode(enc or "utf-8", errors="replace")
            else:
                dec += part
        raw = dec.strip()
    except Exception:
        pass
    try:
        disp, addr = email.utils.parseaddr(raw)
    except Exception:
        disp, addr = "", ""
    out = addr or raw.strip().strip("<>").strip()
    if not inbox and disp.strip():
        out = disp.strip()
    return out[:60]


def _strip_html(html_text):
    text = re.sub(r"<(style|script)[^>]*>.*?</(style|script)>", " ",
                  html_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<(br|p|div|tr|li)[^>]*>", " ", text,
                  flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    import html as _html
    return _html.unescape(text).replace("\xa0", " ")   # nbsp 归一普通空格


def _part_text(part):
    """单个 MIME part → 文本 (按 charset 解码, replace 兜底)."""
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _msg_text(msg):
    """整封 → 正文文本 (plain 优先, html 剥离兜底)."""
    if msg.is_multipart():
        plain, html_fb = "", ""
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and not plain:
                plain = _part_text(part)
            elif ct == "text/html" and not html_fb:
                html_fb = _part_text(part)
        return plain if plain else _strip_html(html_fb)
    raw = _part_text(msg)
    if msg.get_content_type() == "text/html":
        return _strip_html(raw)
    return raw


_QUOTE_HEADS = ("from:", "de:", "sent:", "enviado", "le:", "am:", "van:")


def _trim_quotes(text):
    """裁掉引言/签名线及之后 (回复链噪音; 家庭版同口径)."""
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(">") or s.startswith("--") \
                or s.lower().startswith(_QUOTE_HEADS):
            break
        lines.append(line)
    return "\n".join(lines)


def extract_preview(msg, max_chars=200):
    """列表页单行预览: 正文 → 去引言 → 拼接 → 控制字符剔除 → 截断."""
    try:
        text = _msg_text(msg)
        if not text:
            return ""
        # 营销邮件 plain 部分常内嵌 <img data:...> 标签 — 图标化 + 残余标签剔除
        text = re.sub(r"<img[^>]*>", "(图)", text, flags=re.IGNORECASE)
        lines = []
        for line in _trim_quotes(text).splitlines():
            s = line.strip()
            if s:
                lines.append(s)
        out = " ".join(lines)
        out = "".join(c for c in out if ord(c) >= 32 and c != chr(127))
        out = re.sub(r"<[a-zA-Z][^>]{0,200}>", " ", out)   # 残余标签剔除
        return out[:max_chars].strip()
    except Exception:
        return ""


def extract_body(msg, max_chars=6000):
    """详情页正文: 保留换行结构, 去引言/签名, 空行压缩."""
    try:
        text = _msg_text(msg)
        if not text:
            return "(空邮件)"
        text = re.sub(r"<img[^>]*>", "(图)", text, flags=re.IGNORECASE)
        out, blank = [], False
        for line in _trim_quotes(text).splitlines():
            s = line.rstrip()
            if s.strip():
                out.append(s)
                blank = False
            elif out and not blank:
                out.append("")
                blank = True
        body = "\n".join(out).strip()
        return body[:max_chars] if body else "(空邮件)"
    except Exception:
        return "(正文解析失败)"


def fmt_date(raw):
    """Date 头 → 本地时区 mm-dd HH:MM."""
    try:
        if not raw:
            return ""
        return parsedate_to_datetime(raw).astimezone().strftime("%m-%d %H:%M")
    except Exception:
        return ""


# ── IMAP 短连接操作 (全部不抛异常, 返回 {"ok","err",...}) ───────────────────
_LIST_RE = re.compile(r"\((?P<flags>[^)]*)\)\s+(?P<delim>\"[^\"]*\"|NIL)"
                      r"\s+(?P<name>.+)$")
_TIMEOUT = 12


def _friendly_err(e):
    s = str(e)
    if isinstance(e, smtplib.SMTPAuthenticationError) \
            or "AUTHENTICATIONFAILED" in s or "LOGIN failed" in s:
        return "登录被拒 (授权码错或未开 IMAP/SMTP)"
    if "DT:SPM" in s:
        return "163 拒发 (疑垃圾/频率限制, 稍后再试)"
    if "Unsafe Login" in s:
        return "服务端拒连 (Unsafe Login)"
    if "timeout" in s.lower() or "timed out" in s.lower():
        return "连接超时"
    m = re.search(r"\[([A-Z-]+)[^\]]*\]", s)
    return ("登录失败 " + m.group(1)) if m else s[:60]


_SESS = {"M": None, "email": "", "at": 0.0}   # IMAP 会话复用 (单 worker 线程)
_SESS_TTL = 240                               # 秒; NOOP 兜底校验服务端掐线


def _drop_sess():
    if _SESS["M"] is not None:
        try:                              # 死会话 logout 别等满 socket 超时
            _SESS["M"].socket().settimeout(3)
        except Exception:
            pass
        try:
            _SESS["M"].logout()
        except Exception:
            pass
    _SESS.update(M=None, email="", at=0.0)


def _release(M):
    """操作收尾 (原 finally logout): 命中缓存的会话不登出, 留给下个操作复用."""
    if _SESS["M"] is not M:
        try:
            M.logout()
        except Exception:
            pass


def _connect(acct):
    """acct={"email","auth"} → (IMAP4_SSL | None, err|None). 登录后补 ID.
    会话复用: 同账号 TTL 内 NOOP 通过即免登录 — 163 频繁登录会被限流拖慢
    (短连接风暴: 列表每分钟重拉 = 每分钟全新登录, 用一阵子后越来越卡)."""
    addr, auth = acct.get("email", ""), acct.get("auth", "")
    if not addr or not auth:
        return None, "账号未配置"
    p = provider_for(addr)
    if not p["imap"]:
        return None, "无法识别邮箱域名"
    s = _SESS
    if s["M"] is not None and s["email"] == addr \
            and time.monotonic() - s["at"] < _SESS_TTL:
        try:
            s["M"].socket().settimeout(5)     # 探活短超时 (会话被服务端掐后
            ok = s["M"].noop()[0] == "OK"     # NOOP 别顶满 12s, 163 实测会挂)
            s["M"].socket().settimeout(_TIMEOUT)
            if ok:
                s["at"] = time.monotonic()
                return s["M"], None
        except Exception:
            pass
        _drop_sess()                          # 会话已被服务端掐 → 重连
    try:
        M = imaplib.IMAP4_SSL(p["imap"], p.get("port", 993), timeout=_TIMEOUT)
    except Exception as e:
        return None, _friendly_err(e)
    try:
        M.login(addr, auth)
    except Exception as e:
        try:
            M.logout()
        except Exception:
            pass
        return None, _friendly_err(e)
    try:
        # 163 对无 ID 客户端偶发 Unsafe Login 拒连 — 防御性补发, 失败无害
        M.xatom("ID", '("name" "ONEAPP" "version" "1.0.0")')
    except Exception:
        pass
    _drop_sess()                              # 换账号/旧会话让位
    s["M"], s["email"], s["at"] = M, addr, time.monotonic()
    return M, None


def test_login(acct):
    """登录表单提交: 连通性验证 + INBOX 未读数."""
    M, err = _connect(acct)
    if err:
        return {"ok": False, "err": err}
    try:
        st, _ = M.select("INBOX", readonly=True)
        if st != "OK":
            return {"ok": False, "err": "INBOX 打开失败"}
        st, un = M.uid("SEARCH", "UNSEEN")
        n = len(un[0].split()) if st == "OK" and un and un[0] else 0
        return {"ok": True, "unread": n, "email": acct.get("email", "")}
    except Exception as e:
        return {"ok": False, "err": _friendly_err(e)}
    finally:
        _release(M)


def list_folders(acct):
    """全文件夹: raw 名 (协议用) + 解码显示名 + STATUS 未读/总数.
    垃圾/广告/订阅/草稿跳过; INBOX 置顶, 其余未读多者靠前."""
    M, err = _connect(acct)
    if err:
        return {"ok": False, "err": err}
    try:
        st, lst = M.list()
        if st != "OK":
            return {"ok": False, "err": "LIST 失败"}
        out = []
        for item in lst or []:
            if not item:
                continue
            line = item.decode("utf-8", "replace").strip()
            m = _LIST_RE.match(line)
            if not m:
                continue
            raw = m.group("name").strip()          # 带原样引号, 协议调用直接用
            disp = _decode_imap_utf7(raw.strip('"'))
            if should_skip(disp):
                continue
            unread = total = -1
            try:
                st, st_data = M.status(raw, "(MESSAGES UNSEEN)")
                if st == "OK" and st_data and st_data[0]:
                    mm = re.search(r"MESSAGES (\d+)", st_data[0].decode(
                        "utf-8", "replace"))
                    uu = re.search(r"UNSEEN (\d+)", st_data[0].decode(
                        "utf-8", "replace"))
                    total = int(mm.group(1)) if mm else 0
                    unread = int(uu.group(1)) if uu else 0
            except Exception:
                pass
            out.append({"raw": raw, "name": disp, "unread": unread,
                        "total": total})
        out.sort(key=lambda f: (f["name"].upper() != "INBOX",
                                -(f["unread"] if f["unread"] >= 0 else 0),
                                f["name"]))
        return {"ok": True, "folders": out}
    except Exception as e:
        return {"ok": False, "err": _friendly_err(e)}
    finally:
        _release(M)


def _search_uf(M):
    """未读+旗标 uid 并集 (升序, 尾部=最新).
    QQ 对 OR UNSEEN FLAGGED 返回空 (2026-11-15 实测: STATUS UNSEEN=29 而
    OR=0, 腾讯 IMAP 的 OR 兼容性问题) — 两次单键搜索取并集, 163/QQ 通吃."""
    out = set()
    for key in ("UNSEEN", "FLAGGED"):
        try:
            st, data = M.uid("SEARCH", key)
            if st == "OK" and data and data[0]:
                out.update(data[0].split())
        except Exception:
            pass
    return sorted(out, key=int)


def fetch_msgs(acct, raw_q, limit=30):
    """未读+旗标邮件列表 (最新在前, 服务端搜索, 不拉全量):
    FLAGS + 前 16KB 原文, PEEK 不置已读."""
    M, err = _connect(acct)
    if err:
        return {"ok": False, "err": err}
    try:
        st, _ = M.select(raw_q, readonly=True)
        if st != "OK":
            return {"ok": False, "err": "文件夹打开失败"}
        ids = _search_uf(M)
        recent = ids[-limit:]
        if not recent:
            return {"ok": True, "msgs": [], "total": 0}
        st, fetched = M.uid("FETCH", b",".join(recent).decode("ascii"),
                            "(FLAGS BODY.PEEK[]<0.16384>)")
        if st != "OK":
            return {"ok": False, "err": "取列表失败"}
        got = {}
        for item in fetched or []:
            if isinstance(item, tuple) and item[1]:
                m = re.search(rb"UID (\d+)", item[0])
                if m:
                    got[m.group(1)] = item
        inbox = raw_q.strip('"').upper() == "INBOX"
        msgs = []
        for uid in reversed(recent):               # 最新在前
            it = got.get(uid)
            if not it:
                continue
            flags = it[0].decode("utf-8", "replace")
            msg = email_mod.message_from_bytes(it[1])
            msgs.append({
                "uid": uid.decode("ascii"),
                "subject": decode_subject(msg["Subject"])[:80],
                "from": decode_from(msg["From"] or "", inbox=inbox)[:70],
                "date": fmt_date(msg.get("Date")),
                "preview": extract_preview(msg, 200),
                "seen": "\\Seen" in flags,
                "flagged": "\\Flagged" in flags,
            })
        return {"ok": True, "msgs": msgs, "total": len(ids)}
    except Exception as e:
        return {"ok": False, "err": _friendly_err(e)}
    finally:
        _release(M)


def fetch_body(acct, raw_q, uid):
    """详情正文: 前 128KB 原文 → 纯文本 (换行结构保留).
    附带回信元数据 (msg_id/refs/from_addr/date, 回复预填 + In-Reply-To 用)."""
    M, err = _connect(acct)
    if err:
        return {"ok": False, "err": err}
    try:
        st, _ = M.select(raw_q, readonly=True)
        if st != "OK":
            return {"ok": False, "err": "文件夹打开失败"}
        st, data = M.uid("FETCH", uid, "(BODY.PEEK[]<0.131072>)")
        if st != "OK" or not data or data[0] is None \
                or not isinstance(data[0], tuple) or not data[0][1]:
            return {"ok": False, "err": "取正文失败"}
        msg = email_mod.message_from_bytes(data[0][1])
        try:
            _, from_addr = email.utils.parseaddr(
                msg.get("Reply-To") or msg.get("From") or "")
        except Exception:
            from_addr = ""
        return {"ok": True, "uid": uid,
                "subject": decode_subject(msg["Subject"]),
                "text": extract_body(msg),
                "msg_id": (msg.get("Message-ID") or "").strip(),
                "refs": (msg.get("References") or "").strip(),
                "from_addr": from_addr,
                "date": fmt_date(msg.get("Date"))}
    except Exception as e:
        return {"ok": False, "err": _friendly_err(e)}
    finally:
        _release(M)


_ACTIONS = {                                          # action → STORE 操作
    "mark_read": ("+FLAGS", "(\\Seen)"),
    "mark_unread": ("-FLAGS", "(\\Seen)"),
    "flag": ("+FLAGS", "(\\Flagged)"),
    "unflag": ("-FLAGS", "(\\Flagged)"),
}


def mail_action(acct, raw_q, uid, action):
    """标已读/未读/旗标 (读写模式短连接)."""
    op = _ACTIONS.get(action)
    if not op:
        return {"ok": False, "err": "未知操作"}
    M, err = _connect(acct)
    if err:
        return {"ok": False, "err": err}
    try:
        st, _ = M.select(raw_q)                     # 读写模式 (STORE 需要)
        if st != "OK":
            return {"ok": False, "err": "文件夹打开失败"}
        st, _ = M.uid("STORE", uid, op[0], op[1])
        return {"ok": st == "OK", "uid": uid, "action": action}
    except Exception as e:
        return {"ok": False, "err": _friendly_err(e)}
    finally:
        _release(M)


_SENT_NAMES = ("sent messages", "sent items", "sent mail", "sent")


def _append_sent(addr, auth, msg):
    """发信后尽力把原件 APPEND 到服务器已发送夹.
    163/126 类 SMTP 会自动存 Sent (QQ 不存) — 先按 Message-ID 查重,
    服务器已有则跳过, 两类服务商都不产生重复. LIST 解码后按
    已发送/Sent 匹配文件夹; 任何失败静默 (不影响发送结果)."""
    try:
        M, err = _connect({"email": addr, "auth": auth})
        if err or M is None:
            return
        try:
            st, lst = M.list()
            box = ""
            if st == "OK":
                for item in lst or []:
                    if not item:
                        continue
                    m = _LIST_RE.match(
                        item.decode("utf-8", "replace").strip())
                    if not m:
                        continue
                    raw = m.group("name").strip()
                    disp = _decode_imap_utf7(raw.strip('"')).lower()
                    if "已发送" in disp or disp in _SENT_NAMES:
                        box = raw
                        break
            if box:
                mid = (msg.get("Message-ID") or "").strip()
                have = False
                if mid:                        # SMTP 自动存档的服务商已有原件
                    st, data = M.uid("SEARCH", "HEADER", "Message-ID",
                                     '"%s"' % mid)
                    have = st == "OK" and data and data[0] \
                        and bool(data[0].split())
                if not have:
                    M.append(box, "\\Seen", None, msg.as_bytes())
        finally:
            _release(M)
    except Exception:
        pass


def send_mail(acct, to, subject, body, in_reply_to="", references=""):
    """SMTP_SSL 直发 (授权码同 IMAP). 回信带 In-Reply-To/References 头.
    成功后尽力存服务器已发送副本 (查重防双份). 返回 {"ok","err"} 不抛异常."""
    addr, auth = acct.get("email", ""), acct.get("auth", "")
    if not addr or not auth:
        return {"ok": False, "err": "账号未配置"}
    to = (to or "").strip()
    if "@" not in to:
        return {"ok": False, "err": "收件人格式不对"}
    p = provider_for(addr)
    if not p["smtp"]:
        return {"ok": False, "err": "无法识别邮箱域名"}
    msg = EmailMessage()
    msg["From"] = addr
    msg["To"] = to
    msg["Subject"] = (subject or "").strip() or "(无主题)"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=addr.rsplit("@", 1)[-1])
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = ((references + " " + in_reply_to).strip()
                             [:900])           # 头过长部分服务器会拒
    msg.set_content((body or "").strip() or " ")
    try:
        with smtplib.SMTP_SSL(p["smtp"], p.get("sport", SMTP_PORT),
                              timeout=15) as S:
            S.login(addr, auth)
            S.send_message(msg)
    except Exception as e:
        return {"ok": False, "err": _friendly_err(e)}
    _append_sent(addr, auth, msg)
    return {"ok": True, "err": ""}


def inbox_unread(acct):
    """账号层未读角标: 只 STATUS INBOX, 不 SELECT (轻量)."""
    M, err = _connect(acct)
    if err:
        return {"ok": False, "err": err, "email": acct.get("email", "")}
    try:
        st, data = M.status("INBOX", "(UNSEEN)")
        if st == "OK" and data and data[0]:
            m = re.search(r"UNSEEN (\d+)", data[0].decode("utf-8", "replace"))
            return {"ok": True, "email": acct.get("email", ""),
                    "unread": int(m.group(1)) if m else 0}
        return {"ok": False, "err": "STATUS 失败", "email": acct.get("email", "")}
    except Exception as e:
        return {"ok": False, "err": _friendly_err(e),
                "email": acct.get("email", "")}
    finally:
        _release(M)
