# -*- coding: utf-8 -*-
"""net — ONEAPP 公网直连网络层 (无本地服务依赖).

所有数据源都是公网 API/RSS, 设备直接 urllib 拉取 (家里 WiFi / 手机热点 /
任何能上网的环境均可运行). 后台单线程 worker + TTL 缓存 + 请求去重,
UI 线程只投递请求、从 results 队列取结果:

  ("data", tag, obj|None) / ("text", tag, str|None) / ("bin", tag, bytes|None)
  ("mode", mode, None)    / ("error", tag, None)
"""

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request

STATE_DIR = os.path.expanduser("~/.local/share/oneapp")
CONFIG_FILE = os.path.join(STATE_DIR, "config.json")

# 探测目标: 大陆骨干 + 无鉴权 200, 3s 内通 = 在线.
PROBE_URL = "https://www.baidu.com/favicon.ico"

# 公网请求保持系统默认 opener (设备无代理环境; 开发机走代理反而可达).
_OPENER = urllib.request.build_opener()
_UA = "ONEAPP/1.0"


def load_json_file(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def http_json(url, timeout=8, post=None, headers=None):
    """GET/POST 一个 JSON. 返回 (obj|None, None); 兼容旧签名."""
    hdrs = {"User-Agent": _UA}
    if headers:
        hdrs.update(headers)
    data = None
    if post is not None:
        hdrs["Content-Type"] = "application/json"
        data = json.dumps(post).encode()
    req = urllib.request.Request(url, data=data, headers=hdrs)
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            body = r.read(2097152)
            try:
                return json.loads(body), None
            except Exception:
                return None, None
    except Exception:
        return None, None


class Net:
    """后台单线程 worker; UI 线程经 pub()/pub_text()/bin()/job() 投递,
    从 results 拿. mode: probing | online | offline."""

    def __init__(self):
        self.mode = "probing"
        self.results = queue.Queue()
        self._reqs = queue.Queue()
        self._cache = {}                      # key -> (deadline, data)
        self._inflight = set()
        self._probe_at = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.probe()                            # 启动即探测链路

    # ── UI 线程接口 ──
    def pub(self, url, tag, ttl=600, timeout=10, headers=None):
        """公网 JSON 请求 (天气/行情等); 结果 ('data', tag, obj|None)."""
        self._reqs.put(("pub", url, tag, ttl, timeout, headers))

    def pub_text(self, url, tag, ttl=15):
        """公网文本请求 (腾讯行情 GBK 文本); 结果 ('text', tag, str|None)."""
        self._reqs.put(("pubt", url, tag, ttl))

    def bin(self, _port_unused, url, tag, ttl=1800):
        """公网二进制 (专辑封面等绝对外链); 结果 ('bin', tag, bytes|None).
        首参保留旧签名占位 (调用处 net.bin(None, url, ...))."""
        self._reqs.put(("bin", url, tag, ttl))

    def job(self, tag, fn, ttl=600):
        """通用取数: worker 线程里跑 fn() (零参, 返回 obj|None), 按 tag 缓存.
        RSS/汇率/开奖等需要本地解析的数据源都走这里; 结果 ('data', tag, obj)."""
        self._reqs.put(("job", tag, fn, ttl))

    def invalidate(self, key):
        """作废缓存 (dict.pop 原子, UI 线程直调)."""
        self._cache.pop(key, None)

    def probe(self):
        self._reqs.put(("probe",))

    def close(self):
        self._stop.set()
        self._reqs.put(("quit",))

    # ── worker ──
    def _run(self):
        while not self._stop.is_set():
            try:
                job = self._reqs.get(timeout=0.5)
            except queue.Empty:
                job = None
            if job:
                kind = job[0]
                if kind == "quit":
                    return
                if kind == "probe":
                    self._do_probe()
                elif kind == "pub":
                    self._do_pub(*job[1:])
                elif kind == "pubt":
                    self._do_pubt(*job[1:])
                elif kind == "bin":
                    self._do_bin(*job[1:])
                elif kind == "job":
                    self._do_job(*job[1:])
            elif self.mode == "offline" and time.monotonic() - self._probe_at > 30:
                self._do_probe()               # 离线时 30s 重试
            elif self.mode == "online" and time.monotonic() - self._probe_at > 300:
                self._do_probe()               # 在线 5min 复核

    def _do_probe(self):
        self._probe_at = time.monotonic()
        try:
            req = urllib.request.Request(PROBE_URL, headers={"User-Agent": _UA})
            with _OPENER.open(req, timeout=3) as r:
                ok = r.status == 200
        except Exception:
            ok = False
        self._set_mode("online" if ok else "offline")

    def _do_job(self, tag, fn, ttl):
        key = ("job", tag)
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and hit[0] > now:
            return
        if key in self._inflight:
            return
        self._inflight.add(key)
        try:
            try:
                obj = fn()
            except Exception:
                obj = None
            if obj is not None:
                self._cache[key] = (now + ttl, obj)
                self.results.put(("data", tag, obj))
            else:
                self.results.put(("error", tag, None))
        finally:
            self._inflight.discard(key)

    def _do_pub(self, url, tag, ttl, timeout=10, headers=None):
        key = ("pub", url)
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and hit[0] > now:
            return
        if key in self._inflight:
            return
        self._inflight.add(key)
        try:
            j, _ = http_json(url, timeout=timeout, headers=headers)
            if j is not None:
                self._cache[key] = (now + ttl, j)
                self.results.put(("data", tag, j))
            else:
                self.results.put(("error", tag, None))
        finally:
            self._inflight.discard(key)

    def _do_pubt(self, url, tag, ttl):
        key = ("pubt", url)
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and hit[0] > now:
            return
        if key in self._inflight:
            return
        self._inflight.add(key)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            try:
                with _OPENER.open(req, timeout=10) as r:
                    body = r.read(262144)
            except Exception:
                self.results.put(("text", tag, None))
                return
            for enc in ("utf-8", "gbk"):        # qt.gtimg.cn 返回 GBK 文本
                try:
                    txt = body.decode(enc)
                    break
                except UnicodeDecodeError:
                    txt = None
            self._cache[key] = (now + ttl, txt)
            self.results.put(("text", tag, txt))
        finally:
            self._inflight.discard(key)

    def _do_bin(self, url, tag, ttl):
        """二进制 GET (封面等绝对外链): 结果 ('bin', tag, bytes|None)."""
        key = ("bin", url)
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and hit[0] > now:
            return
        if key in self._inflight:
            return
        self._inflight.add(key)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            try:
                with _OPENER.open(req, timeout=15) as r:
                    data = r.read(3 * 1024 * 1024)
                self._cache[key] = (now + ttl, data)
                self.results.put(("bin", tag, data))
            except Exception:
                self.results.put(("bin", tag, None))
        finally:
            self._inflight.discard(key)

    def _set_mode(self, mode):
        if mode != self.mode:
            self.mode = mode
            self._cache.clear()
        self.results.put(("mode", mode, None))
