#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音乐 app 直连源 (M2 QQ / M3 网易云) — 纯逻辑, 无 pygame 依赖.

设计要点 (调研结论见 HANDOFF):
- qqmusic-api-python==0.7.2 (MA 同款锁版); 缺库时 qq_ok()=False, UI 显示需装.
- 凭据+设备指纹必须同持久化 (STATE_DIR/qq_cred.json + qqdev/): 新设备 profile
  全流 purl 为空仅 TRY 试听 (2026-11-13 实测), 首次扫码登录后设备文件不再动.
- QR 登录: lib 给的是渲染好的 PNG (尺寸/版本随内容变), 本地解成 0/1 矩阵
  (quiet 自适应 + finder 校验) 交 UI 画 rect — 不依赖 SDL_image/segno.
- 取 URL 必须登录态 (匿名全拦); 质量梯 FLAC→320→128→TRY 30s 试听 (MA 同款).
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import string
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import zlib

STATE_DIR = os.path.expanduser("~/.local/share/oneapp")
QQ_CRED_FILE = os.path.join(STATE_DIR, "qq_cred.json")
QQ_DEV_DIR = os.path.join(STATE_DIR, "qqdev")
NE_COOKIE_FILE = os.path.join(STATE_DIR, "ne_cookie.json")
NE_ANON_FILE = os.path.join(STATE_DIR, "ne_anon.json")
# 网易云直连 (2026-11-14 由双层链路收敛, 商店版无本地服务):
# 本文件自实现 weapi 协议直打 music.163.com, 七链实测全通:
# resolve(v1 兼容 enhance/player/url)/搜索/账号/likelist/song detail/
# 游客注册/QR unikey; cookie=MUSIC_U 存本机 180 天


def ne_ok():
    return _NE_CRYPTO


def _ne_https(u):
    """music.126.net 直链升 https (运营商/热点网络下 http 长流易被掐断,
    2026-11-14 热点播放断流嫌疑; https CDN 实测可取)."""
    if u and u.startswith("http://") and ".music.126.net/" in u:
        return "https://" + u[7:]
    return u


def qq_ok():
    """qqmusic-api 可用性 (懒探, 结果缓存)."""
    global _QQ_OK
    if _QQ_OK is None:
        try:
            import importlib.util
            _QQ_OK = importlib.util.find_spec("qqmusic_api") is not None
        except Exception:
            _QQ_OK = False
    return _QQ_OK


_QQ_OK = None


def qq_load_cred():
    """持久凭据 → Credential; 无/坏 → None."""
    try:
        with open(QQ_CRED_FILE) as f:
            d = json.load(f)
        from qqmusic_api.models.request import Credential
        return Credential(**d)
    except Exception:
        return None


def qq_save_cred(cred):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(QQ_CRED_FILE, "w") as f:
            f.write(cred.model_dump_json())
        try:
            os.chmod(QQ_CRED_FILE, 0o600)
        except Exception:
            pass
    except Exception:
        pass


def qq_clear_cred():
    try:
        os.remove(QQ_CRED_FILE)
    except Exception:
        pass


def qq_bound():
    return qq_ok() and qq_load_cred() is not None


def _qq_client():
    """async 上下文内: 带持久凭据+设备目录的 Client (登录态优先)."""
    from qqmusic_api import Client
    return Client(credential=qq_load_cred(), device_path=QQ_DEV_DIR)


def _norm_song(s, src="qq"):
    """lib Song → 曲目 dict (mu 引擎统一形状)."""
    album = getattr(s, "album", None)
    amid = getattr(album, "mid", None) if album is not None else None
    singers = [getattr(g, "name", "") for g in (getattr(s, "singer", None) or [])
               if getattr(g, "name", None)]
    fobj = getattr(s, "file", None)
    vs = [v for v in (getattr(s, "vs", None) or []) if v]
    return {
        "src": src,
        "id": getattr(s, "mid", None) or "",
        "title": getattr(s, "name", "") or "",
        "artist": "/".join(singers),
        "cover": ("https://y.gtimg.cn/music/photo_new/T002R500x500M000%s.jpg"
                  % amid) if amid else "",
        "dur": int(getattr(s, "interval", 0) or 0),
        "song_type": getattr(s, "type", 0) or 0,
        "media_mid": (getattr(fobj, "media_mid", "") or "") if fobj else "",
        "vs0": vs[0] if vs else "",
        "url": None,                        # 播放时解析 (短时效)
        "preview": False,
    }


