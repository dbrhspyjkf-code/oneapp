# -*- coding: utf-8 -*-
"""news_rss — 新闻页数据层: 直连 7 个中文 RSS/Atom 源 (零本地服务依赖).

从家庭后端 _rss_news.py 移植为纯 stdlib (urllib + xml.etree):
RSS2.0 / Atom 双格式, 命名空间无关, 剥 HTML 标签; 单源失败不影响整体.
源清单 = 大陆直连实测 7/7 全通 (2026-11-14, zero-news 同款 UA 无封锁).
输出形状与原端点一致: {"ok", "fetched", "feeds": {fid: {ok, name, items}}}.

IPv4 优先: 家宽 IPv6 半残时部分站 AAAA 会拖到超时 (hermes/zero-news 同款处理).
"""

import datetime
import email.utils
import html as _html
import re
import socket
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

# 进程级 IPv4 优先 (zero-news 同款; 不影响 ffplay 等子进程)
_orig_getaddrinfo = socket.getaddrinfo


def _v4_first(host, port, family=0, type=0, proto=0, flags=0):
    try:
        return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    except socket.gaierror:
        return _orig_getaddrinfo(host, port, family, type, proto, flags)


socket.getaddrinfo = _v4_first

FEEDS = {
    "ai":       {"name": "AI 新闻",   "url": "https://www.qbitai.com/feed"},   # 量子位
    "ithome":   {"name": "IT之家",    "url": "https://www.ithome.com/rss/"},
    "sspai":    {"name": "少数派",    "url": "https://sspai.com/feed"},
    "solidot":  {"name": "Solidot",   "url": "https://www.solidot.org/index.rss"},
    "ifanr":    {"name": "爱范儿",    "url": "https://www.ifanr.com/feed"},
    "leiphone": {"name": "雷峰网",    "url": "https://www.leiphone.com/feed"},
    "wscn":     {"name": "华尔街见闻", "url": "https://dedicated.wallstreetcn.com/rss.xml"},
}

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ONEAPP/1.0"
_TIMEOUT = 12
_MAX_ITEMS = 20
_TTL_S = 600                                   # 进程内缓存 (Net.job 之上再省一层)

_STRIP_TAGS_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_CACHE = {}                                    # url -> {"ts": float, "data": dict}


def _clean(s, limit):
    s = _WS_RE.sub(" ", _STRIP_TAGS_RE.sub(" ", _html.unescape(s or ""))).strip()
    return s[:limit]


def _fmt_ts(s):
    """RFC822(RSS) / ISO8601(Atom) → 本地时区 "MM-dd HH:MM", 解析失败为 ""."""
    s = (s or "").strip()
    if not s:
        return ""
    dt = None
    try:
        dt = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        pass
    if dt is None:
        try:
            dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if dt.tzinfo:
        dt = dt.astimezone()
    return dt.strftime("%m-%d %H:%M")


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_feed(raw):
    """RSS2.0 / Atom → [{t, d, u, ts}] (统一入口, 便于离线测试)."""
    root = ET.fromstring(raw)
    is_atom = _local(root.tag) == "feed"
    out = []
    for node in root.iter():
        if _local(node.tag) != ("entry" if is_atom else "item"):
            continue
        title = desc = link = date = ""
        for ch in node:
            name = _local(ch.tag)
            if name == "title":
                title = ch.text or ""
            elif name in ("description", "summary", "content"):
                if not desc:
                    desc = ch.text or ""
            elif name == "link":
                link = ch.get("href") or ch.text or ""
            elif name in ("pubDate", "published", "updated", "date"):
                if not date:
                    date = ch.text or ""
        t = _clean(title, 120)
        if not t:
            continue
        out.append({"t": t, "d": _clean(desc, 200),
                    "u": (link or "").strip()[:300], "ts": _fmt_ts(date)})
        if len(out) >= _MAX_ITEMS:
            break
    return out


def _fetch_one(fid, meta):
    url = meta["url"]
    ent = _CACHE.get(url)
    now = time.time()
    if ent and now - ent["ts"] < _TTL_S:
        return fid, ent["data"]
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            raw = r.read(1048576)
        items = parse_feed(raw)
        if not items:
            raise ValueError("empty feed")
        data = {"ok": True, "name": meta["name"], "items": items}
        _CACHE[url] = {"ts": now, "data": data}
    except Exception:                          # 单源失败不影响整体; 不缓存, 下轮重试
        data = {"ok": False, "name": meta["name"], "error": "fetch_failed"}
    return fid, data


def news_fetch_all():
    """Net.job 入口 (worker 线程调用): 内部线程池并发 7 源."""
    with ThreadPoolExecutor(max_workers=8) as ex:
        pairs = list(ex.map(lambda kv: _fetch_one(*kv), FEEDS.items()))
    return {"ok": True,
            "fetched": datetime.datetime.now().strftime("%m-%d %H:%M"),
            "feeds": dict(pairs)}
