#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""portal — Wi-Fi 门户认证 (captive portal) 直连 — 纯逻辑, 无 pygame 依赖.

设计要点:
- 纯标准库 urllib/ssl/re, 零第三方依赖 (商店版约束).
- 探针国内可达优先 (gstatic 被墙, 已联网也探不通 → 会误报门户);
  204 / msft 原样响应 = 已联网, 30x = 跳转门户, 200+HTML = DNS 劫持
  (响应体即门户页, 直接解析, 省一次请求).
- 门户页 https 自签证书很常见 → SSL 一律不验证 (仅认证用途, 可接受).
- 编码: header charset / meta charset / utf-8 → gbk 回落 (国内门户大量 GBK).
- 表单: hidden 全收 (token 常藏这里), checkbox 勾选才提交, 第一个
  submit 按钮 name=value 一并带上 (部分门户必带), select 取首选项.
- 全部顶层函数返回 {"ok": ...} 不抛异常 (net.job worker 吞异常丢详情).
"""

import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

# 探针列表 (顺序即优先级; timeout 单发 5s, 命中即止)
PROBES = ("http://connect.rom.miui.com/generate_204",
          "http://wifi.vivo.com.cn/generate_204",
          "http://www.msftconnecttest.com/connecttest.txt")
UA = ("Mozilla/5.0 (Linux; Linux 6.1; armv7l) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """探测期禁跟随重定向 — Location 头就是门户地址."""

    def redirect_request(self, req, fp, code, msg, hdrs, newurl):
        return None                            # None → urllib 抛 HTTPError


def _mk_opener(follow):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False                 # 门户自签证书常见
    ctx.verify_mode = ssl.CERT_NONE
    handlers = [urllib.request.HTTPSHandler(context=ctx)]
    if not follow:
        handlers.append(_NoRedirect())
    return urllib.request.build_opener(*handlers)


_OP_NOFOLLOW = _mk_opener(False)
_OP_FOLLOW = _mk_opener(True)


def _open(url, method="GET", body=None, headers=None, timeout=8, follow=False):
    """底层 HTTP. 返回 (status, headers dict, body bytes, final_url);
    网络不可达 status=0. headers 重复键 (Set-Cookie) 以 \n 连接."""
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    op = _OP_FOLLOW if follow else _OP_NOFOLLOW
    try:
        with op.open(req, timeout=timeout) as r:
            return (r.status, _hdict(r.headers), r.read(2097152),
                    r.geturl() or url)
    except urllib.error.HTTPError as e:        # 30x (禁跟随) / 4xx / 5xx 都要拿头
        try:
            b = e.read(262144)
        except Exception:
            b = b""
        return (e.code, _hdict(e.headers), b, url)
    except Exception:
        return (0, {}, b"", url)


def _hdict(msg):
    out = {}
    for k, v in (msg.items() if hasattr(msg, "items") else []):
        out[k] = (out[k] + "\n" + v) if k in out else v
    return out


def _hdr(hdrs, name):
    for k in hdrs:
        if k.lower() == name.lower():
            return hdrs[k].split("\n")[0]
    return ""


def _decode(body, hdrs=None):
    """utf-8 → 声明编码 → gbk 逐级回落 (国内门户 GBK 常见)."""
    if not body:
        return ""
    order = []
    ct = _hdr(hdrs or {}, "Content-Type")
    m = re.search(r"charset=([A-Za-z0-9_\-]+)", ct, re.I)
    if m:
        order.append(m.group(1))
    m = re.search(rb"charset=[\"']?([A-Za-z0-9_\-]+)", body[:4096], re.I)
    if m:
        order.append(m.group(1).decode("ascii", "replace"))
    order += ["utf-8", "gbk"]
    for e in order:
        try:
            s = body.decode(e)
            if s:
                return s
        except Exception:
            pass
    return body.decode("utf-8", "replace")


def probe():
    """探测是否被门户拦截. 返回 dict:
    ok=1: online=1 已联网; online=0: portal=跳转地址 或 page_html=劫持页.
    ok=0: 探针全超时 (多半没连上 WiFi)."""
    for url in PROBES:
        st, hdrs, body, fin = _open(url, timeout=5)
        if st == 204:
            return {"ok": 1, "online": 1, "probe": url, "st": st}
        if st == 200 and url.endswith("connecttest.txt"):
            if "Microsoft Connect Test" in _decode(body):
                return {"ok": 1, "online": 1, "probe": url, "st": st}
        if st in (301, 302, 303, 307, 308):
            loc = _hdr(hdrs, "Location")
            if loc:
                return {"ok": 1, "online": 0, "probe": url, "st": st,
                        "portal": urllib.parse.urljoin(url, loc)}
        if st == 200:
            t = _decode(body, hdrs)
            low = t[:2048].lower()
            if "<html" in low or "<form" in low or "<body" in low:
                # DNS 劫持/透明代理: 响应体本身就是门户页
                return {"ok": 1, "online": 0, "probe": url, "st": st,
                        "portal": "", "page_url": fin, "page_html": t}
    return {"ok": 0, "err": "探针全部超时 (可能未连上此 WiFi)"}


def fetch(url):
    """拉门户页 (跟随重定向到最终页), 透传 Set-Cookie 供提交用."""
    st, hdrs, body, fin = _open(url, timeout=12, follow=True)
    if st == 0:
        return {"ok": 0, "err": "门户页拉取失败 (超时/拒绝)"}
    cookies = []
    for k, v in hdrs.items():
        if k.lower() == "set-cookie":
            cookies.extend(x.split(";")[0].strip() for x in v.split("\n"))
    return {"ok": 1, "st": st, "url": fin, "html": _decode(body, hdrs),
            "cookies": "; ".join(cookies)}


def _attr(s, name):
    m = re.search(r'\b%s\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s"\'>]+))'
                  % name, s, re.I)
    if not m:
        return ""
    return m.group(2) if m.group(2) is not None else \
        (m.group(3) if m.group(3) is not None else (m.group(4) or ""))


def _strip_tags(s):
    import html as html_mod
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html_mod.unescape(s)).strip()


_EDITABLE = ("", "text", "tel", "password", "email", "number",
             "search", "date")
EDITABLE = _EDITABLE                       # dash 侧预填/存历史用公开名


def parse_form(html, base=""):
    """解析门户页第一个带字段的 <form>. 返回 fields (hidden 全收) /
    button (第一个 submit 的 name=value) / action (绝对地址) /
    has_form=0 时附 text 片段供展示."""
    out = {"ok": 1, "has_form": 0, "method": "POST", "action": "",
           "fields": [], "button": None, "title": "", "text": ""}
    if not html:
        out["ok"] = 0
        out["err"] = "空页面"
        return out
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if m:
        out["title"] = _strip_tags(m.group(1))[:40]
    forms = re.findall(r"<form\b([^>]*)>(.*?)(?:</form>|(?=<form\b)|$)",
                       html, re.S | re.I)
    cands = []
    for attrs, region in forms:
        meth = (_attr(attrs, "method") or "post").upper()
        if meth not in ("GET", "POST"):
            meth = "POST"
        fields, button = _inputs(region)
        cands.append((attrs, meth, fields, button))
    pick = next((c for c in cands if c[2]), cands[0] if cands else None)
    if pick is None:
        out["text"] = _strip_tags(html)[:200]
        return out
    attrs, meth, fields, button = pick
    act = _attr(attrs, "action")
    out.update({"has_form": 1, "method": meth,
                "action": urllib.parse.urljoin(base or "", act)
                if base else act,
                "fields": fields, "button": button})
    return out


def _inputs(region):
    fields, button = [], None
    for m in re.finditer(r"<input\b([^>]*?)/?>", region, re.I):
        a = m.group(1)
        typ = (_attr(a, "type") or "text").lower()
        name = _attr(a, "name")
        if not name:
            continue
        f = {"name": name, "value": _attr(a, "value"), "type": typ,
             "ph": _attr(a, "placeholder")}
        if typ == "hidden":
            fields.append(f)
        elif typ == "checkbox":
            f["checked"] = bool(re.search(r"\bchecked\b", a, re.I))
            fields.append(f)
        elif typ in ("submit", "image"):
            if button is None:
                button = {"name": name, "value": f["value"]}
        elif typ in ("radio", "file"):
            pass                                # 门户极少见, 不支持不误交
        elif typ in _EDITABLE:
            fields.append(f)
    for m in re.finditer(r"<button\b([^>]*)>(.*?)</button>", region,
                         re.S | re.I):
        a, inner = m.group(1), m.group(2)
        typ = (_attr(a, "type") or "submit").lower()
        if typ == "submit" and _attr(a, "name") and button is None:
            button = {"name": _attr(a, "name"), "value": _attr(a, "value")}
        elif typ == "submit" and button is None and "name=" not in a:
            pass                                # 无名按钮无需提交
    for m in re.finditer(r"<select\b([^>]*)>(.*?)</select>", region,
                         re.S | re.I):
        name = _attr(m.group(1), "name")
        if not name:
            continue
        om = re.search(r"<option\b[^>]*value=[\"']?([^\"'>]*)",
                       m.group(2), re.I)
        fields.append({"name": name, "value": om.group(1) if om else "",
                       "type": "select", "ph": ""})
    for m in re.finditer(r"<textarea\b([^>]*)>(.*?)</textarea>", region,
                         re.S | re.I):
        name = _attr(m.group(1), "name")
        if name:
            fields.append({"name": name, "value": _strip_tags(m.group(2)),
                           "type": "text", "ph": ""})
    return fields, button


def submit(method, action, fields, button=None, cookies="", referer=""):
    """提交表单 (follow=True 跟到结果页). fields 就地取 value/checked.
    返回 {"ok","st","page_html"} — 是否真的放行由调用方复探判定."""
    if not action:
        return {"ok": 0, "err": "无提交地址"}
    pairs = []
    for f in fields:
        n = f.get("name")
        if not n:
            continue
        typ = f.get("type", "text")
        if typ == "checkbox":
            if f.get("checked"):
                pairs.append((n, f.get("value") or "on"))
        elif typ != "radio" and typ != "file":
            pairs.append((n, f.get("value", "")))
    if button and button.get("name"):
        pairs.append((button["name"], button.get("value", "")))
    headers = {}
    if cookies:
        headers["Cookie"] = cookies
    if referer:
        headers["Referer"] = referer
    if method.upper() == "GET":
        q = urllib.parse.urlencode(pairs)
        url = action + ("&" if "?" in action else "?") + q
        st, hdrs, body, fin = _open(url, timeout=12, follow=True)
    else:
        data = urllib.parse.urlencode(pairs).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        st, hdrs, body, fin = _open(action, method="POST", body=data,
                                    headers=headers, timeout=12, follow=True)
    if st == 0:
        return {"ok": 0, "err": "提交失败 (超时/拒绝)"}
    return {"ok": 1, "st": st, "url": fin, "page_html": _decode(body, hdrs)}


def urlhost(url):
    try:
        return urllib.parse.urlsplit(url).netloc
    except Exception:
        return ""


def local_info():
    """本机 IP + 网关 (Linux). Mac/测试环境无 /proc → ok=0 (UI 隐藏该行)."""
    gw = ""
    try:
        with open("/proc/net/route") as f:
            for ln in f.read().splitlines()[1:]:
                p = ln.split()
                if len(p) > 2 and p[1] == "00000000":
                    raw = p[2]
                    gw = ".".join(str(int(raw[i:i + 2], 16))
                                  for i in (6, 4, 2, 0))
                    break
    except Exception:
        return {"ok": 0, "err": "no /proc/net/route"}
    ip = ""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))       # UDP connect 不发包
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    return {"ok": 1, "ip": ip, "gw": gw}