def _qq_extract_url(resp, mid):
    """get_song_urls 响应 → 流 URL (pydantic/裸 dict 两形态; sip+purl 拼接)."""
    base = "https://isure.stream.qqmusic.qq.com/"
    items = getattr(resp, "data", None)
    if items is None and isinstance(resp, dict):
        items = resp.get("data") or resp.get("midurlinfo") or []
    for it in items or []:
        purl = str(getattr(it, "purl", "") or
                   (it.get("purl", "") if isinstance(it, dict) else ""))
        smid = str(getattr(it, "songmid", "") or
                   (it.get("songmid", "") if isinstance(it, dict) else ""))
        if smid and smid != mid:
            continue
        if purl:
            return purl if purl.startswith("http") else base + purl.lstrip("/")
    return None


def qq_search(q, num=12):
    """匿名搜索 → [曲目 dict] (线程内调用, 独立事件循环)."""
    async def f():
        from qqmusic_api.modules.search import SearchType
        client = _qq_client()
        try:
            r = await client.search.search_by_type(q, SearchType.SONG, num=num)
            return [_norm_song(s) for s in (getattr(r, "song", None) or [])]
        finally:
            await client.close()
    return asyncio.run(f())


def qq_resolve_url(item):
    """播放时解析 → (url, preview). 质量梯 FLAC→320→128, 全败落 TRY 30s
    试听 (MA 同款); 匿名/失效凭据下多数曲目只有 TRY."""
    async def f():
        from qqmusic_api.modules.song import (SongFileInfo, SongFileType,
                                              SpecialSongFileType)
        client = _qq_client()
        try:
            cred = client.credential
            fi = [SongFileInfo(item["id"], song_type=item.get("song_type") or 0,
                               media_mid=item.get("media_mid") or None)]
            for ft in (SongFileType.FLAC, SongFileType.MP3_320,
                       SongFileType.MP3_128):
                url = _qq_extract_url(
                    await client.song.get_song_urls(fi, file_type=ft,
                                                    credential=cred),
                    item["id"])
                if url:
                    return url, False
            if item.get("vs0"):
                fi2 = [SongFileInfo(item["id"],
                                    song_type=item.get("song_type") or 0,
                                    media_mid=item["vs0"])]
                url = _qq_extract_url(
                    await client.song.get_song_urls(
                        fi2, file_type=SpecialSongFileType.TRY,
                        credential=cred),
                    item["id"])
                if url:
                    return url, True
            return None, False
        finally:
            await client.close()
    return asyncio.run(f())


def qq_favorites(num=30):
    """我喜欢 (凭据; euin=encryptUin) → [曲目 dict]; 未登录/失败 → []."""
    if not qq_bound():
        return []

    async def f():
        client = _qq_client()
        try:
            cred = client.credential
            euin = getattr(cred, "encrypt_uin", "") or ""
            if not euin:
                return []
            resp = await client.user.get_fav_song(euin, page=1, num=num,
                                                  credential=cred)
            songs = getattr(resp, "songs", None) or []
            return [_norm_song(s) for s in songs]
        finally:
            await client.close()
    try:
        return asyncio.run(f())
    except Exception:
        return []


def qq_refresh_if_needed():
    """musickey 3 天滚动续期 (check_expired → refresh → 持久化); 失败静默."""
    if not qq_bound():
        return

    async def f():
        client = _qq_client()
        try:
            if await client.login.check_expired():
                cred = await client.login.refresh_credential()
                if cred:
                    qq_save_cred(cred)
        finally:
            await client.close()
    try:
        asyncio.run(f())
    except Exception:
        pass


def qq_qr_matrix(png_bytes):
    """lib QR PNG (灰度 1bit colortype0) → 0/1 矩阵 (dark=1, 含 finder 校验).
    尺寸/quiet 区随内容变: 按首个 finder 行的暗程长推模块宽, quiet 自适应."""
    i = 8
    idat = b""
    w = h = None
    while i + 8 <= len(png_bytes):
        ln = int.from_bytes(png_bytes[i:i + 4], "big")
        typ = png_bytes[i + 4:i + 8]
        d = png_bytes[i + 8:i + 8 + ln]
        if typ == b"IHDR":
            w = int.from_bytes(d[0:4], "big")
            h = int.from_bytes(d[4:8], "big")
        elif typ == b"IDAT":
            idat += d
        i += 12 + ln
    if not (w and h and idat):
        return None
    try:
        raw = zlib.decompress(idat)
    except Exception:
        return None
    stride = (w + 7) // 8
    rows = []
    pos = 0
    prev = bytearray(stride)
    for _ in range(h):
        if pos >= len(raw):
            return None
        ft = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if len(line) < stride:
            return None
        if ft == 1:
            for x in range(1, stride):
                line[x] = (line[x] + line[x - 1]) & 255
        elif ft == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        elif ft == 3:
            for x in range(stride):
                a = line[x - 1] if x >= 1 else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 255
        elif ft == 4:
            for x in range(stride):
                a = line[x - 1] if x >= 1 else 0
                b = prev[x]
                c = prev[x - 1] if x >= 1 else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 255
        rows.append(bytes(line))
        prev = line

    def bit(x, y):                          # PNG 位 0=黑 → dark=1
        return 1 - ((rows[y][x >> 3] >> (7 - (x & 7))) & 1)

    qy = next((y for y in range(h) if any(bit(x, y) for x in range(w))), None)
    if qy is None:
        return None
    qx = next((x for x in range(w) if bit(x, qy)), None)
    if qx is None:
        return None
    run = 0
    while qx + run < w and bit(qx + run, qy):
        run += 1
    if run % 7 or run < 14:                 # finder 顶行 = 7 模块
        return None
    px = run // 7
    n = int(round((w - 2 * qx) / px))
    if n < 21 or n > 60:
        return None
    mat = [[bit(qx + int(mx * px), qy + int(my * px)) for mx in range(n)]
           for my in range(n)]
    if mat[0][:7] != [1] * 7 or mat[6][:7] != [1] * 7:   # finder 校验
        return None
    return mat


class QQLogin:
    """后台扫码线程: 造 QR (矩阵) + 轮询 check_qrcode.

    state: idle → qr(矩阵就绪) → scanned(已扫待确认) → ok / fail
    过期自动重造新码 (MA 同款, 120s 窗口). 回调写实例属性, UI 线程只读."""

    def __init__(self):
        self.state = "idle"                 # idle|qr|scanned|ok|fail
        self.matrix = None
        self.err = ""
        self._stop = threading.Event()
        self._th = None

    def start(self, login_type="qq"):
        self.stop()
        if not qq_ok():
            self.state = "fail"
            self.err = "需 pip3 install qqmusic-api-python==0.7.2"
            return
        self._stop.clear()
        self.state = "qr"
        self.err = ""
        self._th = threading.Thread(target=self._run,
                                    args=(login_type,), daemon=True)
        self._th.start()

    def stop(self):
        self._stop.set()
        self._th = None
        self.state = "idle"

    def _run(self, login_type):
        async def flow():
            from qqmusic_api.models.login import (QRCodeLoginEvents,
                                                  QRLoginType)
            lt = QRLoginType.WX if login_type == "wx" else QRLoginType.QQ
            client = _qq_client()
            try:
                while not self._stop.is_set():
                    qr = await client.login.get_qrcode(lt)
                    self.matrix = qq_qr_matrix(bytes(qr.data))
                    self.state = "qr"
                    deadline = time.time() + 120
                    while not self._stop.is_set() and time.time() < deadline:
                        r = await client.login.check_qrcode(qr)
                        ev = r.event
                        if ev == QRCodeLoginEvents.DONE and r.credential:
                            qq_save_cred(r.credential)
                            self.state = "ok"
                            return
                        if ev in (QRCodeLoginEvents.SCAN,
                                  QRCodeLoginEvents.CONF):
                            if self.state != "scanned":
                                self.state = "scanned"
                        elif ev == QRCodeLoginEvents.TIMEOUT:
                            break           # 过期 → 重造新码
                        elif ev == QRCodeLoginEvents.REFUSE:
                            self.state = "fail"
                            self.err = "手机端拒绝了登录"
                            return
                        await asyncio.sleep(1.5)
            except Exception as e:          # 网络/库错误 → 可见失败
                self.state = "fail"
                self.err = str(e)[:40]
            finally:
                try:
                    await client.close()
                except Exception:
                    pass
        asyncio.run(flow())


# ── M3 网易云 (自实现 weapi; QR unikey/client_login + song url v1 + 喜欢) ──
try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
    from cryptography.hazmat.primitives.ciphers import modes as _cmodes
    from cryptography.hazmat.primitives.padding import PKCS7 as _PKCS7
    from cryptography.hazmat.primitives.serialization import load_pem_public_key \
        as _loadpub
    _NE_CRYPTO = True
except Exception:
    _NE_CRYPTO = False

_NE_PRESET = b"0CoJUm6Qyw8W8jud"
_NE_EAPI_KEY = b"e82ckenh8dichen8"
_NE_IV = b"0102030405060708"
# 网易 weapi RSA 公钥 (NeteaseCloudMusicApi 公开常量)
_NE_PUB_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDgtQn2JZ34ZC28NWYpAUd98iZ37BUrX/aKzmFbt7cl"
    "FSs6sXqHauqKWqdtLkF2KexO40H1YTX8z2lSgBBOAxLsvaklV8k4cBFK9snQXE9/DDaFt6Rr7iVZMldc"
    "zhC0JNgTz+SHXT6CBHuX3e9SdB1Ua44oncaTWz7OBGLbCiK45wIDAQAB\n"
    "-----END PUBLIC KEY-----")


def ne_load_cookie():
    try:
        with open(NE_COOKIE_FILE) as f:
            d = json.load(f)
        return d.get("MUSIC_U") or ""
    except Exception:
        return ""


def ne_save_cookie(music_u):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(NE_COOKIE_FILE, "w") as f:
            json.dump({"MUSIC_U": music_u}, f)
        os.chmod(NE_COOKIE_FILE, 0o600)
    except Exception:
        pass


def ne_clear_cookie():
    try:
        os.remove(NE_COOKIE_FILE)
    except Exception:
        pass


def ne_bound():
    return ne_ok() and bool(ne_load_cookie())


def _ne_weapi(payload, secret="0CoJUm6Qyw8W8jud"):
    """weapi: AES-CBC 双层 + RSA 无填充 (NeteaseCloudMusicApi 同构)."""
    text = json.dumps(payload).encode()
    b62 = string.ascii_letters + string.digits
    sec = "".join(b62[secrets.randbelow(62)] for _ in range(16)).encode()
    b2 = base64.b64encode(_ne_aes_cbc(
        base64.b64encode(_ne_aes_cbc(text, secret.encode())), sec)).decode()
    pub = _loadpub(_NE_PUB_PEM.encode()).public_numbers()
    rsk = format(pow(int.from_bytes(sec[::-1], "big"), pub.e, pub.n),
                 "x").rjust(256, "0")
    return b2, rsk


def _ne_aes_cbc(data, key):
    p = _PKCS7(128).padder()
    pad = p.update(data) + p.finalize()
    e = Cipher(algorithms.AES(key), _cmodes.CBC(_NE_IV)).encryptor()
    return e.update(pad) + e.finalize()


def _ne_post(weapi_path, payload, cookie=""):
    """weapi POST → JSON (music.163.com/weapi/<path>; cookie 可带 MUSIC_U)."""
    return _ne_post_raw(weapi_path, payload, cookie)[0]


def _ne_absorb_cookies(jar, hdr):
    """把 Set-Cookie 头并入 jar (name→value); 逗号拆分时过滤 Expires 等
    属性项 — 多 Set-Cookie 被 urllib 合并成逗号串的粗粒度解析够用."""
    if not isinstance(hdr, str):
        return
    for part in hdr.split(","):
        nv = part.strip().split(";")[0]
        if "=" not in nv:
            continue
        k, v = nv.split("=", 1)
        k = k.strip()
        if k and k not in ("Expires", "Max-Age", "Path", "Domain", "Secure",
                           "HttpOnly", "SameSite"):
            jar[k] = v.strip()


def _ne_eapi_post(eapi_path, payload, cookie=""):
    """eapi POST (PC 客户端协议): md5 框架 + AES-ECB(e82...) 大写 hex."""
    text = json.dumps(payload)
    digest = hashlib.md5(("nobody%suse%smd5forencrypt"
                          % (eapi_path, text)).encode()).hexdigest()
    data = ("%s-36cd479b6b5-%s-36cd479b6b5-%s"
            % (eapi_path, text, digest)).encode()
    p = _PKCS7(128).padder()
    pad = p.update(data) + p.finalize()
    e = Cipher(algorithms.AES(_NE_EAPI_KEY), _cmodes.ECB()).encryptor()
    params = (e.update(pad) + e.finalize()).hex().upper()
    if isinstance(cookie, dict):
        cookie = "; ".join("%s=%s" % kv for kv in cookie.items())
    hdrs = {"Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://music.163.com/", "User-Agent": "Mozilla/5.0",
            "Cookie": (cookie + "; os=pc; appver=3.1.17.204416").lstrip("; ")}
    form = ("params=%s" % urllib.parse.quote(params)).encode()
    r = urllib.request.urlopen(urllib.request.Request(
        "https://music.163.com/eapi/" + eapi_path.lstrip("/"),
        data=form, headers=hdrs), timeout=10)
    return json.loads(r.read()), r.headers.get("Set-Cookie") or ""


def _ne_identity():
    """假浏览器身份 cookie (NeteaseCloudMusicApi processCookieObject 同构)."""
    import random as _r
    nuid = "".join(_r.choice("0123456789abcdef") for _ in range(32))
    ts = int(time.time() * 1000)
    wnm = "".join(_r.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(6))
    return {
        "__remember_me": "true",
        "ntes_kaola_ad": "1",
        "_ntes_nuid": nuid,
        "_ntes_nnid": "%s,%d" % (nuid, ts),
        "WNMCID": "%s.%d.01.0" % (wnm, ts),
        "WEVNSM": "1.0.0",
        "osver": "Microsoft-Windows-10-Professional-build-19045-64bit",
        "os": "pc",
        "channel": "netease",
        "appver": "3.1.17.204416",
    }


def _ne_post_raw(weapi_path, payload, cookie=""):
    """同 _ne_post, 另返回 HTTP Set-Cookie 头 (QR 803 的 MUSIC_U 在这里;
    get_all 合并 — 多个 Set-Cookie 头各带不同 cookie, MUSIC_A 在后续头)."""
    params, rsk = _ne_weapi(payload)
    hdrs = {"Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://music.163.com/", "User-Agent": "Mozilla/5.0",
            "Cookie": (cookie + "; os=pc; appver=2.9.7").lstrip("; ")}
    form = ("params=%s&encSecKey=%s"
            % (urllib.parse.quote(params), urllib.parse.quote(rsk))).encode()
    r = urllib.request.urlopen(urllib.request.Request(
        "https://music.163.com/weapi/" + weapi_path, data=form, headers=hdrs),
        timeout=10)
    return json.loads(r.read()), ";".join(r.headers.get_all("Set-Cookie")
                                          or [])


def _ne_encrypt_id(digit):
    """封面 CDN 路径加密 (id → /<enc>/<id>.jpg), 社区通用算法."""
    import hashlib
    magic = b"3go8&$8*3*3h0k(2)2"
    sid = bytearray(str(digit).encode())
    for i in range(len(sid)):
        sid[i] ^= magic[i % len(magic)]
    m = hashlib.md5(bytes(sid)).digest()
    return base64.b64encode(m).replace(b"/", b"_").replace(b"+", b"-").decode()


# ── 直连层 (后端不可达/外网): 本机 weapi 直打 music.163.com ──────────────

def _ne_ck(music_u=""):
    """直连请求 cookie: 登录态 MUSIC_U 或游客态 MUSIC_A + 桌面身份."""
    anon = _ne_anon_token()
    base = ("os=pc; appver=3.1.17.204416; osver=Microsoft-Windows-10-"
            "Professional-build-19045-64bit; channel=netease")
    if music_u:
        return base + "; MUSIC_U=" + music_u
    return base + ("; MUSIC_A=" + anon if anon else "")


def _ne_anon_token(refresh=False):
    """游客 token (MUSIC_A): 无它时扫码确认只回 MUSIC_A_T 不发 MUSIC_U
    (2026-11-14 绑定闭环根因). 存 ne_anon.json; weapi register/anonimous
    直连注册 (deviceId xor-md5 同构, alger 同形状)."""
    if not refresh:
        try:
            with open(NE_ANON_FILE) as f:
                v = (json.load(f) or {}).get("MUSIC_A")
            if v:
                return v
        except Exception:
            pass
    if ne_ok():
        try:
            dev = "".join(secrets.choice("0123456789ABCDEF")
                          for _ in range(40))
            enc = base64.b64encode(
                ("%s %s" % (dev, _ne_encrypt_id(dev))).encode()).decode()
            _body, hdr = _ne_post_raw("register/anonimous",
                                      {"username": enc}, "")
            m = re.search(r"MUSIC_A=([^;]+)", hdr or "")
            if m:
                _ne_anon_save(m.group(1))
                return m.group(1)
        except Exception:
            pass
    return ""


def _ne_anon_save(token):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(NE_ANON_FILE, "w") as f:
            json.dump({"MUSIC_A": token, "ts": int(time.time())}, f)
        os.chmod(NE_ANON_FILE, 0o600)
    except Exception:
        pass


def _ne_direct_resolve(sid, ck):
    """直连 weapi song/enhance/player/url → (url, preview) 或 (None, False)
    (后端旧 /song/url 同上游; 2026-11-14 实测 320k 完整流非试听)."""
    d = _ne_post("song/enhance/player/url",
                 {"ids": "[%d]" % sid, "br": 320000}, _ne_ck(ck))
    d0 = (d.get("data") or [{}])[0]
    if d0.get("url"):
        return d0["url"], bool(d0.get("freeTrialInfo"))
    return None, False


def _ne_direct_search(q, num, ck):
    """直连 weapi search/get (带 cookie 可见 VIP) → 曲目列表."""
    d = _ne_post("search/get",
                 {"s": q, "type": 1, "limit": num, "offset": 0}, _ne_ck(ck))
    return [_norm_ne(x) for x in
            ((d.get("result") or {}).get("songs") or [])]


def _ne_direct_favorites(num, ck):
    """直连收藏链: 公开 api account (plain GET) → weapi song/like/get →
    weapi v3/song/detail (c=JSON 形状, 逗号形状会被 400)."""
    req = urllib.request.Request(
        "https://music.163.com/api/nuser/account/get",
        headers={"Cookie": _ne_ck(ck), "Referer": "https://music.163.com/",
                 "User-Agent": "Mozilla/5.0"})
    acc = json.loads(urllib.request.urlopen(req, timeout=10).read())
    uid = (acc.get("profile") or {}).get("userId") \
        or ((acc.get("data") or {}).get("profile") or {}).get("userId")
    if not uid:
        return []
    ids = (_ne_post("song/like/get", {"uid": uid}, _ne_ck(ck))
           .get("ids") or [])[:num]
    if not ids:
        return []
    c = "[" + ",".join('{"id":%d}' % int(i) for i in ids) + "]"
    d = _ne_post("v3/song/detail", {"c": c}, _ne_ck(ck))
    return [_norm_ne(x) for x in (d.get("songs") or [])]


def _norm_ne(s):
    """网易云 song dict (search/detail) → 曲目 dict."""
    artists = "/".join(a.get("name", "") for a in (s.get("artists")
                     or s.get("ar") or []) if a.get("name"))
    alb = s.get("album") or s.get("al") or {}
    pic = alb.get("picUrl") or ""
    pid = alb.get("picId") or alb.get("pic") or ""
    if not pic and pid:
        try:
            pic = ("https://p3.music.126.net/%s/%s.jpg"
                   % (_ne_encrypt_id(pid), pid))
        except Exception:
            pic = ""
    dur = s.get("duration") or s.get("dt") or 0
    return {
        "src": "ne",
        "id": str(s.get("id", "")),
        "title": s.get("name", "") or "",
        "artist": artists,
        "cover": pic,
        "dur": int((dur or 0) / 1000),
        "url": None,
        "preview": False,
    }


def ne_search(q, num=12):
    """搜索: ① 直连 weapi (带 cookie 可见 VIP) ② 公开 api (免登录直连,
    无 VIP 标记)."""
    ck = ne_load_cookie()
    if ne_ok():
        try:
            res = _ne_direct_search(q, num, ck)
            if res:
                return res
        except Exception:
            pass
    url = ("https://music.163.com/api/search/get?s=%s&type=1&limit=%d"
           % (urllib.parse.quote(q), num))
    req = urllib.request.Request(url, headers={
        "Referer": "https://music.163.com/", "User-Agent": "Mozilla/5.0"})
    d = json.loads(urllib.request.urlopen(req, timeout=10).read())
    return [_norm_ne(x) for x in (d.get("result", {}).get("songs") or [])]


def ne_resolve_url(item):
    """播放解析: ① 直连 weapi song/enhance/player/url (登录态 exhigh=320k
    完整流非试听; lossless 档网易必 -460 风控, 2026-11-14 实测) ② outer
    免登录兜底."""
    sid = int(item["id"])
    ck = ne_load_cookie()
    if ck and ne_ok():
        try:
            u, p = _ne_direct_resolve(sid, ck)
            if u:
                return _ne_https(u), p
        except Exception:
            pass
    req = urllib.request.Request(
        "https://music.163.com/song/media/outer/url?id=%d.mp3" % sid,
        headers={"User-Agent": "Mozilla/5.0"})
    r = urllib.request.urlopen(req, timeout=10)
    final = r.geturl()
    clen = int(r.headers.get("Content-Length") or 0)
    r.close()
    if final and "outer" not in final and clen > 30000:
        return _ne_https(final), False
    return None, False


def ne_favorites(num=200):
    """我喜欢直连同构链: api/nuser/account/get + weapi song/like/get +
    weapi v3/song/detail (c=JSON 形状, 逗号形状会 400)."""
    ck = ne_load_cookie()
    if not ck or not ne_ok():
        return []
    try:
        return _ne_direct_favorites(num, ck)
    except Exception:
        return []


def lrc_fetch(title, artist, dur=0):
    """歌词直连 (替代家庭中继 /lyrics): ① LRCLIB 四级回退 (断路器: 连续
    失败≥2 停 10min — 上游黑洞/限流不让每请求干等超时) ② 网易公开歌词
    API 兜底 (国内直连, 中文覆盖好). 返回 {"synced": LRC|None, "plain": ...}."""
    out = _lrc_lrclib(title, artist, dur)
    if out and (out.get("synced") or out.get("plain")):
        return out
    try:
        res = ne_search(title, num=3)
        for it in res:
            if artist and artist not in (it.get("artist") or ""):
                continue
            req = urllib.request.Request(
                "https://music.163.com/api/song/lyric?id=%s&lv=1&kv=1&tv=-1"
                % it["id"],
                headers={"Referer": "https://music.163.com/",
                         "User-Agent": "Mozilla/5.0"})
            d = json.loads(urllib.request.urlopen(req, timeout=8).read())
            lrc = (d.get("lrc") or {}).get("lyric") or ""
            if lrc and "[00:00" in lrc:
                return {"synced": lrc, "plain": None}
    except Exception:
        pass
    return out or {"synced": None, "plain": None}


_LRCLIB = "https://lrclib.net/api"
_LRC_BRK = {"fails": 0, "until": 0.0}      # 断路器 (radio_relay 同款)


def _lrc_dead():
    return time.monotonic() < _LRC_BRK["until"]


def _lrc_mark(ok):
    if ok:
        _LRC_BRK["fails"] = 0
        _LRC_BRK["until"] = 0.0
        return
    _LRC_BRK["fails"] += 1
    if _LRC_BRK["fails"] >= 2:
        _LRC_BRK["until"] = time.monotonic() + 600


def _lrc_call(path, query):
    """LRCLIB GET, 503 重试一次 (限流突发); 健康度回写断路器."""
    url = "%s/%s?%s" % (_LRCLIB, path, urllib.parse.urlencode(query))
    req = urllib.request.Request(url, headers={"User-Agent": "ONEAPP/1.0"})
    for attempt in (0, 1):
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                _lrc_mark(True)
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                _lrc_mark(True)            # 404=正常未收录, 上游活着
                raise
            _lrc_mark(False)
            if e.code == 503 and attempt == 0:
                time.sleep(1.5)
                continue
            raise
        except Exception:
            _lrc_mark(False)               # 超时/连接失败=上游黑洞
            raise


def _lrc_pick(hits, duration):
    for h in hits:
        if h.get("syncedLyrics") or h.get("plainLyrics"):
            if duration and h.get("duration") and abs(h["duration"] - duration) <= 8:
                return h
    for h in hits:
        if h.get("syncedLyrics") or h.get("plainLyrics"):
            return h
    return None


def _lrc_lrclib(title, artist, duration):
    # LRCLIB 为境外源, 家宽偶发 GFW 波动 (2026-10-31 实锤) → 断路器保护
    if _lrc_dead():
        return None
    stages = []
    q0 = {"track_name": title}
    if artist:
        q0["artist_name"] = artist
    stages.append(("get", q0))
    stages.append(("search", q0))
    stages.append(("search", {"track_name": title}))
    for path, q in stages:
        try:
            r = _lrc_call(path, q)
        except urllib.error.HTTPError as e:
            if e.code == 404 and path == "get":
                continue
            break
        except Exception:
            break
        if path == "get" and r:
            return {"synced": r.get("syncedLyrics"),
                    "plain": r.get("plainLyrics")}
        if path == "search":
            h = _lrc_pick(r or [], duration)
            if h:
                return {"synced": h.get("syncedLyrics"),
                        "plain": h.get("plainLyrics")}
    return None


class NeLogin:
    """网易云扫码线程 (weapi unikey/client_login type3) — QQLogin 同接口."""

    def __init__(self):
        self.state = "idle"
        self.matrix = None
        self.err = ""
        self._stop = threading.Event()
        self._th = None

    def start(self, _kind=None):
        self.stop()
        if not ne_ok():
            self.state = "fail"
            self.err = "需 cryptography (pip3 install cryptography)"
            return
        self._stop.clear()
        self.state = "qr"
        self._th = threading.Thread(target=self._run, daemon=True)
        self._th.start()

    def stop(self):
        self._stop.set()
        self._th = None
        self.state = "idle"

    def _run(self):
        try:
            import segno
        except Exception:
            self.state = "fail"
            self.err = "需 pip3 install segno"
            return
        sess = ""
        unikey = ""
        if not ne_ok():
            self.state = "fail"
            self.err = "需 cryptography (pip3 install cryptography)"
            return
        sess = _ne_ck()                    # 会话 cookie 全程固定 (key/check
        try:                               #   同会话; 游客 token 缺失→只回
            body, _hdr = _ne_post_raw(     #   MUSIC_A_T, 2026-11-14 根因)
                "login/qrcode/unikey", {"type": 3}, sess)
            unikey = ((body.get("data") or {}).get("unikey")
                      or body.get("unikey"))
        except Exception as e:
            self.state = "fail"
            self.err = "unikey %s" % str(e)[:30]
            return
        if not unikey:
            self.state = "fail"
            self.err = "unikey 获取失败"
            return
        while not self._stop.is_set():
            q = segno.make("https://music.163.com/login?codekey=" + unikey,
                           error="m")
            self.matrix = [[1 if v else 0 for v in r] for r in q.matrix]
            self.state = "qr"
            deadline = time.time() + 300       # 网易码 5 分钟窗
            while not self._stop.is_set() and time.time() < deadline:
                try:
                    body, hdr = _ne_post_raw(
                        "login/qrcode/client/login",
                        {"key": unikey, "type": 3}, sess)
                    r = dict(body)
                    r["cookie"] = hdr
                except Exception as e:
                    try:
                        with open("/tmp/ne_debug.txt", "a") as df:
                            df.write(time.strftime("%H:%M:%S") +
                                     " POLL-EXC " + repr(e)[:120] + "\n")
                    except Exception:
                        pass
                    time.sleep(2.0)
                    continue
                code = r.get("code")
                if code == 803:                # 成功: MUSIC_U 在 cookie
                    ck = r.get("cookie") or ""
                    if isinstance(ck, list):
                        ck = ";".join(ck)
                    m = re.search(r"MUSIC_U=([^;]+)", ck)
                    if m:
                        ne_save_cookie(m.group(1))
                        self.state = "ok"
                        return
                    self.state = "fail"
                    self.err = "登录成功但未取到 MUSIC_U"
                    return
                if code == 802:
                    self.state = "scanned"
                elif code == 800:
                    break                      # 过期 → 不自动重造 (重跑要
                time.sleep(1.5)               #   新会话 → 用户重进扫码页)
        if not self._stop.is_set():            # 码过期 → 不自动重造
            self.state = "fail"
            self.err = "二维码过期, 重进扫码页"
