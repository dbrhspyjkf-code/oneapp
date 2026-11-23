#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ONEAPP — 工具箱 for M5Stack CardputerZero (320×170).

pygame 直写 /dev/fb_lcd + 裸 evdev 键盘 (与 sudokuzero 同款基建);
零本地服务依赖, 全部数据源为公网直连:
  天气 Open-Meteo · 行情 腾讯 qt.gtimg.cn · 新闻 RSS 源可配置 (news_rss.py,
  新闻页 S 设置视图 显隐/增删, kv 云同步)
  汇率 open.er-api.com · 彩票 双色球 cwl.gov.cn / 大乐透 500.com · 新股 东方财富
  电台 内置站台直连流 · 音乐 QQ/网易云 直连 (musrc.py) + 本地曲库
  歌词 LRCLIB + 网易兜底 (musrc.lrc_fetch)

键位:  1-5 切页   Z/C/F/X(丝印←→↑↓,非输入态) 翻页   t 换主题(仅主页)
       Fn+S/Fn+D 音量±  Fn+A 静音        主页长按 Esc 1s 退出
"""

import glob
import hashlib
import io
import json
import mmap
import os
import math
import random
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import sys
import time
import urllib.parse

import pygame

import fetchers
import ime
import lunar
import mail as mailmod
import musrc
import net as netmod
import news_rss

W, H = 320, 170

# ── 全局 DNS 双栈保护 (2026-11-17 Atour 酒店网实测): 网关劫持 53 端口后
#    对 AAAA 查询静默丢弃 (A 0.01s / AAAA 全家超时) → AF_UNSPEC 解析每连接
#    卡 15-20s, 全 app 联网超时假死. 设备恒无 IPv6, 解析强制 A-only
#    (进程级替换, net/mail/musrc/fetchers 全覆盖). ──
_orig_gai = socket.getaddrinfo


def _gai_v4(host, port, family=0, type=0, proto=0, flags=0):
    if family in (0, socket.AF_UNSPEC):
        family = socket.AF_INET
    return _orig_gai(host, port, family, type, proto, flags)


socket.getaddrinfo = _gai_v4


def _is_pcm_wav(path):
    """按内容魔数判 PCM wav (paplay 只吃 PCM; 扩展名不可靠 — 云端懒下载
    m4a/webm 曾统一落 .wav 名导致 paplay 播不了)."""
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"RIFF"
    except OSError:
        return False


def _ffplay_pre():
    """Linux: ffplay 随父进程死 (PR_SET_PDEATHSIG/SIGKILL), 防孤儿音频."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, 9)
    except Exception:
        pass


_FFPLAY_KW = ({"preexec_fn": _ffplay_pre}
              if sys.platform.startswith("linux") else {})
_AUDIO_ENV = dict(os.environ,
                  XDG_RUNTIME_DIR=os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"))


def hex2rgb(s):
    return (int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16))


_FF_RE_EXT = None


def _ff_reconnect_ext():
    """-reconnect_on_network_error 需 ffmpeg ≥4.4 (版本探测一次缓存; 老版
    给未知旗标会直接拒启, 不可盲加)."""
    global _FF_RE_EXT
    if _FF_RE_EXT is None:
        try:
            out = subprocess.run(["ffmpeg", "-version"], capture_output=True,
                                 timeout=5).stdout.decode(errors="replace")
            m = re.search(r"ffmpeg version (\d+)\.(\d+)", out)
            _FF_RE_EXT = bool(m) and (int(m.group(1)),
                                      int(m.group(2))) >= (4, 4)
        except Exception:
            _FF_RE_EXT = False
    return _FF_RE_EXT


def _ff_slow_net_flags():
    """慢网/热点断流自愈旗标: 15s 读写超时防无限挂 + 网络错自动重连."""
    flags = ["-rw_timeout", "15000000"]
    if _ff_reconnect_ext():
        flags += ["-reconnect_on_network_error", "1"]
    return flags


DARK_TXT = {"hi": "#F2F7F3", "soft": "#C7D6CB", "dim": "#7E9486"}
DARK_SEM = {"ok": "#34D399", "warn": "#FBBF24", "danger": "#EF4444"}
THEMES = [
    {"name": "松绿", "hero": "#204234", "accent": "#34D399", "press": "#2F8066",
     "cards": ["#1B362C", "#173024", "#13291F", "#0F221A"], "border": "#2C4433",
     "track": "#0A1811", "page": "#000000", **DARK_TXT, **DARK_SEM},
    {"name": "暖橙", "hero": "#6E3A16", "accent": "#F5A263", "press": "#B3661F",
     "cards": ["#3A2412", "#30200F", "#271A0C", "#1D1309"], "border": "#5C3A1C",
     "track": "#160D04", "page": "#000000", **DARK_TXT, **DARK_SEM},
    {"name": "海蓝", "hero": "#1C4258", "accent": "#4FA3FF", "press": "#2F6FA8",
     "cards": ["#16334A", "#122B40", "#0E2233", "#0A1A28"], "border": "#2A4A6A",
     "track": "#081522", "page": "#000000", **DARK_TXT, **DARK_SEM},
    {"name": "岩紫", "hero": "#3A2C55", "accent": "#B08CFF", "press": "#6A4FA0",
     "cards": ["#2B213F", "#251C37", "#1E162C", "#171022"], "border": "#453663",
     "track": "#120D1E", "page": "#000000", **DARK_TXT, **DARK_SEM},
    # 晨白/车机灰/纯黑: 取色自 car-dash 车机主题 (2026-11-15 对齐导入)
    {"name": "晨白", "hero": "#F4FAF6", "accent": "#1E9E6A", "press": "#DCEFE4",
     "cards": ["#FFFFFF", "#F7FAF8", "#F2F6F3", "#EDF2EE"], "border": "#D8E2DA",
     "track": "#E4ECE6", "page": "#EEF3EF",
     "hi": "#1A2B22", "soft": "#4A5D52", "dim": "#8AA094",
     "ok": "#18A05E", "warn": "#D99A12", "danger": "#DC4444"},
    {"name": "车机灰", "hero": "#ECEEF1", "accent": "#5F7FB8", "press": "#CFD5DE",
     "cards": ["#F2F4F6", "#EDEFF2", "#E8EAEE", "#E3E5E9"], "border": "#CBD0D8",
     "track": "#D6D9DF", "page": "#DEE0E4",
     "hi": "#262B33", "soft": "#555D6B", "dim": "#8A93A3",
     "ok": "#2FA26E", "warn": "#E8A23D", "danger": "#E05252"},
    {"name": "纯黑", "hero": "#0C0E11", "accent": "#7FA3D9", "press": "#26292E",
     "cards": ["#14161A", "#121418", "#0F1114", "#0B0D10"], "border": "#25282E",
     "track": "#17191E", "page": "#000000",
     "hi": "#F0F2F4", "soft": "#AEB4BC", "dim": "#6E747D",
     "ok": "#5B9BD5", "warn": "#E8A23D", "danger": "#E05252"},
]

STATE_DIR = os.path.expanduser("~/.local/share/oneapp")
THEME_FILE = os.path.join(STATE_DIR, "theme.json")

# 天气: 默认坐标 (Open-Meteo 公网直连)
WX_HOME = {"lat": 22.5629, "lon": 114.1207, "city": "深圳"}   # 定位失败回落
WX_LOC_FILE = os.path.join(STATE_DIR, "wx_loc.json")   # IP 归属地快照

# 电台输出: 本机 (ffplay→pulse) / AirPlay (pyatv RAOP 直推, dash 音乐 app
# 同款移植 — zero-dash 真机全链验证过)。设备音频服务器无 raop sink 模块
# (libspa-0.2-raop 不在 RPi OS 仓库), 走进程内发送: ffmpeg 解码电台流 →
# flac 管道 → atvremote stream=- → 音箱。依赖 pyatv==0.18.0 (设备 --user,
# Dash 已装); 缺库/无设备时 O 键回落本机。局域网自动发现 (mDNS 扫描)。
RAOP_CACHE = os.path.join(STATE_DIR, "raop_devs.json")
RADIO_OUT_FILE = os.path.join(STATE_DIR, "radio_out.json")
RADIO_FAV_CACHE = os.path.join(STATE_DIR, "radio_fav.json")   # 电台收藏 (key 表)
RAOP_VOL_FILE = os.path.join(STATE_DIR, "raop_vol.json")   # AirPlay 会话音量记忆
_RAOP_SKIP = re.compile(r"电视|tv|macbook|ipad|iphone", re.I)


def _raop_ok(d):
    """输出循环只留音箱: 滤电视/电脑/手机 (每按一次 O=杀进程重连 ~10s,
    中间噪音设备既慢又易触发会话冷却拒连)."""
    return bool(d.get("ip") and d.get("id")
                and not _RAOP_SKIP.search(d.get("name") or ""))


def _raop_local_net():
    """本机当前网段 ("192.168.1" 形态); 拿不到 → "" (调用方不过滤, 保守).
    UDP connect 不实际发包, 只取默认路由源地址; 无路由时放行不过滤."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("223.5.5.5", 80))
            return s.getsockname()[0].rsplit(".", 1)[0]
        finally:
            s.close()
    except Exception:
        return ""


# 子进程入口统一走 -c 包装: pyatv CLI 用 asyncio.get_event_loop(), py3.14
# 已移除隐式建环 (Mac 模拟 3.14 崩, 设备 3.13 也统一走这条, 一份代码两端跑)。
_ATVR_SCAN = r'''
import asyncio, json, sys
async def _s():
    from pyatv import scan
    from pyatv.const import Protocol
    loop = asyncio.get_event_loop()
    devs = []
    for atv in await scan(loop, timeout=float(sys.argv[1])):
        for s in atv.services:
            if s.protocol == Protocol.RAOP:
                devs.append({"ip": str(atv.address), "port": s.port,
                             "name": atv.name, "id": str(atv.identifier)})
    print(json.dumps(devs, ensure_ascii=False))
asyncio.run(_s())
'''
# 直播 PCM 直通库 (消费端用, 测试同源 exec): pyatv stream_file 的解码层对
# 无限直播管道根本不可用 — ma_decoder UNKNOWN 格式探测 (mp3 无魔数) 按实时
# 节奏无限扫流 → ma_decoder_init 永不返回 → stream_file 从未发出过一个音频
# 包 (2026-11-14 线程栈+aplog 实证; dash 音乐=有限文件探测会终止所以一直没
# 事)。绕开 miniaudio: 产端 -f s16le 直出 PCM, open_source patch 成直通
# AudioSource (readframes 阻塞全读, 真 EOF 才短读), 预读 64KB 作存活门。
# readframes 尾必须字节交换成大端: RTP L16 净荷是大端 (RFC 3551), pyatv
# 三个官源 (Buffered/Internet/File) 全以 _to_audio_samples 收尾做此交换;
# 直通版漏掉 = 全频道"沙沙声" (小端字节被当大端解读, 2026-11-14 真机听感
# 实证 — 帧流/握手全正常但内容全噪)。
_ATVR_PCM_LIB = r'''
import array
import asyncio
import sys
import pyatv.protocols.raop.audio_source as _as
from pyatv.interface import MediaMetadata

def _prefill(raw, target):
    # 读满 target 或 EOF; 返回 eof。产端没头/秒死在此暴露 (不烧音箱连接)
    n = 0
    while n < target:
        chunk = raw.read(min(65536, target - n))
        if not chunk:
            return True
        n += len(chunk)
    return False

def _to_l16(data):
    # s16le → RTP L16 大端; 同 pyatv _to_audio_samples 小端分支 (奇字节截齐)
    a = array.array("h", bytes(data[:len(data) // 2 * 2]))
    if sys.byteorder == "little":
        a.byteswap()
    return a.tobytes()

class PcmSource(_as.AudioSource):
    def __init__(self, raw, rate, ch, width, title):
        self.raw = raw
        self.rate = rate
        self.ch = ch
        self.width = width
        self.title = title
        self.loop = asyncio.get_event_loop()

    async def readframes(self, nframes):
        need = nframes * self.ch * self.width
        buf = bytearray()
        while len(buf) < need:
            chunk = await self.loop.run_in_executor(
                None, self.raw.read, need - len(buf))
            if not chunk:
                break
            buf.extend(chunk)
        return _to_l16(buf)

    async def get_metadata(self):
        return MediaMetadata(title=self.title or None)

    @property
    def sample_rate(self):
        return self.rate

    @property
    def channels(self):
        return self.ch

    @property
    def sample_size(self):
        return self.width

    @property
    def duration(self):
        return 0

    async def close(self):
        pass

def _patch_open_source(title):
    async def open_source(source, sample_rate, channels, sample_size):
        if (sample_rate, channels, sample_size) != (44100, 2, 2):
            raise RuntimeError("pcm feed fixed 44100/2/s16, got %s/%s/%s" % (
                sample_rate, channels, sample_size))
        return PcmSource(source, sample_rate, channels, sample_size, title)
    return open_source
'''
_ATVR_PLAY = _ATVR_PCM_LIB + r'''
import asyncio, os, signal, sys, threading
async def _s():
    from pyatv import connect
    from pyatv.conf import AppleTV, ManualService
    from pyatv.const import Protocol
    from pyatv.interface import MediaMetadata
    import pyatv.protocols.raop as _raop
    import pyatv.protocols.raop.audio_source as _as
    loop = asyncio.get_event_loop()
    stop_evt = asyncio.Event()
    signal.signal(signal.SIGTERM,
                  lambda *_: loop.call_soon_threadsafe(stop_evt.set))
    try:     # 预读存活门 (25s 上限): 产端没头/停摆直接退出, 不烧音箱连接
        if await asyncio.wait_for(
                loop.run_in_executor(
                    None, _prefill, sys.stdin.buffer, 65536), 25):
            return
    except Exception:
        return
    title = sys.argv[4] if len(sys.argv) > 4 else ""
    artist = sys.argv[5] if len(sys.argv) > 5 else ""   # 音乐: 歌手 (电台="")
    _as.open_source = _patch_open_source(title)   # PCM 直通 (绕开 miniaudio)
    _raop.open_source = _as.open_source           # raop 模块全局引用同步换
    conf = AppleTV(sys.argv[1], sys.argv[1])   # 免 mDNS 扫描直连 (省 ~8s)
    conf.add_service(ManualService(sys.argv[3] or None, Protocol.RAOP,
                                   int(sys.argv[2]), None))
    for _att in range(2):   # 快速切台: 旧会话 TEARDOWN 在途偶发拒连 → 重试一次
        try:
            atv = await asyncio.wait_for(connect(conf, loop), 20)  # 挂死变快败
            break
        except Exception:
            if _att:
                return
            await asyncio.sleep(3)
    try:                                 # 设备依赖组合下必坏 (srptools 1.0.1)
        print("RAOP-UP", flush=True)     # 出声锚点 (radio 无进度条, 仅日志用)
        md = MediaMetadata(title=title or None, artist=artist or None) \
            if (title or artist) else None
        if len(sys.argv) > 6 and sys.argv[6]:
            def _vol_ctl(path=sys.argv[6]):
                # 音量命令线程: SETVOL <0-100> → atv.audio.set_volume
                # (同 RTSP 会话 SET_PARAMETER = iPhone 播放条同款会话音量,
                # 直达音箱); 落盘成功回执 RAOPVOL <pyatv 视角音量>.
                try:
                    fd = os.open(path, os.O_RDONLY)   # 父端持 O_RDWR → 立即开
                    for line in os.fdopen(fd, "r"):
                        line = line.strip()
                        if not line.startswith("SETVOL "):
                            continue
                        try:
                            v = max(0.0, min(100.0, float(line.split()[1])))
                            fut = asyncio.run_coroutine_threadsafe(
                                atv.audio.set_volume(v), loop)
                            fut.result(timeout=5)
                            print("RAOPVOL %d" % int(atv.audio.volume),
                                  flush=True)
                        except Exception:
                            pass
                except Exception:
                    pass
            threading.Thread(target=_vol_ctl, daemon=True).start()
        # stream_task + stop_evt: SIGTERM → 优雅取消 stream → finally 走
        # atv.close() 真 RTSP TEARDOWN (python 默认 SIGTERM 立即死, 不跑
        # finally — 暴力拆连让音箱进冷却拒连, 换台秒死根因之一)
        stream_task = asyncio.ensure_future(atv.stream.stream_file(
            sys.stdin.buffer, metadata=md,
            override_missing_metadata=True))
        evt_task = asyncio.ensure_future(stop_evt.wait())
        await asyncio.wait({stream_task, evt_task},
                           return_when=asyncio.FIRST_COMPLETED)
        if not stream_task.done():
            stream_task.cancel()
            try:
                await stream_task
            except BaseException:        # CancelledError 非 Exception 子类
                pass
    finally:
        atv.close()
asyncio.run(_s())
'''


def raop_scan(timeout=12):
    """pyatv 子进程扫 RAOP 设备 → [{"ip","port","name","id"}]; 无 pyatv/
    无设备/超时 → []. Mac 模拟同样可扫 (发现 HomePod/Mac 接收器)."""
    p = None
    try:
        import importlib.util
        if importlib.util.find_spec("pyatv") is None:
            return []
        base = [sys.executable, "-c", _ATVR_SCAN, str(timeout)]
        # nice: pyatv 导入+zeroconf 扫描 ~15s 吃满一核, 降优先级保 UI/音频顺滑
        if shutil.which("nice"):
            base = ["nice", "-n", "15"] + base
        p = subprocess.Popen(base, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
        # 设备实测: 子进程扫完打印 JSON 后退出阶段挂死 (zeroconf 收尾不解) —
        # subprocess.run(timeout) 超时会把结果一起扔掉; 改读行即杀
        watchdog = threading.Timer(timeout + 20, p.kill)
        watchdog.daemon = True
        watchdog.start()
        line = p.stdout.readline()        # 扫完 (~timeout+导入) 即回, 不等退程
        watchdog.cancel()
        p.kill()
        if not line.strip():
            return []
        devs = json.loads(line.strip().splitlines()[-1])
        return [d for d in devs if _raop_ok(d)]
    except Exception:
        return []
    finally:
        if p:
            try:
                p.kill()
                p.stdout.close()
            except Exception:
                pass



def wx_url(loc):
    """按归属地坐标拼 Open-Meteo URL (loc 缺坐标 → 回落深圳)."""
    lat = loc.get("lat") if loc else None
    lon = loc.get("lon") if loc else None
    if lat is None or lon is None:
        lat, lon = WX_HOME["lat"], WX_HOME["lon"]
    return ("https://api.open-meteo.com/v1/forecast?latitude=%.4f"
            "&longitude=%.4f&current_weather=true" % (lat, lon))

FONT_CANDIDATES = [
    "/usr/share/APPLaunch/share/font/AlibabaPuHuiTi-3-55-Regular.ttf",   # 设备
    "/System/Library/Fonts/PingFang.ttc",                                # Mac 模拟
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "font.ttf"),
]

WEEK_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ── 字体 ────────────────────────────────────────────────────────────────────
_ft = None
def load_font():
    global _ft
    if _ft is not None:
        return _ft
    try:
        from pygame import freetype as ft
    except Exception:
        from pygame import _freetype as ft
    ft.init()
    for p in FONT_CANDIDATES:
        try:
            _ft = ft.Font(p)
            _ft.origin = False
            return _ft
        except Exception:
            continue
    raise RuntimeError("no CJK font found: %s" % FONT_CANDIDATES)


_cache = {}
def _render(s, size, color):
    key = (s, size, color)
    r = _cache.get(key)
    if r is None:
        if len(_cache) > 600:               # 动态文本防膨胀
            _cache.clear()
        f = load_font()
        f.size = size
        r = f.render(s, fgcolor=color)[0]
        _cache[key] = r
    return r


def textw(s, size):
    return _render(str(s), size, (255, 255, 255)).get_width()


def texth(s, size):
    return _render(str(s), size, (255, 255, 255)).get_height()


_glyph_vbox = {}                          # (s, size) → 墨迹上下沿 (行框内)
def glyph_vbox(s, size):
    """渲染 s 取 alpha 包围盒的上下沿 — 行框垂直居中用 (字面框≠墨迹框, 同 _ball_glyph_cy)."""
    key = (s, size)
    b = _glyph_vbox.get(key)
    if b is None:
        r = _render(s, size, (255, 255, 255))
        w, h = r.get_size()
        ys = [y for y in range(h) for x in range(w)
              if r.get_at((x, y))[3] > 40]
        b = (min(ys), max(ys)) if ys else (0, h - 1)
        _glyph_vbox[key] = b
    return b


def text(surf, s, size, color, xy, right=False):
    r = _render(str(s), size, color)
    x, y = xy
    if right:
        x -= r.get_width()
    surf.blit(r, (x, y))


def textc(surf, s, size, color, cx, y):
    r = _render(str(s), size, color)
    surf.blit(r, (cx - r.get_width() // 2, y))


_ball_glyph = {}                          # fs → 数字字形在行框内的垂直中心
def _ball_glyph_cy(fs):
    """渲染 "05" 扫 alpha 包围盒求字形垂直中心 (球内数字居中用), 按字号缓存."""
    cy = _ball_glyph.get(fs)
    if cy is None:
        r = _render("05", fs, (255, 255, 255))
        w, h = r.get_size()
        ys = [y for y in range(h) for x in range(w)
              if r.get_at((x, y))[3] > 40]
        cy = (min(ys) + max(ys)) / 2 if ys else h / 2
        _ball_glyph[fs] = cy
    return cy


_clip_cache = {}
def _split_width_segs(txt, size, vw):
    """按像素宽把文本切成 ≤vw 的段列表 (分段轮播显示用)."""
    segs, cur = [], ""
    for chx in txt:
        if textw(cur + chx, size) <= vw:
            cur += chx
        else:
            segs.append(cur)
            cur = chx
    if cur:
        segs.append(cur)
    return segs or [""]


def _cloud_id():
    """云端条目 id (12 hex): 客户端生成 → 幂等上传/断网重传不产生重复."""
    return "".join(random.choice("0123456789abcdef") for _ in range(12))


def clip_text(s, size, maxw):
    """截断到 maxw 像素宽, 超出加省略号.
    邮件 preview 是 800 字符全文, 逐字符回退测宽是 O(n²) 渲染
    (每帧上千次 render → 主循环冻结 CPU 110%), 改预截断+二分, 结果按参数缓存."""
    s0 = str(s)
    key = (s0, size, maxw)
    hit = _clip_cache.get(key)
    if hit is not None:
        return hit
    if textw(s0, size) <= maxw:
        out = s0
    else:
        cap = int(maxw * 2) + 4             # 预截断上界按最窄字形 ≥0.5px 算
        if len(s0) > cap:                   # (CJK=size px, 拉丁约半宽, cap 截太紧会少留字)
            s0 = s0[:cap]
        lo, hi = 0, len(s0)
        while lo < hi:                      # 最长能放下 "…" 的前缀
            mid = (lo + hi + 1) // 2
            if textw(s0[:mid] + "…", size) <= maxw:
                lo = mid
            else:
                hi = mid - 1
        out = s0[:lo] + "…"
    if len(_clip_cache) > 2000:
        _clip_cache.clear()
    _clip_cache[key] = out
    return out


_tail_cache = {}
def tail_text(s, size, maxw):
    """保留末段截断到 maxw (前缀 …): URL 输入框用 — 正在敲的是尾部,
    头部截掉才知道输入对不对. 与 clip_text 同款缓存+二分, 不逐字符测宽."""
    s0 = str(s)
    key = (s0, size, maxw)
    hit = _tail_cache.get(key)
    if hit is not None:
        return hit
    if textw(s0, size) <= maxw:
        out = s0
    else:
        # fits(hi) 随 hi 增大而成立 (尾越短越放得下), 找最小可放 hi = 最长可见尾
        lo, hi = 0, len(s0) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if textw("…" + s0[mid:], size) <= maxw:
                hi = mid
            else:
                lo = mid + 1
        out = "…" + s0[lo:]
    if len(_tail_cache) > 2000:
        _tail_cache.clear()
    _tail_cache[key] = out
    return out


_wrap_cache = {}
def _wrap_lines_impl(s_, size, maxw, n):
    """贪心折行: 每行二分找最长可放前缀 (输出与旧逐字符实现一致)."""
    s = str(s_)
    all_lines = []
    while s:
        if textw(s, size) <= maxw:
            all_lines.append(s)
            break
        lo, hi = 1, len(s)
        while lo < hi:                      # 最大 k 使 s[:k] 放得下 (k≥1, 超宽单字独占一行)
            mid = (lo + hi + 1) // 2
            if textw(s[:mid], size) <= maxw:
                lo = mid
            else:
                hi = mid - 1
        all_lines.append(s[:lo])
        s = s[lo:]
    if len(all_lines) <= n:
        return all_lines
    out = all_lines[:n - 1]
    rest = "".join(all_lines[n - 1:])
    out.append(clip_text(rest, size, maxw))
    return out


# ── 行情: 腾讯 qt 文本解析 / 指数表 (与 car-dash 同源) ──────────────────────
IDX_LIST = [
    ("上证指数", "sh000001"), ("深证成指", "sz399001"), ("创业板指", "sz399006"),
    ("科创综指", "sh000688"), ("沪深300", "sh000300"), ("恒生指数", "r_hkHSI"),
    ("道琼斯", "usDJI"), ("纳斯达克", "usIXIC"),
]
QT_BASE = "https://qt.gtimg.cn/q="
IFZQ_BASE = "https://web.ifzq.gtimg.cn/appstock/app/"
HF_CODES = ["hf_XAU", "hf_XAG", "hf_AHD", "hf_CAD",
            "hf_NID", "hf_SND", "hf_CL"]
CMD_LIST = [("比特币", None), ("伦敦金", "hf_XAU"), ("伦敦银", "hf_XAG"),
            ("伦铝", "hf_AHD"), ("伦铜", "hf_CAD"), ("伦镍", "hf_NID"),
            ("伦锡", "hf_SND"), ("纽约原油", "hf_CL")]
GATE_URL = "https://api.gateio.ws/api/v4/spot/tickers?currency_pair=BTC_USDT"


def qt_code(code):
    """→ qt 代码 (幂等): 已带 sh/sz/bj 前缀原样; 裸 6 位按首位补前缀."""
    c = str(code)
    if c[:2] in ("sh", "sz", "bj"):
        return c
    return ("sh" if c[0] == "6" else "sz") + c


def norm_stock_code(s):
    """添加自选股输入 → qt 代码: sh/sz/bj+6位原样; 裸 6 位按首位补
    (5/6/9→sh, 0/3→sz, 4/8→bj). 无效返回 None."""
    s = (s or "").strip().lower()
    if re.match(r"^(sh|sz|bj)[0-9]{6}$", s):
        return s
    if re.match(r"^[0-9]{6}$", s):
        pre = "sh" if s[0] in "569" else ("bj" if s[0] in "48" else "sz")
        return pre + s
    return None


def parse_qt_text(txt):
    """v_code="f0~f1~..." → {code: [fields]}, GBK 已在 net 层解码."""
    import re as _re
    out = {}
    for m in _re.finditer(r'v_([a-zA-Z0-9_]+)="([^"]*)"', txt or ""):
        out[m.group(1)] = m.group(2).split("~")
    return out


def parse_hf_text(txt):
    """外盘期货 v_hf_XAU="价,涨跌%,..." → {code: [fields]} (逗号分隔)."""
    import re as _re
    out = {}
    for m in _re.finditer(r'v_(hf_[A-Za-z0-9_]+)="([^"]*)"', txt or ""):
        out[m.group(1)] = m.group(2).split(",")
    return out


def qt_ok(f):
    try:
        return f is not None and float(f[3]) > 0 and float(f[4]) > 0
    except (ValueError, IndexError):
        return False


def qt_num(f, i):
    try:
        return float(f[i])
    except (ValueError, IndexError):
        return 0.0


def parse_minute(j, code):
    """ifzq 分时 → 价格序列 (港美指无分时返回 [])."""
    try:
        node = j["data"][code]["data"]
        rows = node.get("data") or []
        out = []
        for r in rows:
            p = str(r).split(" ")
            if len(p) > 1:
                try:
                    out.append(float(p[1]))
                except ValueError:
                    pass
        return out
    except (KeyError, TypeError, AttributeError):
        return []


def parse_day(j, code):
    """ifzq 日K(qfq) → [(date, open, close, high, low), ...]."""
    try:
        node = j["data"][code]
        arr = node.get("qfqday") or node.get("day") or []
        out = []
        for r in arr:
            if isinstance(r, list) and len(r) >= 5:
                try:
                    out.append((r[0], float(r[1]), float(r[2]),
                                float(r[3]), float(r[4])))
                except ValueError:
                    pass
        return out
    except (KeyError, TypeError, AttributeError):
        return []


# ── 电池 (bq27220 电量计, 30s 缓存) ─────────────────────────────────────────
_BATT_DIR = "/sys/class/power_supply/bq27220-0"
_batt_cache = (0.0, (None, ""))


def battery_state():
    """→ (pct 0-100|None, status)."""
    global _batt_cache
    import time as _t
    now = _t.monotonic()
    if now - _batt_cache[0] < 30:
        return _batt_cache[1]
    pct, st = None, ""
    try:
        pct = int(open(_BATT_DIR + "/capacity").read().strip())
        st = open(_BATT_DIR + "/status").read().strip()
    except Exception:
        pass
    _batt_cache = (now, (pct, st))
    return pct, st


# ── 天气 WMO code → 中文 (与 car-dash wxCodeZh 一致) ────────────────────────
def wx_code_zh(c):
    m = {0: "晴", 1: "晴", 2: "多云", 3: "阴", 45: "雾", 48: "雾",
         51: "小雨", 53: "小雨", 55: "小雨", 56: "冻雨", 57: "冻雨",
         61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
         71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
         80: "阵雨", 81: "阵雨", 82: "强阵雨", 85: "阵雪", 86: "阵雪",
         95: "雷阵雨", 96: "雷雨", 99: "雷雨"}
    return m.get(c, "")


# ── 应用 ────────────────────────────────────────────────────────────────────
PAGES = [("主页", ""), ("想法", ""), ("电台", ""), ("行情", ""), ("新闻", ""), ("工具", "")]
MODE_LABEL = {"online": ("在线", "ok"), "offline": ("离线", "danger"),
              "probing": ("…", "dim")}
# 新闻源表: 内置 7 源种子 news_rss.defaults() → news_feeds.json (S 设置视图
# 显隐/增删, kv 云同步); 运行期 [(id,name)] 由 app.news_srcs() 动态给出
NEWS_FEEDS_FILE = os.path.join(STATE_DIR, "news_feeds.json")  # 源表 (kv 云同步)
NEWS_FEEDS_MAX = 16                     # 源数上限 (并发抓取时长可控)

# ── 工具页: app 卡片入口 (卡片=名字+快捷键, 5×2 网格, Z/C/F/X(丝印←→↑↓) 移动
#    光标, Enter 打开, 数字直开, Esc 子应用返回网格) ──
TOOL_APPS = [("fx", "汇率"), ("unit", "换算"),
             ("cal", "万年历"), ("lot", "彩票"), ("timer", "计时器"),
             ("music", "音乐"), ("tv", "电视"),
             ("sched", "日程"), ("set", "设置")]   # 日程=8 设置=9 (2026-11-16 定版: 设置挪日程后)
# 邮件不在工具网格 (2026-11-16 用户定版): 主页底栏 9 邮件 直达
TOOL_GRID = 5                            # 每行卡片数
MAIL_BODY_ROWS = 5                       # 邮件详情每屏正文行数 (13px, 用户定版原生字号)
SETTINGS_FILE = os.path.join(STATE_DIR, "settings.json")   # 设置: API KEY + 主页卡片显隐
SETTINGS_FILE = os.path.join(STATE_DIR, "settings.json")   # 设置: API KEY + 主页卡片显隐

# 音乐 app: 本地曲库 (mp3/flac/wav 直读) + QQ/网易云直连 provider (musrc.py,
# 扫码绑定后走同一播放页), 输出仅本机 (商店版无 AirPlay/音箱遥控)。
MUSIC_DIR = os.path.expanduser("~/music")
MUSIC_EXTS = (".mp3", ".flac", ".wav")
MUSIC_SRCS = [("local", "本地曲库", ""), ("qq", "QQ 音乐", ""),
              ("ne", "网易云", "")]
MU_LAST_CACHE = os.path.join(STATE_DIR, "mu_last.json")  # 上次播放会话 (跨重启)
MU_OUT_CACHE = os.path.join(STATE_DIR, "mu_out.json")   # 音乐输出 (按设备 id)
TV_LAST_CACHE = os.path.join(STATE_DIR, "tv_last.json")  # 电视上次分组/光标
TV_FAV_CACHE = os.path.join(STATE_DIR, "tv_fav.json")    # 电视收藏 (频道名表)
IDEAS_FILE = os.path.join(STATE_DIR, "ideas.json")        # 想法 (本地存储)
SCHED_CACHE = os.path.join(STATE_DIR, "sched.json")      # 日程卡缓存 (提醒/行程)
CLOUD_URL_DEFAULT = "https://notes.apisz.top"   # 注册成功自动配 (可改设置)
CACHE_DIR = os.path.join(STATE_DIR, "cache")              # 主页数据磁盘缓存
_SENT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "data", "daily_sentences.csv")   # 内置 1440 句 (217KB)
_SENTENCES = None


def daily_sentence():
    """内置每日一句 (替代 hitokoto 网络): 按日期序号轮换, 零加载等待,
    与车机/网页版 (hermes _daily_sentence.py 同算法) 每天同一句."""
    global _SENTENCES
    if _SENTENCES is None:
        import csv
        rows = []
        try:
            with open(_SENT_CSV, encoding="utf-8") as f:
                for row in csv.reader(f):
                    if len(row) >= 2 and row[0].strip() and row[1].strip():
                        if rows or row[0].strip().lower() != "en":
                            rows.append((row[0].strip(), row[1].strip()))
        except Exception:
            rows = []
        _SENTENCES = rows
    if not _SENTENCES:
        return {"ok": True, "text": "机不可失,时不再来。", "from": "旧唐书"}
    import datetime
    en, cn = _SENTENCES[datetime.date.today().toordinal() % len(_SENTENCES)]
    return {"ok": True, "text": cn, "from": en}
# 主页右列卡片键 (与 draw_home vis 元组同序; 设置页开关/≤3 计数共用)
CARD_KEYS = ("sched", "hitokoto", "news", "fin")
# 主页卡缓存标签: 进 app 秒显上次数据, 后台刷新后再覆盖 (用户要求免"获取中")
HOME_CACHE_TAGS = ("wx", "news", "ipo", "quotes")
WATCH_FILE = os.path.join(STATE_DIR, "watch.json")        # 自选股 (本地)
# 自选股种子 (qt.gtimg.cn 代码; watch.json 存在则整体覆盖)
WATCH_SEED = {"items": [
    {"code": "sh600519", "name": "贵州茅台"},
    {"code": "sz000001", "name": "平安银行"},
    {"code": "sh601318", "name": "中国平安"},
    {"code": "sz300750", "name": "宁德时代"},
    {"code": "sh688981", "name": "中芯国际"},
]}
# Oneapp 账户 kv 云同步 (noteserver /v1/kv/<key>): 本地文件=真相, 变更即推;
# 登录后逐服务对齐 (本地有记录→确认框: Enter 上传 / Esc 云端覆盖);
# 开机拉取按文件 mtime 判新旧 (LWW, 静默)
KV_FILES = {"radio_fav": RADIO_FAV_CACHE, "watch": WATCH_FILE,
            "tv_fav": TV_FAV_CACHE, "news_feeds": NEWS_FEEDS_FILE}
KV_LABEL = {"radio_fav": "电台收藏", "watch": "自选股", "tv_fav": "电视收藏",
            "news_feeds": "新闻源"}
VOICES_DIR = os.path.join(STATE_DIR, "voices")   # 语音想法 (本地 wav)
# 录音 (ES8389 麦克风; amixer 路由照抄 xiaozhi_launcher 已知良好配置 —
# 驱动默认态=错误路由, 底噪 RMS 2767, 配置后 258; 每次录音前重置, 默认会漂)
REC_DEV = "plughw:CARD=ES8389Audio,DEV=0"
REC_MAX = 60                             # 秒, 自动截断 (arecord -d 自己停)
# 数值控制必须 cset numid 直写 — 本机 sset 对全名报 "Unable to find simple
# control", 短名靠模糊匹配时灵时不灵 (2026-11-14 "没声音"根因: Capture
# Volume 漂回 50/-70dB 全零, sset 160 静默失败). numid 由驱动固定枚举顺序,
# ES8389 同内核版本稳定. 录前 rec_mixer_apply 回读取证, 不符即拒录报项.
# 条目: ("num", numid, 值) | ("str", 控制名, 值) — 枚举/开关 sset 名字可靠.
REC_MIXER = [
    ("str", "ADC MUX", "AMIC"),
    ("str", "PGAL Select", "DifferentialL"),
    ("str", "PGAR Select", "DifferentialR"),
    ("num", 2, 160),                     # ADCL Capture Volume 0-255
    ("num", 3, 160),                     # ADCR Capture Volume
    ("num", 4, 6),                       # ADCL PGA Volume 0-14 ×3dB
    ("num", 5, 6),                       # ADCR PGA Volume
    ("num", 19, 0),                      # ADC2DAC Mixer Volume
    ("str", "ADC Mixer DACL ADCL Mixer", "off"),   # 防 DAC→ADC 回环
    ("str", "ADC Mixer DACR ADCR Mixer", "off"),
]


def rec_mixer_apply():
    """应用录音 mixer 配置并逐项回读验证. → (ok, 失败项描述).

    回读走 amixer contents 全表 (numid 与 name= 同行, enum 的 values= 是
    索引需经 items 表映射成名字)."""
    for kind, ident, v in REC_MIXER:
        if kind == "num":
            subprocess.run(["amixer", "-c", "1", "cset", "numid=%d" % ident,
                            "--", str(v)],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=4, check=False)
        else:
            subprocess.run(["amixer", "-c", "1", "sset", ident, v],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=4, check=False)
    try:
        out = subprocess.run(["amixer", "-c", "1", "contents"],
                             capture_output=True, text=True,
                             timeout=6).stdout
    except Exception:
        return False, "amixer contents 回读失败"
    vals, enum_items, name, numid = {}, {}, None, None
    for ln in out.splitlines():
        s = ln.strip()
        if "name='" in s:
            name = s.split("name='", 1)[1].rsplit("'", 1)[0]
            numid = None
            if s.startswith("numid="):
                numid = int(s.split("numid=", 1)[1].split(",")[0])
        elif s.startswith("; Item #") and name and "'" in s:
            enum_items.setdefault(name, []).append(s.split("'")[1])
        elif s.startswith(": values=") and name:
            v0 = s[len(": values="):].strip()
            vals[name] = v0
            if numid is not None:
                vals[numid] = v0
    bad = []
    for kind, ident, v in REC_MIXER:
        if kind == "num":
            cur = vals.get(ident)
            ok = cur is not None                 and cur.split(",")[0][:1].isdigit()                 and float(cur.split(",")[0]) >= float(v)
            label = "numid%d" % ident
        else:
            cur = vals.get(ident)
            if cur and ident in enum_items and cur.split(",")[0][:1].isdigit():
                idx = int(cur.split(",")[0])
                cur = enum_items[ident][idx]                     if idx < len(enum_items[ident]) else cur
            ok = cur is not None and cur.split(",")[0] == v
            label = ident
        if not ok:
            bad.append("%s=%s≠%s" % (label, cur, v))
    return (not bad), ";".join(bad)


def trim_silence_head(path, thr=800, keep=0.3):
    """裁掉录音开头静音 (人声前空拍): 连续 3 个 100ms 窗 RMS 超 thr 才算
    开口 (单发起录爆音不算), 保留前 keep 秒. 返回裁后时长秒, 失败 None."""
    try:
        import wave as _w
        w = _w.open(path)
        n, sr = w.getnframes(), w.getframerate()
        import array as _a
        data = _a.array("h", w.readframes(n))
        w.close()
        win = sr // 10                          # 100ms
        speak = None
        hot = 0
        for i in range(0, len(data) - win, win):
            seg = data[i:i + win]
            rms = math.sqrt(sum(x * x for x in seg) / len(seg))
            hot = hot + 1 if rms > thr else 0
            if hot >= 3:
                speak = i - 2 * win             # 回到连续段起点
                break
        cut = 0
        if speak is not None and speak >= int(keep * sr):
            cut = speak - int(keep * sr)        # 人声前留 keep 秒
        out = data[cut:].tobytes()
        w2 = _w.open(path, "wb")
        w2.setnchannels(1)
        w2.setsampwidth(2)
        w2.setframerate(sr)
        w2.writeframes(out)                     # 同时重写正确 WAV 头
        w2.close()
        return round(len(out) / 2 / sr)
    except Exception:
        return None


def clean_voice(path, target_pk=26000):
    """录音后处理 (纯 stdlib, worker 线程跑): ①~100Hz 一阶高通 (去隆隆/工频)
    ②噪声门 (20ms 帧, 底噪=帧RMS 10 分位, 门限=底噪×1.6, 帧增益线性斜坡)
    ③峰值归一到 target_pk (80% 满幅). 返回处理时长秒, 失败 None."""
    try:
        import wave as _w
        w = _w.open(path)
        sr, n = w.getframerate(), w.getnframes()
        import array as _a
        d = _a.array("h", w.readframes(n))
        w.close()
        if not len(d):
            return None
        # ① 一阶高通: y = a*(y_prev + x - x_prev), a=0.985 ≈ fc 100Hz@16k
        a = 0.985
        px = py = 0
        hp = _a.array("h")
        for x in d:
            y = a * (py + x - px)
            px = x
            py = y
            hp.append(int(max(-32768, min(32767, y))))  # array('h') 溢出即抛
        # ② 噪声门: 20ms 帧能量 → 帧增益 (线性斜坡防爆音).
        # floor 取 10 分位; 若 p10>p50×0.6 (整段无静音, 如连续说话) → 门限退化
        # 为弱门, 防全轨误杀 (家庭版 VAD 同坑). 最低增益 0.25.
        win = max(1, sr // 50)
        rms_list = []
        for i in range(0, len(hp) - win + 1, win):
            seg = hp[i:i + win]
            rms_list.append(math.sqrt(sum(v * v for v in seg) / len(seg)))
        if not rms_list:
            return None
        s = sorted(rms_list)
        p10, p50, p80 = (s[len(s) // 10], s[len(s) // 2],
                         s[min(len(s) - 1, int(len(s) * 0.8))])
        if p10 > p50 * 0.6:
            thr = p50 * 0.5 + 8                # 无静音段: 只压最弱的一半
        else:
            thr = p10 * 2.2 + 8
        gains = [1.0] * len(rms_list)
        for i, r in enumerate(rms_list):
            if r < thr:
                depth = (thr - r) / (thr + 1.0)
                gains[i] = max(0.25, 1.0 - depth * 1.2)
        out = _a.array("h")
        for i, r in enumerate(rms_list):       # 帧内线性斜坡 (与前帧增益插值)
            g0 = gains[i - 1] if i else 1.0
            g1 = gains[i]
            for j, v in enumerate(hp[i * win:(i + 1) * win]):
                g = g0 + (g1 - g0) * j / win
                out.append(int(max(-32768, min(32767, v * g))))
        out.extend(hp[len(rms_list) * win:])
        # ③ 语音 RMS 归一 (p80 帧): 单击脉冲/削波峰不再毁整体响度;
        # k 钳 [1,20], 后做峰值安全回缩
        k = min(20.0, max(1.0, (target_pk / 8.0) / max(1, p80)))
        out = _a.array("h", [int(max(-32768, min(32767, v * k))) for v in out])
        pk = max(abs(v) for v in out) or 1
        if pk > 32000:
            kk = 32000.0 / pk
            out = _a.array("h", [int(v * kk) for v in out])
        w2 = _w.open(path, "wb")
        w2.setnchannels(1)
        w2.setsampwidth(2)
        w2.setframerate(sr)
        w2.writeframes(out.tobytes())
        w2.close()
        return round(len(out) / sr)            # out=样本数组 (非字节), /sr=秒
    except Exception:
        return None

# 换算表 (与 car-dash 换算 / tab5 app_settings 同表)
FX_CODES = ["USD", "CNY", "EUR", "JPY", "GBP", "HKD", "KRW", "AUD"]
FX_NAMES = {"USD": "美元", "CNY": "人民币", "EUR": "欧元", "JPY": "日元",
            "GBP": "英镑", "HKD": "港元", "KRW": "韩元", "AUD": "澳元"}
FX_FALLBACK = {"USD": 1.0, "CNY": 7.25, "EUR": 0.92, "JPY": 157.0,
               "GBP": 0.79, "HKD": 7.81, "KRW": 1380.0, "AUD": 1.51}
UCATS = [
    ("长度", [("mm", 0.001), ("cm", 0.01), ("m", 1), ("km", 1000),
             ("in", 0.0254), ("ft", 0.3048), ("yd", 0.9144), ("mi", 1609.344)]),
    ("重量", [("mg", 0.001), ("g", 1), ("kg", 1000), ("t", 1e6),
             ("oz", 28.3495), ("lb", 453.592)]),
    ("温度", [("°C", 0), ("°F", 1), ("K", 2)]),
    ("面积", [("mm²", 1e-6), ("cm²", 1e-4), ("m²", 1), ("km²", 1e6),
             ("ha", 1e4), ("亩", 666.6667), ("acre", 4046.86), ("ft²", 0.092903)]),
    ("体积", [("mL", 0.001), ("L", 1), ("m³", 1000), ("gal", 3.78541),
             ("cup", 0.236588)]),
    ("速度", [("m/s", 1), ("km/h", 0.277778), ("mph", 0.44704), ("kn", 0.514444)]),
    ("数据", [("B", 1), ("KB", 1024), ("MB", 1048576), ("GB", 1073741824),
             ("TB", 1099511627776)]),
]
TM_PRESETS = [1, 3, 5, 10, 30, 60]       # 计时器预设 (分钟, 数字键 1-6)


def fmt_num(v):
    """结果格式化: 整数去尾零, 其余 %.6g, 千位分隔."""
    if abs(v) >= 1e12 or (abs(v) < 1e-6 and v != 0):
        return "%.4g" % v
    if v == int(v) and abs(v) < 1e10:
        return "{:,d}".format(int(v))
    return "{:,}".format(float("%.6g" % v))


def mmss(t):
    """秒 → m:ss (播放器进度标签)."""
    t = max(0, int(t))
    return "%d:%02d" % (t // 60, t % 60)


def temp_convert(v, frm, to):
    """温度 → °C 中转 (UCATS 温度表无系数)."""
    c = v if frm == "°C" else ((v - 32) * 5 / 9 if frm == "°F" else v - 273.15)
    return c if to == "°C" else (c * 9 / 5 + 32 if to == "°F" else c + 273.15)


def mu_parse_name(path):
    """文件路径 → (artist, title, ext)。'歌手 - 标题' 惯例; QQ 音乐下载的
    '歌手-标题' (无空格) 也识别 — 首段 ≤30 字才当歌手, 防劈开带连字符曲名."""
    name = os.path.basename(path)
    stem, ext = os.path.splitext(name)
    a, t = "", stem
    if " - " in stem:
        a, t = stem.split(" - ", 1)
    elif "-" in stem:
        a, t = stem.split("-", 1)
        if not a.strip() or not t.strip() or len(a.strip()) > 30:
            a, t = "", stem
    return (a.strip(), t.strip(), ext.lower())


def scan_music_dir(root):
    """递归扫曲库 → [{path, artist, title, ext}] (按相对路径排序, ≤500 首).
    展示名不带目录层级, 同名靠 artist/title 区分; 空/无权限目录静默返回 []."""
    out = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for fn in sorted(filenames):
                if fn.lower().endswith(MUSIC_EXTS):
                    artist, title, ext = mu_parse_name(fn)
                    out.append({"path": os.path.join(dirpath, fn),
                                "artist": artist, "title": title, "ext": ext})
                    if len(out) >= 500:
                        return out
    except Exception:
        pass
    return out


def mu_probe_dur(path):
    """ffprobe 时长 (秒); 无 ffprobe/解析失败 → 0 (进度条整条隐藏, tab5 规则)."""
    if shutil.which("ffprobe") is None:
        return 0.0
    try:
        r = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries",
                            "format=duration", "-of", "csv=p=0", path],
                           capture_output=True, text=True, timeout=12)
        return float(r.stdout.strip().splitlines()[0])
    except Exception:
        return 0.0




def mu_extract_cover(path):
    """内嵌封面字节 (mp3 ID3v2 APIC / flac PICTURE 块); wav/无图 → None.
    只读头部区域不解整文件; 解出的 jpg/png 字节交 jpg_surf (设备 SDL_image
    可解; Mac 模拟无 PNG 支持会静默回 None → 无封面占位)."""
    try:
        with open(path, "rb") as f:
            head = f.read(10)
            if head[:3] == b"ID3":                     # mp3 ID3v2
                ver = head[3]
                if ver == 4:                           # v2.4 帧长/标签长 syncsafe
                    size = ((head[6] & 0x7F) << 21) | ((head[7] & 0x7F) << 14) \
                        | ((head[8] & 0x7F) << 7) | (head[9] & 0x7F)
                else:                                  # v2.2/2.3 普通 int32
                    size = int.from_bytes(head[6:10], "big")
                size = min(size, 2 * 1024 * 1024)
                body = f.read(size)
                i = 0
                while i + 10 <= len(body):
                    fid = body[i:i + 4]
                    if not fid.strip(b"\x00 "):        # padding → 到头
                        break
                    if ver == 4:
                        fsz = ((body[i + 4] & 0x7F) << 21) \
                            | ((body[i + 5] & 0x7F) << 14) \
                            | ((body[i + 6] & 0x7F) << 7) | (body[i + 7] & 0x7F)
                    else:
                        fsz = int.from_bytes(body[i + 4:i + 8], "big")
                    if fid == b"APIC" and 14 <= fsz <= len(body) - i - 10:
                        d = body[i + 10:i + 10 + fsz]
                        enc, d = d[0], d[1:]
                        z = d.find(b"\x00")            # mime
                        mime = d[:z].decode("ascii", "ignore").lower()
                        d = d[z + 1:][1:]              # 跳 picture type
                        if enc in (1, 2):              # utf-16 描述双零结尾
                            d = d[d.find(b"\x00\x00") + 2:]
                        else:
                            d = d[d.find(b"\x00") + 1:]
                        if ("jpg" in mime or "jpeg" in mime or "png" in mime
                                or "bmp" in mime) and len(d) > 100:
                            return d
                    i += 10 + fsz
                return None
            if head[:4] == b"fLaC":                    # flac 元数据块链
                f.seek(4)
                while True:
                    bh = f.read(4)
                    if len(bh) < 4:
                        return None
                    typ, last = bh[0] & 0x7F, bh[0] & 0x80
                    bsz = int.from_bytes(bh[1:4], "big")
                    if typ == 6:                       # PICTURE
                        d = f.read(min(bsz, 2 * 1024 * 1024))
                        ml = int.from_bytes(d[4:8], "big")
                        mime = d[8:8 + ml].decode("ascii", "ignore").lower()
                        dl = int.from_bytes(d[8 + ml:12 + ml], "big")
                        # w/h/depth/colors 16B + data_len 4B
                        base = 12 + ml + dl + 16
                        dln = int.from_bytes(d[base:base + 4], "big")
                        img = d[base + 4:base + 4 + dln]
                        if ("jpg" in mime or "jpeg" in mime
                                or "png" in mime) and len(img) > 50:
                            return img
                        return None
                    f.seek(bsz, 1)                     # 跳过其余块
                    if last:
                        return None
    except Exception:
        return None
    return None


class MusicLvl:
    """本地音乐电平源 → vu_tick 三带输入 (电台 lvs 同标度 0..100).
    本机 = parec 监听默认 sink monitor (ffplay→pulse 混音后波形);
    AirPlay = ffmpeg FIFO wav 旁路 (AirPlay 不走本地 pulse, monitor 采不到).
    三带 = 两条单极 IIR 分频 (低/中/高), 逐块 RMS + EMA 平滑."""

    def __init__(self):
        self.lvl = (0.0, 0.0, 0.0)
        self._p = None                         # parec 子进程
        self._fp = None                        # 读端 (fifo 模式 fdopen)
        self._fifo = None                      # 旁路 fifo 路径 (stop 时清理)
        self._th = None

    def start_parec(self):
        self.stop()
        if shutil.which("parec") is None or shutil.which("pactl") is None:
            return
        try:
            info = subprocess.run(["pactl", "info"], capture_output=True,
                                  text=True, timeout=3).stdout
            sn = [l.split(":", 1)[1].strip() for l in info.splitlines()
                  if l.startswith("Default Sink")]
            if not sn:
                return
            self._p = subprocess.Popen(
                ["parec", "-d", sn[0] + ".monitor", "--raw",
                 "--format=s16le", "--rate=8000", "--channels=1",
                 "--latency-msec=40"],     # 小预缓冲: VU 贴拍 (默认慢半拍)
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self._th = threading.Thread(target=self._run,
                                        args=(self._p.stdout, 1, 1024, 0.35),
                                        daemon=True)   # 1024B=64ms 窗 (原 256ms)
            self._th.start()
        except Exception:
            self.stop()

    def start_fifo(self, path):
        """AirPlay: 读 ffmpeg FIFO 旁路 wav (44.1k s16 立体声, 隔 12 取样).
        FIFO 替代 fd3 直传 (pass_fds+preexec 的 fd 号跨版本不定, 2026-11-14
        实测 fd3 没落进 ffmpeg → wav muxer EBADF 秒死 = 无声根因). 读端
        O_NONBLOCK 打开避免与 ffmpeg 的写端 open 互等."""
        self.stop()
        self._fifo = path

        def _open():
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                head = b""
                for _ in range(150):      # 等 ffmpeg 开写端 (nonblock 无 writer
                    head = os.read(fd, 4096)   #   时 read 立刻 EOF, 须轮询)
                    if head:
                        break
                    time.sleep(0.2)
                if not head:
                    os.close(fd)
                    return
                os.set_blocking(fd, True)  # 转阻塞: BufferedReader 不吃 EAGAIN
                fp = os.fdopen(fd, "rb")
                self._fp = fp
                self._run(fp, 12, head=head)
            except Exception:
                pass

        self._th = threading.Thread(target=_open, daemon=True)
        self._th.start()

    def _run(self, fp, step, chunk=4096, ema=0.25, head=b""):
        """chunk=读窗字节 (parec 1024=64ms / fifo 4096=23ms@44.1k); ema=块间平滑."""
        try:
            if step > 1:                       # wav 管道: 跳 RIFF 头找 data
                if not head:
                    head = fp.read(4096)
                j = head.find(b"data")
                if j < 0:
                    return
            s0 = s1 = 0.0
            lv = [0.0, 0.0, 0.0]
            while True:
                buf = fp.read(chunk)
                if not buf:
                    break
                n = len(buf) // 2
                if n < 16:
                    continue
                a = struct.unpack("<%dh" % n, buf[:n * 2])
                acc = [0.0, 0.0, 0.0]
                cnt = 0
                for i in range(0, n - 1, step):  # fd3 立体声取偶=左声道
                    v = a[i] / 32768.0
                    s0 += 0.28 * (v - s0)        # 浅低通
                    s1 += 0.06 * (v - s1)        # 深低通
                    lo, mid, hi = s1, s0 - s1, v - s0
                    acc[0] += lo * lo
                    acc[1] += mid * mid
                    acc[2] += hi * hi
                    cnt += 1
                if not cnt:
                    continue
                lv = [min(100.0, lv[k] + ((acc[k] / cnt) ** 0.5 * 400.0
                                          - lv[k]) * ema)
                      for k in range(3)]
                self.lvl = tuple(lv)
        except Exception:
            pass

    def stop(self):
        p, self._p = self._p, None
        if p:
            try:
                p.terminate()
            except Exception:
                pass
        fp, self._fp = self._fp, None
        if fp:
            try:
                fp.close()                     # 读端关闭 → 线程 read 抛错退出
            except Exception:
                pass
        fifo, self._fifo = self._fifo, None
        if fifo:
            try:
                os.unlink(fifo)                # 旁路 fifo 用完即清
            except Exception:
                pass
        self._th = None
        self.lvl = (0.0, 0.0, 0.0)


def jpg_surf(data, w=0, h=0):
    """JPEG 字节 → Surface (设备 SDL_image 2.8.5 实测可解; 失败回 None).
    注意 smoothscale 只吃 24/32 位, 原样加载缩放, 位深交给 blit 时转换."""
    try:
        s = pygame.image.load(io.BytesIO(data))
        if w and h:
            s = pygame.transform.smoothscale(s, (w, h))
        return s
    except Exception:
        return None


def gen_beep(path="/tmp/dz_beep.wav"):
    """计时结束提示音: 3 声 880Hz 短蜂 (纯 Python 生成, ffplay 播)."""
    try:
        import wave
        sr = 16000
        frames = bytearray()
        for k in range(3):
            for i in range(int(sr * 0.30)):
                env = min(1.0, i / (sr * 0.01)) * max(0.0, 1 - i / (sr * 0.28))
                v = int(22000 * env * math.sin(2 * math.pi * 880 * i / sr))
                frames += struct.pack("<hh", v, v)
            for i in range(int(sr * 0.15)):
                frames += struct.pack("<hh", 0, 0)
        w = wave.open(path, "wb")
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))
        w.close()
        return True
    except Exception:
        return False


class App:
    def __init__(self, netins):
        self.net = netins
        self.page = 0
        self.data = {}                        # tag → json
        self._load_home_cache()               # 上次数据秒显 (后台再刷新)
        self.theme_idx = 0
        try:
            self.theme_idx = json.load(open(THEME_FILE)).get("idx", 0) % len(THEMES)
        except Exception:
            pass
        self.osd = None                       # (label, until)
        self.dialog = None                    # ("exit",) | ("del", item)
        # 想法 (本地 json) / 自选股 (本地 json, 种子兜底)
        self.ideas = netmod.load_json_file(IDEAS_FILE, {"texts": []})
        self.iview = "local"                  # 想法视图: local=本地卡 cloud=云端列表
        self.csel = 0                         # 云端列表光标
        self.cloud_notes = []                 # 云端条目快照 (job 拉取)
        self._cloud_trig = 0.0                # 上次云同步触发 (节流)
        # 日程卡 (提醒/行程, noteserver /v1/sched; 与想法云同账号 token)
        self.sched = netmod.load_json_file(SCHED_CACHE,
                                           {"reminds": [], "trips": []})
        self._sched_home_at = 0.0             # 主页日程卡刷新节流
        self.watch = netmod.load_json_file(WATCH_FILE, WATCH_SEED)
        self.wx_loc = netmod.load_json_file(WX_LOC_FILE, None) or dict(WX_HOME)
        # 语音想法: 录音 (arecord) + 本地播放 (ffplay)
        self.rec_proc = None                  # arecord 录音进程
        self.rec_started = 0.0
        self.rec_path = "/tmp/oneapp_rec.wav"
        self.playing_voice = None             # 正在播放的语音条目
        self._voice_at = 0.0                  # 播放起点 (monotonic)
        # 想法页状态
        self.in_input = False                 # 输入模式
        self.buf = ""
        self.sel = 0
        self.scroll = 0
        self.player = None                    # ffplay 子进程 (电台)
        # 电台页状态
        self.radio_grp = 0                    # 分组索引 (0=收藏, 1+=GROUPS)
        self.radio_sels = {}                  # 各组光标记忆 (切组恢复)
        self.radio_fav = self.radio_fav_load()
        self.rsel = 0
        self.rscroll = 0
        self.playing_key = None               # 播放中电台 key
        self.play_at = 0.0                    # 本次开播时刻
        self.raop_cons = None                 # AirPlay 消费端 (atvremote stream=-)
        self.vol_w = None                     # 音量控制 FIFO 写端 (O_RDWR, 会话期常开)
        self.vol_fifo = None
        self.raop_up_at = None                # RAOP-UP 到达时刻 (None=未连上)
        self.raop_mute_prev = None            # Fn+A 静音前的音量 (恢复位)
        _v = netmod.load_json_file(RAOP_VOL_FILE, 50)
        self.raop_vol = _v if isinstance(_v, int) and 0 <= _v <= 100 else 50
        # 电台输出 (0=本机, ≥1 → raop[idx-1]); 缓存须带 id, 旧格式无 id → 重扫
        self.raop = [d for d in netmod.load_json_file(RAOP_CACHE, [])
                     if isinstance(d, dict) and _raop_ok(d)]
        self.radio_out = 0
        _oid = netmod.load_json_file(RADIO_OUT_FILE, "")
        if _oid:                              # 恢复只认本网段可用设备 (外地
            self.radio_out = next(            # 不把死路音箱当当前输出恢复)
                (i + 1 for i, d in enumerate(self._raop_avail())
                 if d.get("id") == _oid), 0)
        self._raop_scanned = bool(self.raop)  # 空缓存: 进电台页后台补扫一次
        self.vu = [3.0] * 12                  # VU 条 (car-dash vuTick 同款)
        self.vu_pk = [3.0] * 12
        self._vu_at = 0.0
        self.autoplay_key = None              # 设备测试: DASH_PLAY=<key>
        # 行情页状态
        self.fin_view = 0                     # 0 自选 1 指数 2 商品
        self.fin_input = False                # 添加自选股输入态 (n 键)
        self._hk_at = 0.0                     # 每日一句跑马灯相位
        self._wk_at = 0.0                     # 主页自选股跑马灯相位
        self._tk_at = 0.0                     # 行情顶部跑马灯相位
        self.fsel = 0
        self.fscroll = 0
        self.fdetail = None                   # None | {"code","name","view"}
        self.fin_ts = ""                      # 行情时间戳
        # 工具页: 卡片入口网格 + 子应用 (Esc 回网格; 参照 tab5 驾驶舱工具页)
        self.cal_ym = None                    # (y, m) 万年历当前月
        self.tool = None                      # None=网格 | TOOL_APPS id
        self.tsel = 0                         # 网格光标
        # 汇率 / 单位换算 (字段: 0 金额 1 源 2 目标 3 类别)
        self.fx_amt = "1"
        self.fx_from = 0                      # FX_CODES 索引
        self.fx_to = 1
        self.u_cat = 0                        # UCATS 索引
        self.u_amt = "1"
        self.u_from = 0
        self.u_to = 1
        self.cv_field = 0
        self.cv_armed = False                 # 首键替换默认 1, 之后追加 (car-dash 同款)
        # 彩票 (双色球/大乐透) / 计时器
        self.lot_type = 0                     # LOT_TYPES 索引: 0=双色球 1=大乐透
        self.lot_scroll = 0                   # 选中期索引 (顶部卡片跟随)
        self.lot_view = 0                     # 历史列表窗口起点
        self.tm_dur = 300                     # 秒
        self.tm_left = 300.0
        self.tm_run = False
        self.tm_done = False
        self.tm_end = 0.0                     # monotonic 结束时刻
        self.beep_proc = None
        # 音乐 (本地曲库; QQ/网易直连)
        self.mu_view = "src"                  # src 源选择 | list 本地列表 | play 播放
        self.mu_src = "local"
        self.mu_sel = 0                       # 列表光标
        self.mu_files = []                    # scan_music_dir 结果
        self.mu_scan_at = 0.0                 # 进列表页重扫节流 (60s)
        self.mu_playing = None                # 当前 {"path","title","artist",...}
        self.mu_play_idx = -1                 # mu_playing 在 mu_files 里的序号
        self.mu_proc = None                   # 播放进程 (本机 ffplay / AirPlay 产端 ffmpeg)
        self.mu_ff = None                     # AirPlay 消费端 (电台链同款; draw 锚点判据)
        self.mu_retries = 0                   # 提前收工重连额度 (每曲 3)
        self.mu_retry_at = 0.0                # 非阻塞重试定时锚
        self.mu_advance = False               # True=自然播完自动连播下一首
        self.mu_pos0 = 0.0                    # 本段起点秒 (-ss 值)
        self.mu_t0 = 0.0                      # mu_pos0 对应的 monotonic 锚
        self.mu_dur = 0.0                     # ffprobe 时长 (0=未知 → 条隐藏)
        self.mu_paused = False
        self.lvl = MusicLvl()                 # 电平源 (parec/fd3 → vu_tick; 电台/音乐共用)
        self.mu_pic = None                    # 封面 Surface 84×84 (内嵌图)
        self.mu_pic_raw = None                # 待主线程转 Surface 的字节
        self.mu_pic_path = None
        self.mu_pic_seq = 0                   # 网络封面请求序号 (防换曲串图)
        self.mu_lyr = []                      # [(秒, 文本)] relay lrclib
        self.mu_lyr_key = None
        self.mu_q = ""                        # QQ 搜索词
        self.mu_input = False                 # 软键盘输入态 (搜索)
        self.mu_busy = False                  # 线程取数中 (搜索/收藏/解析)
        self.mu_lists = {"local": [], "qq": [],
                         "ne": []}            # 每源播放队列 (会话内)
        self.mu_busy_at = 0.0                 # busy 起始 (30s 看门狗)
        self.mu_qr = None                     # 扫码会话 (QQLogin/NeLogin)
        self.mu_qr_kind = "qq"               # 当前扫码源
        self.esc_hold_at = None               # 长按 Esc 起始 (播放页退音乐/主页退 dash)
        self.esc_exit = False                 # esc_hold_poll 满时长 → 主循环退出
        self.mu_proc_at = None                # 本机 ffplay 起始 (VU 首响兜底)
        self.mu_saved_at = 0.0                # 会话落盘节流 (mu_tick 15s)
        self.mu_aud_up = None                 # 出声时刻 (VU 首响锚点; None=起播期)
        self.mu_sess = 0                      # 播放会话号 (锚点线程守卫)
        # 音乐输出: 0=本机, ≥1 → raop[idx-1] (MU_OUT_CACHE 按设备 id 恢复;
        # 电台/音乐共用 raop 设备表, 输出选择各自独立)
        self.mu_out = 0
        _mo = netmod.load_json_file(MU_OUT_CACHE, {})
        _moid = _mo.get("id") if isinstance(_mo, dict) else ""
        if _moid:                             # 恢复只认本网段可用设备 (外地
            self.mu_out = next(               # 不把死路音箱当当前输出恢复)
                (i + 1 for i, d in enumerate(self._raop_avail())
                 if d.get("id") == _moid), 0)
        self.mu_prod_dead_at = None           # AirPlay 产端死而消费端未退的起始
        self.mu_is_raop = False               # 当前会话是 AirPlay (连接超时
        self.mu_raop_t0 = None                #   看门狗 16s 无声 → 回本机)
        self.news_src_i = 0                   # 新闻源索引
        self.news_sel = 0
        self.news_scroll = 0
        self.news_detail = None               # 选中的新闻条目 (详情视图)
        self.news_detail_scroll = 0           # 详情滚动行数
        self.news_i = 0                       # 主页新闻跑马灯条目轮换
        self._news_at = 0.0
        self.news_feeds = self._news_feeds_load()  # 源表 [{id,name,url,on}] kv 云同步
        news_rss.set_feeds(self.news_feeds)   # 抓取层生效源表 (news_fetch_all)
        self.news_set = False                 # 新闻源设置视图 (S 进, Esc 退)
        self.ns_sel = 0                       # 设置视图光标
        self.ns_scroll = 0
        self.ns_input = 0                     # 加/改源输入相: 0关 1标题 2地址
        self.ns_url = ""                      # 地址相收下的地址 (标题相期间暂存)
        self.ns_edit_id = None                # 编辑中的源 id (None=新增)
        self.esc_t = None                      # Esc 按下时刻 (长按回主页)
        # 邮件 app (工具第 7 卡; IMAP 收 + SMTP 发, 协议层在 mail.py)
        self.mail = mailmod.load_accounts()    # {"accounts": [...], "last": ...}
        self.mail_lv = "acct"                  # acct/login/folders/list/detail/compose
        self.mail_sel = 0                      # 当层光标 (账号/文件夹/邮件)
        self.mail_scroll = 0
        self.mail_edit = 0                     # 登录表单字段 (0=邮箱 1=授权码)
        self.mail_acct_buf = ""                # 登录表单·邮箱输入
        self.mail_auth_buf = ""                # 登录表单·授权码输入
        self.mail_cur = None                   # 当前账号 dict (含授权码)
        self.mail_folders = []                 # [{raw,name,unread,total}]
        self.mail_folder = None                # 当前文件夹 raw 名
        self.mail_msgs = []
        self.mail_got = False                  # 列表已收到过结果 (区分空/加载中)
        self.mail_body = None                  # 详情文本 (str) / None
        self.mail_body_uid = None              # 详情对应 uid (结果对号)
        self.mail_body_fld = None              # 详情对应夹 (聚合视图逐夹操作)
        self.mail_body_scroll = 0
        self.mail_c_meta = None                # 详情元数据 (回回复信头/引用用)
        self.mail_c = {"to": "", "subject": "", "body": ""}   # 写信三字段
        self.mail_c_quote = ""                 # 回复引用块 (显示+发送拼接, 不可编辑)
        self.mail_cf = 0                       # 写信焦点 (0=收件人 1=主题 2=正文)
        self.mail_c_reply = False              # 回复态 (完成后回 detail)
        self.mail_c_from = "list"              # 写信入口层 (Esc 回跳)
        self.mail_c_busy = False               # 发送中 (worker 跑 SMTP)
        self.mail_c_busy_at = 0.0              # 发送看门狗锚
        self.mail_unread = {}                  # {email: 未读数} (账号层角标)
        self.mail_total = 0                    # 文件夹邮件总数
        self.mail_page = 0                     # 单夹列表分页 (0=最近10封)
        self.mail_has_more = False             # 还有更早邮件 (6 加载)
        self.mail_unread_n = {}                # {email: 全账号未读数}
        self.mail_flag_n = {}                  # {email: 全账号红旗数}
        self.mail_err = ""                     # 列表获取失败文案 (空态显示)
        self._mail_body_cache = (None, [])     # (key, lines) 详情折行缓存
        self._mail_c_cache = (None, [])        # (key, lines) 写信正文折行缓存
        # 设置 (工具·设置卡): API KEY + 主页卡片显隐 (settings.json 0600)
        self.cfg = netmod.load_json_file(SETTINGS_FILE, {}) or {}
        if not isinstance(self.cfg, dict):
            self.cfg = {}
        self.cfg.setdefault("cards", {})
        self._card_norm()                     # >3 张显式关掉排序靠后的
        self.set_tab = 0                       # 设置页签 (0=卡片 1=主题)
        self.set_sel = 0                       # 页签内行光标
        self.set_edit = None                   # None=常态 glm/ds=KEY 弹窗
        self.reg = None                        # 注册表单态 {name,invite,field}
        self.reg_busy = False                  # 注册提交中 (防重复)
        self.login = None                      # 登录表单态 {user,pw,tok,field}
        self.login_busy = False                # 登录验证中 (防重复)
        self.qrlogin = None                    # 扫码登录态 {st,dc,url,mat,err}
        self.qr_poll_at = 0.0                  # 扫码轮询节流
        self.kv_confirm = None                 # kv 云同步确认框 {k,n,cn,v}
        self.kv_queue = []                     # 登录对齐待处理服务队列
        self.kv_switch = False                 # 换号对齐中 (旧 kv 是别人的)
        self.set_buf = ""
        # 电视 (工具第 8 卡; 直播源内置 tv_channels.py, 单 ffmpeg 视频管道
        # + 音频同进程 — 播放方案移植官方 cardputer-zero-iptv 的 panel.py)
        self.tv_grp = 0                        # 分组索引 (0=收藏, 1+=GROUPS)
        self.tv_fav = [n for n in
                       netmod.load_json_file(TV_FAV_CACHE, [])
                       if isinstance(n, str)]   # 收藏频道名 (插入序)
        self.tv_sel = 0                        # 组内光标
        self.tv_sels = {0: 0}                  # 每组光标 {grp_idx: sel}
        self.tv_scroll = 0                     # 列表窗口起点
        self.tv_playing = None                 # 播放中频道 (name, group, urls)
        self.tv_url_i = 0                      # 当前线路序号 (失败自动 +1 换路)
        self.tv_proc = None                    # ffmpeg 子进程
        self.tv_gen = 0                        # 播放会话号 (读帧线程守卫)
        self.tv_hw = False                     # 本次尝试硬解 (h264_v4l2m2m)
        self.tv_audio = True                   # 本次尝试带音频映射
        self.tv_att = 0                        # 重试梯级 (0 初试 / 1 软解 / 2 -an)
        self.tv_frame = None                   # 最新 rgb24 帧 (W*H*3 字节)
        self.tv_frames = 0                     # 累计帧数 (诊断)
        self.tv_first_at = None                # 首帧耗时锚 (None=未出帧)
        self.tv_last_at = 0.0                  # 最近帧时刻 (断流看门狗)
        self.tv_started = 0.0                  # 本次起播时刻
        self.tv_err = ""                       # 播放失败文案 (列表页 OSD)
        self.tv_dead = None                    # 读帧线程收尾 {"rc":..., "err":...}
        self.tv_osd_at = 0.0                   # 频道名 OSD 起始
        self.tv_reader = None                  # 读帧线程
        # 拼音 IME (中文输入; '/' 切中英; 两处输入框共用一套状态)
        self.ime_mode = True                   # True=拼音 False=英文直输
        self.ime_buf = ""                      # 当前拼音串
        self.ime_cands = []                    # 候选 (词组+单字, ≤24)
        self.ime_assoc = None                  # 联想词 (上次提交后, None=无)
        self.ime_page = 0                      # 候选页 (每页 8)
        if self.cfg.get("cloud_tok"):
            if self.cfg.get("kv_owner") \
                    and self.cfg["kv_owner"] != self.cfg.get("cloud_user"):
                self.kv_align_trigger()        # 上次换号未完成对齐 → 补做
            else:
                self.kv_start_pull()           # Oneapp 账户 kv 开机静默 LWW
        # 链路探测由 net 层启动时自发

    def th(self, key):
        return hex2rgb(THEMES[self.theme_idx][key])

    def card(self, i):
        cards = THEMES[self.theme_idx]["cards"]
        return hex2rgb(cards[i % len(cards)])   # 第 4 卡循环取色

    def _load_home_cache(self):
        """主页卡数据磁盘缓存 → self.data (天气/新闻/新股); 每日一句内置."""
        for tag in HOME_CACHE_TAGS:
            j = netmod.load_json_file(os.path.join(CACHE_DIR, tag + ".json"),
                                      None)
            if j is not None:
                self.data[tag] = j
        self.data["hitokoto"] = daily_sentence()

    # ── 网络结果 ──
    def on_net(self, kind, a, b):
        if kind == "mode":
            self.data.clear()
            self._load_home_cache()           # 模式切换不闪"获取中"
        elif kind == "data":
            self.data[a] = b
            if a in HOME_CACHE_TAGS:
                try:
                    os.makedirs(CACHE_DIR, exist_ok=True)
                    netmod.save_json_file(
                        os.path.join(CACHE_DIR, a + ".json"), b)
                except Exception:
                    pass
            if a == "wxloc" and isinstance(b, dict) and b.get("lat") is not None:
                old = self.wx_loc or {}
                if b.get("city") != old.get("city") \
                        or abs(float(b["lat"]) - float(old.get("lat") or 0)) > 0.05:
                    self.wx_loc = b          # 归属地变了 → 下轮 poll 按新坐标拉
                    try:
                        netmod.save_json_file(WX_LOC_FILE, b)
                    except Exception:
                        pass
            if a.startswith("mulyr:") and isinstance(b, dict):
                self.mu_lyr = self._pl_lrc_parse(b.get("synced") or "")
            if a.startswith("mail_") and isinstance(b, dict):
                self.on_mail_net(a, b)
            if a == "cloudsync" and isinstance(b, dict):
                self.on_cloud_done(b)
            elif a == "cloudlist" and isinstance(b, dict) \
                    and not b.get("skip"):
                self.cloud_notes = b.get("notes") or []
                self.csel = max(0, min(self.csel,
                                       len(self.cloud_notes) - 1))
                if self.iview == "cloud":
                    self.osd = ("云端 %d 条" % len(self.cloud_notes),
                                time.monotonic() + 1.2)
            elif a == "cloudvoice" and isinstance(b, dict) \
                    and b.get("data"):
                lid = b.get("local_id")           # 懒下载: 落 voices/ 并回填条目
                p = (os.path.join(VOICES_DIR, "%s.wav" % b["id"]) if lid
                     else "/tmp/oneapp_cloud_%s.wav" % b["id"])
                try:
                    os.makedirs(VOICES_DIR, exist_ok=True)
                    with open(p, "wb") as f:
                        f.write(b["data"])
                    if lid:
                        for it in self.ideas.get("voices", []):
                            if it.get("id") == lid:
                                it["file"] = p
                        netmod.save_json_file(IDEAS_FILE, self.ideas)
                    self.play_voice({"id": lid or ("cloud-" + b["id"]),
                                     "cid": b["id"], "file": p,
                                     "dur": b.get("dur") or 0})
                except OSError:
                    self.osd = ("播放失败", time.monotonic() + 1.2)
            elif a == "clouddel" and isinstance(b, dict) \
                    and not b.get("skip"):
                cid = b.get("id")
                if b.get("st") in (200, 404):   # 404=云端已无, 同样算成
                    self.cloud_notes = [x for x in self.cloud_notes
                                        if x.get("id") != cid]
                    for key in ("voices", "texts"):   # 本地孪生同删
                        keep = []
                        for it in self.ideas.get(key, []):
                            if it.get("cid") == cid:
                                if key == "voices" and it.get("file"):
                                    try:
                                        os.remove(it["file"])
                                    except OSError:
                                        pass
                                continue
                            keep.append(it)
                        self.ideas[key] = keep
                    netmod.save_json_file(IDEAS_FILE, self.ideas)
                    self.csel = max(0, min(self.csel,
                                           len(self.cloud_notes) - 1))
                    self.osd = ("已删除", time.monotonic() + 1.2)
                else:
                    self.osd = ("删除失败 HTTP %s" % b.get("st"),
                                time.monotonic() + 1.5)
            elif a == "cloudping" and isinstance(b, dict) \
                    and not b.get("skip"):
                if b.get("ok"):
                    if b.get("user") and \
                            self.cfg.get("cloud_user") != b["user"]:
                        self.cfg["cloud_user"] = b["user"]   # 用户名回填
                        self.cfg_save()
                    self.osd = ("云连接 ✓ %s" % b.get("user"),
                                time.monotonic() + 2)
                else:
                    self.osd = ("云连接 ✗ HTTP %s" % b.get("st"),
                                time.monotonic() + 2.5)
            elif a == "cloudregqr" and isinstance(b, dict):
                self.reg_busy = False
                if b.get("ok") and b.get("dc"):
                    self.reg = None            # 关表单 → 出码弹层接管
                    self.qrlogin = {"st": "qr", "dc": b["dc"],
                                    "url": b["url"], "mat": b.get("mat"),
                                    "mode": "reg",
                                    "reg": dict(b.get("reg") or {})}
                    self.qr_poll_at = 0.0
                elif self.qrlogin is not None:  # 过期重出码失败 → 弹层报错
                    self.qrlogin.update(st="fail",
                                        err=b.get("err") or "出码失败")
                else:
                    err = b.get("err") or ("HTTP %s" % b.get("st")
                                           if b.get("st") else "出码失败")
                    self.osd = ("注册失败 %s" % err,
                                time.monotonic() + 2.5)
            elif a == "cloudlogin" and isinstance(b, dict):
                self.login_busy = False
                if b.get("ok"):
                    prev_user = self.cfg.get("cloud_user") or ""
                    new_user = b.get("user") or ""
                    self.cfg["cloud_tok"] = b["tok"]
                    if not self.cfg.get("cloud_url"):
                        self.cfg["cloud_url"] = b.get("url") \
                            or CLOUD_URL_DEFAULT
                    self.cfg["cloud_user"] = new_user
                    if prev_user and prev_user != new_user:
                        self.kv_switch = True    # 换号: 本地 kv 是旧号残留
                    self.cfg_save()
                    self.login = None          # 成功: 关表单 + ping 复核
                    if b.get("now"):           # 换号: csync 重置 (本地
                        self.ideas["csync"] = b["now"]   #   不向新号回放)
                        netmod.save_json_file(IDEAS_FILE, self.ideas)
                    self.cloud_ping_trigger()
                    self.kv_align_trigger()    # Oneapp 账户: 登录即对齐
                    self.osd = ("已登录 %s" % b.get("user"),
                                time.monotonic() + 2.5)
                else:
                    err = b.get("err") or ("HTTP %s" % b.get("st")
                                           if b.get("st") else "网络异常")
                    self.osd = ("登录失败 %s" % err,
                                time.monotonic() + 2.5)
            elif a == "cloudqrstart" and isinstance(b, dict) \
                    and self.qrlogin is not None:
                if b.get("ok"):
                    self.qrlogin.update(st="qr", dc=b["dc"], url=b["url"],
                                        mat=b.get("mat"))
                else:
                    self.qrlogin.update(st="fail",
                                        err=b.get("err") or "出码失败")
            elif a == "cloudwxbind" and isinstance(b, dict):
                if b.get("ok") and b.get("dc"):
                    self.qrlogin = {"st": "qr", "dc": b["dc"],
                                    "url": b["url"], "mat": b.get("mat"),
                                    "mode": "wxbind"}
                    self.qr_poll_at = 0.0
                elif self.qrlogin is not None:  # 重出码失败 → 弹层报错
                    self.qrlogin.update(st="fail",
                                        err=b.get("err") or "出码失败")
                else:
                    err = b.get("err") or ("HTTP %s" % b.get("st")
                                           if b.get("st") else "出码失败")
                    self.osd = ("绑定失败 %s" % err,
                                time.monotonic() + 2.5)
            elif a == "cloudqrpoll" and isinstance(b, dict) \
                    and self.qrlogin is not None:
                stt = b.get("st")
                isreg = (self.qrlogin or {}).get("mode") == "reg"
                iswx = (self.qrlogin or {}).get("mode") == "wxbind"
                if stt == "approved" and b.get("token"):
                    prev_user = self.cfg.get("cloud_user") or ""
                    new_user = b.get("user") or ""
                    if not self.cfg.get("cloud_url"):
                        self.cfg["cloud_url"] = CLOUD_URL_DEFAULT
                    self.cfg["cloud_tok"] = b["token"]
                    self.cfg["cloud_user"] = new_user
                    if prev_user and prev_user != new_user:
                        self.kv_switch = True    # 换号: 本地 kv 是旧号残留
                    self.cfg_save()
                    self.qrlogin = None        # 成功: 关扫码页
                    self.qr_poll_at = 0.0
                    if b.get("now"):           # 换号: csync 重置防回放
                        self.ideas["csync"] = b["now"]
                        netmod.save_json_file(IDEAS_FILE, self.ideas)
                    self.cloud_ping_trigger()
                    self.kv_align_trigger()    # Oneapp 账户: 登录即对齐
                    self.osd = (("注册成功 已绑定微信 %s" if isreg
                                 else "扫码登录 ✓ %s") % b.get("user"),
                                time.monotonic() + 2.5)
                elif stt == "bound":           # 设置·绑定微信 完成
                    self.qrlogin = None
                    self.qr_poll_at = 0.0
                    self.osd = ("微信已绑定 ✓", time.monotonic() + 2.5)
                elif stt == "none":            # 设备码过期 → 自动重出码
                    self.qr_poll_at = 0.0
                    if isreg:                  # reg: 表单已关, 存档重出
                        self.qrlogin.update(st="init")
                        self.net.invalidate(("job", "cloudregqr"))
                        self.net.job("cloudregqr", self._cloud_regqr_fn,
                                     ttl=10)
                    elif iswx:                 # wxbind: 直接重出绑定码
                        self.qrlogin.update(st="init")
                        self.net.invalidate(("job", "cloudwxbind"))
                        self.net.job("cloudwxbind", self._cloud_wxbind_fn,
                                     ttl=10)
                    else:
                        self._cloud_qr_open()
                elif stt == "done":            # token 被别的端领走(不该发生)
                    self.qrlogin.update(st="fail", err="授权已被领取, 重试")
                elif stt == "failed":          # 扫码注册竞态失败 (重名等)
                    self.qrlogin.update(st="fail",
                                        err=b.get("err") or "注册失败")
                elif stt == "pending":
                    self.qrlogin.update(st="wait")
            elif a.startswith("kvput:"):
                pass                           # 推送结果静默 (失败开机补推)
            elif a.startswith("kvalign:") and isinstance(b, dict):
                self._kv_align_step(a.split(":", 1)[1], b)
            elif a.startswith("kvsync:") and isinstance(b, dict):
                k = a.split(":", 1)[1]
                if b.get("ok"):
                    try:
                        lm = os.path.getmtime(KV_FILES[k])
                    except OSError:
                        lm = 0.0
                    cu = b.get("updated") or 0
                    local = netmod.load_json_file(KV_FILES[k], None)
                    _own = self.cfg.get("kv_owner") in (
                        None, "", self.cfg.get("cloud_user")) \
                        and not self.kv_switch      # 归属本账号才可推
                    if cu > lm + 2:
                        self.kv_apply(k, b.get("v"))   # 云端更新 → 覆盖
                    elif lm > cu + 2:
                        if self._kv_nonempty(local) and _own:
                            self.kv_push(k)            # 本地更新 → 补推
                        elif self._kv_nonempty(b.get("v")):
                            # 本地空且更新 → 不补推 (陈旧空列表盖云端 =
                            # 2026-11-20 收藏清零事故), 跟随云端救回
                            self.kv_apply(k, b.get("v"))
                elif b.get("st") == 404:       # 云端没存过: 本地非空即首推
                    local = netmod.load_json_file(KV_FILES[k], None)
                    if local and self.cfg.get("kv_owner") in (
                            None, "", self.cfg.get("cloud_user")) \
                            and not self.kv_switch:
                        # 只首推归属本账号 (或从未登录) 的本地数据 —
                        # 换号残留不克隆进新号 (2026-11-21 换号隔离)
                        self.kv_push(k)
                # (st==0 网络失败: 不裁决不首推 — 首推会把陈旧本地盖掉
                #  云端别的端的新收藏, 开机 WiFi 未就绪时必踩)
            elif a == "schedget" and isinstance(b, dict) \
                    and not b.get("skip"):
                if b.get("ok"):
                    self.sched = {"reminds": b.get("reminds") or [],
                                  "trips": b.get("trips") or []}
                    netmod.save_json_file(SCHED_CACHE, self.sched)
                    if self.tool == "sched":
                        self.osd = ("日程 %d 条"
                                    % len(self._sched_items()),
                                    time.monotonic() + 1.2)
                elif self.tool == "sched":
                    st = b.get("st")
                    self.osd = (("日程获取失败 HTTP %s" % st) if st
                                else "日程获取失败",
                                time.monotonic() + 2.5)
        elif kind == "error":                 # worker 里 fn 抛异常 (None) —
            if a.startswith("mail_list"):     # 以前静默吞, 失败根本不可见
                self.mail_err = "网络异常 · 稍后刷新"
            elif a.startswith("mail_body"):
                self.mail_body = "获取失败: 网络异常"
            elif a == "mail_send":
                self.mail_c_busy = False
                self.osd = ("发送失败: 网络异常", time.monotonic() + 3)
            elif a.startswith("mail_"):
                self.osd = ("网络异常", time.monotonic() + 2.5)
            elif a == "cloudsync":
                if getattr(self, "_cloud_pend_n", 0) > 0:
                    self.osd = ("云同步失败", time.monotonic() + 2)
            elif a == "cloudping":
                self.osd = ("云连接 ✗ 网络异常", time.monotonic() + 2)
            elif a == "cloudregqr":
                self.reg_busy = False
                if self.qrlogin is not None:    # 重出码网络异常 → 弹层报错
                    self.qrlogin.update(st="fail", err="网络异常")
                else:
                    self.osd = ("注册失败 网络异常", time.monotonic() + 2.5)
            elif a == "cloudlogin":
                self.login_busy = False
                self.osd = ("登录失败 网络异常", time.monotonic() + 2.5)
            elif a == "cloudqrstart":
                if self.qrlogin is not None:
                    self.qrlogin.update(st="fail", err="网络异常")
            elif a == "cloudwxbind":
                self.osd = ("绑定失败 网络异常", time.monotonic() + 2.5)
            elif a == "cloudqrpoll":
                self.qr_poll_at = 0.0          # 网络异常: 下帧重试 poll
            elif a.startswith("kvalign:"):
                if self.kv_switch:             # 换号对齐: 失败也清 (归旧号)
                    k2 = a.split(":", 1)[1]
                    if self.kv_queue and self.kv_queue[0] == k2:
                        self.kv_queue.pop(0)
                    self.kv_apply(k2, None)
                    self.cfg["kv_owner"] = self.cfg.get("cloud_user") or ""
                    self.cfg_save()
                elif self.kv_queue:
                    self.kv_queue.pop(0)
                self.kv_align_next()
            elif a.startswith("kvsync:") or a.startswith("kvput:"):
                pass
            elif a == "schedget":
                if self.tool == "sched":
                    self.osd = ("日程获取失败 · 网络异常",
                                time.monotonic() + 2.5)
        elif kind == "text":
            self.data[a] = b
            if b and a in ("quotes", "idx"):
                self.fin_ts = time.strftime("%H:%M:%S")
            if b and a == "quotes":           # 主页自选股卡缓存
                try:
                    os.makedirs(CACHE_DIR, exist_ok=True)
                    netmod.save_json_file(
                        os.path.join(CACHE_DIR, "quotes.json"), b)
                except Exception:
                    pass
        elif kind == "bin":
            if b:
                self.data[a] = b                 # 封面字节
                if a.startswith("mupic:"):       # 音乐在线封面 (换曲防串图)
                    if a == "mupic:%d" % self.mu_pic_seq:
                        self.mu_pic = jpg_surf(b, 84, 84)

    # ── 每秒请求 (worker 按 TTL 去重) ──
    def poll(self):
        """按页取数: 只拉当前页所需, 页面切换即时响应不排队."""
        self.mu_tick()                        # 音乐连播: 任意页/离线都要走 (本地曲库)
        if self.net.mode == "online":
            if self.page == 0:
                self.net.job("news", news_rss.news_fetch_all, 600)   # 主页新闻卡
                if self._qt_codes():          # 自选股卡行情
                    self.net.pub_text(QT_BASE + ",".join(
                        c for c in self._qt_codes()), "quotes", 12)
                self.net.job("wxloc", fetchers.wx_locate, 1800)      # IP 归属地 30min 重探
                self.net.pub(wx_url(self.wx_loc), "wx", 600, direct=True)
            if self.page == 2:
                self._raop_scan_lazy()        # 空缓存时进电台页后台补扫一次
                self.kv_pull("radio_fav")     # 电台收藏云端拉取 (开机拉取
                                              #   失败/跨端变更的兜底, TTL 节流)
            if self.page == 3:
                self.net.job("ipo", fetchers.ipo_fetch, 1800)   # 顶部跑马灯
                self.kv_pull("watch")         # 自选股云端拉取 (跨端变更/开机
                                              #   拉取失败的兜底, TTL 节流)
                self.net.pub_text(QT_BASE + ",".join(
                    c for c in self._qt_codes()), "quotes", 12)
                self.net.pub_text(QT_BASE + ",".join(c for _, c in IDX_LIST),
                                   "idx", 15)
                self.net.pub_text(QT_BASE + ",".join(HF_CODES), "cmd", 30)
                self.net.pub(GATE_URL, "gate", 30)
                if self.fdetail:
                    code = self.fdetail["code"]
                    self.net.pub(IFZQ_BASE + "minute/query?code=" + code,
                                 "minute", 60)
                    self.net.pub(IFZQ_BASE + "fqkline/get?param=" + code +
                                 ",day,,,250,qfq", "day", 60)
            if self.page == 4:
                self.net.job("news", news_rss.news_fetch_all, 600)
                self.kv_pull("news_feeds")    # 源表云端拉取兜底 (TTL 节流)
            if self.page == 5:
                self.poll_tool()
            if self.rec_proc and time.monotonic() - self.rec_started >= REC_MAX:
                self.rec_stop()                # 60s 自动截断
            if self.player and self.playing_voice \
                    and self.player.poll() is not None:
                self.player = None             # 语音播完自动收
                self.playing_voice = None
            if self.playing_key:
                if (self.player is not None and self.player.poll() is not None) \
                        or (self.raop_cons is not None
                            and self.raop_cons.poll() is not None):
                    self.radio_stop()          # ffplay 已退出 / RAOP 会话已断
                    self.osd = ("流已断", time.monotonic() + 1.5)
                elif self.raop_cons is not None and self.raop_up_at is None \
                        and time.monotonic() - self.play_at > 50:
                    self.radio_stop()          # RAOP 预读+连接 50s 没到 = 挂死
                    self.osd = ("AirPlay 未连上", time.monotonic() + 2.0)

    def poll_tool(self):
        """工具子应用按需取数 (poll 每秒调; TTL 层去重)."""
        self.timer_tick()
        t = self.tool
        if t == "fx":
            self.net.job("rate", fetchers.fx_fetch, 3600)
        elif t == "lot":
            self.net.job("ssq", fetchers.ssq_fetch, 600)
            self.net.job("dlt", fetchers.dlt_fetch, 600)
        elif t == "music":
            self._mu_qr_poll()                # 扫码状态机 (ok→搜索/fail→源页)
        elif t == "mail":
            self.poll_mail()                  # TTL 层去重, 各层只拉当层数据
        elif t == "tv":
            self.tv_watchdog()                # 起播超时/断流/退出分类

    # ── 想法列表 (文字+语音按时间倒序合并) ──
    def idea_items(self):
        """本地缓存列表 — 只显示当前登录账户的条目 (owner 归属过滤);
        未登录显示全部本地 (离线只读)."""
        user = self.cfg.get("cloud_user") or ""
        items = [{"kind": "text", "id": it.get("id"),
                  "cid": it.get("cid"),
                  "text": it.get("text", ""),
                  "createdAt": it.get("createdAt", 0)}
                 for it in self.ideas.get("texts", [])
                 if not user or it.get("owner", user) == user]
        for it in self.ideas.get("voices", []):
            if user and it.get("owner", user) != user:
                continue
            items.append({"kind": "voice", "id": it.get("id"),
                          "cid": it.get("cid"),
                          "file": it.get("file", ""),
                          "dur": it.get("dur", 0),
                          "createdAt": it.get("createdAt", 0)})
        items.sort(key=lambda x: -x["createdAt"])
        return items

    def _qt_codes(self):
        return [qt_code(it["code"]) for it in self.watch.get("items", [])
                if it.get("code")][:16]

    def fin_add_commit(self):
        """输入框 Enter: 规范化代码 → 去重 → 落 watch.json (登录即上云)."""
        code = norm_stock_code(self.buf)
        self.buf = ""
        if not code:
            self.osd = ("代码无效 · 如 600519 / sh600519", time.monotonic() + 2)
            return
        if any(it.get("code") == code for it in self.watch.get("items", [])):
            self.osd = ("已在自选股", time.monotonic() + 1.5)
            return
        self.watch.setdefault("items", []).append({"code": code, "name": ""})
        self.watch_save()
        self.osd = ("已添加 " + code
                    + (" · 已上云" if self.cfg.get("cloud_tok") else ""),
                    time.monotonic() + 1.5)

    def watch_del(self, code):
        """删除自选股 (dialog 确认后调, 按 code) → 落 watch.json (登录即上云)."""
        items = self.watch.get("items", [])
        n0 = len(items)
        self.watch["items"] = [x for x in items if x.get("code") != code]
        if len(self.watch["items"]) != n0:
            self.watch_save()
            self.fsel = max(0, min(self.fsel,
                                   len(self.watch["items"]) - 1))
            self.osd = ("已删除 " + (code or "")
                        + (" · 已上云" if self.cfg.get("cloud_tok") else ""),
                        time.monotonic() + 1.2)

    def open_detail(self, code, name, view="min"):
        self.fdetail = {"code": code, "name": name, "view": view, "zoom": 1.0}

    def ime_clear(self):
        """清空 IME 状态 (进出输入框/提交后调; 保留中英模式)."""
        self.ime_buf = ""
        self.ime_cands = []
        self.ime_assoc = None
        self.ime_page = 0

    def ime_commit(self, w):
        """提交候选词/字 → 追加文本 + 出联想."""
        self.ime_buf = ""
        self.ime_cands = []
        self.ime_page = 0
        self.ime_assoc = ime.assoc(w[-1]) or None
        return ("word", w)

    def ime_key(self, k, ch):
        """拼音 IME 键处理 (仅中文模式调用). 返回 None=已消化;
        ("char", s)=文本追加; ("del", "")=删已提交末字符; ("submit",)=提交输入框.
        键位: 字母=拼音 (设备 f/z/x/c 经 unicode 同样进串), 空格=首选,
        1-9=选候选, \u2191\u2193=翻页 (真方向键; 复用键事件带 unicode 不入此支),
        Backspace=缩串/删字, Enter=拼音串转 ASCII 直提."""
        if k in (pygame.K_UP, pygame.K_DOWN) and not ch:
            n = len(self.ime_cands) if self.ime_buf else len(self.ime_assoc or [])
            if n > 8:
                d = -1 if k == pygame.K_UP else 1
                self.ime_page = (self.ime_page + d) % ((n + 7) // 8)
            return None
        if k == pygame.K_BACKSPACE:
            if self.ime_buf:
                self.ime_buf = self.ime_buf[:-1]
                self.ime_page = 0
                self.ime_cands = ime.candidates(self.ime_buf)
                return None
            return ("del", "")                # 2 元组对齐 (调用处 tag, val 解包)
        if ch and "a" <= ch.lower() <= "z":
            self.ime_assoc = None
            if len(self.ime_buf) < 12:
                self.ime_buf += ch.lower()
            self.ime_page = 0
            self.ime_cands = ime.candidates(self.ime_buf)
            return None
        cands = self.ime_cands if self.ime_buf else (self.ime_assoc or [])
        if ch and ch != "0" and ch.isdigit():
            i = self.ime_page * 8 + (int(ch) - 1)
            if cands and i < len(cands):
                return self.ime_commit(cands[i])
            return ("char", ch)               # 无候选 → ASCII 直通
        if k == pygame.K_SPACE:
            if cands:
                return self.ime_commit(cands[self.ime_page * 8])
            return ("char", " ")
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER) and self.ime_buf:
            s = self.ime_buf
            self.ime_clear()
            return ("char", s)
        if ch and 0x20 <= ord(ch) < 0x7F:     # 其余 ASCII (0/符号) 直通
            return ("char", ch)
        return None

    def ime_bar(self, surf, y):
        """候选条 (拼音/联想共用): 编号候选 + 页码指示. 返回 True=有画."""
        if self.ime_buf:
            cands = self.ime_cands
            head = self.ime_buf
        elif self.ime_assoc:
            cands = self.ime_assoc
            head = "联"
        else:
            return False
        acc, soft, dim = (self.th("accent"), self.th("soft"),
                          self.th("dim"))
        text(surf, head, 11, acc, (6, y))
        x = 6 + textw(head, 11) + 8
        start = self.ime_page * 8
        for i in range(start, min(len(cands), start + 8)):
            s = "%d%s" % (i - start + 1, cands[i])
            w = textw(s, 11)
            if x + w > W - 30:
                break
            text(surf, s, 11, soft, (x, y))
            x += w + 6
        np = max(1, (len(cands) + 7) // 8)
        if np > 1:
            text(surf, "%d/%d" % (self.ime_page + 1, np), 10, dim,
                 (W - 6, y + 1), right=True)
        return True

    def stop_player(self):
        if self.player:
            try:
                self.player.terminate()
            except Exception:
                pass
            self.player = None

    # ── 电台 ──
    @staticmethod
    def radio_tabs():
        """分组页签: 收藏在最前 (打开即看), 后接四组."""
        import radio_stations
        return ["★"] + radio_stations.GROUPS

    @staticmethod
    def radio_fav_load():
        """收藏 key 表 (radio_fav.json); 坏数据当空."""
        v = netmod.load_json_file(RADIO_FAV_CACHE, []) or []
        return [k for k in v if isinstance(k, str)]

    def radio_list(self, grp=None):
        """分组台表 (grp=None → 当前组). 0=收藏 (key→台, 缺剔);
        省级/地方组按 IP 归属地本地置顶 (同城>同省>其他, 稳定排序)."""
        import radio_stations
        g = self.radio_grp if grp is None else grp
        if g == 0:
            by = {s["key"]: s for s in radio_stations.STATIONS}
            return [by[k] for k in self.radio_fav if k in by]
        lst = [s for s in radio_stations.STATIONS
               if s["grp"] == radio_stations.GROUPS[g - 1]]
        prov, city = self.radio_local()
        if prov or city:
            lst = sorted(lst, key=lambda s: radio_stations.local_rank(
                s, prov, city))
        return lst

    def radio_local(self):
        """IP 归属地 → (prov, city) 匹配锚点; 未定位/境外返回 (None, None)."""
        import radio_stations
        w = self.wx_loc or {}
        prov = radio_stations.norm_prov(w.get("prov"))
        city = radio_stations.norm_city(w.get("city"))
        return (prov, city)

    def radio_grp_cycle(self, d):
        """←→ 切分组 (光标按组记忆; 空组归 0)."""
        self.radio_sels[self.radio_grp] = self.rsel
        self.radio_grp = (self.radio_grp + d) % len(self.radio_tabs())
        lst = self.radio_list()
        self.rsel = min(self.radio_sels.get(self.radio_grp, 0),
                        max(0, len(lst) - 1))
        self.rscroll = max(0, min(self.rsel - 5, max(0, len(lst) - 7)))

    def radio_fav_toggle(self):
        """5 键收藏/取消 (播放中=当前台, 列表=选中台); 落 radio_fav.json.
        取消需二次确认 (用户定版 2026-11-20: 只能逐个删且防误按)."""
        import radio_stations
        cur = next((s for s in radio_stations.STATIONS
                    if s["key"] == self.playing_key), None)
        lst = self.radio_list()
        st = cur or (lst[self.rsel] if 0 <= self.rsel < len(lst) else None)
        if not st:
            return
        k = st["key"]
        if k in self.radio_fav:
            self.dialog = ("unfav", k, st["name"])
            return
        self.radio_fav.append(k)
        self.osd = ("已收藏 · %s" % st["name"], time.monotonic() + 1.5)
        self.radio_fav_save()
        n = len(self.radio_list())            # 收藏组可能变短, 光标钳回
        if self.radio_grp == 0 and n:
            self.rsel = min(self.rsel, n - 1)

    def radio_fav_remove(self, k):
        """确认框 Enter 后真正移除 (radio_fav_toggle 的取消半程)."""
        if k not in self.radio_fav:
            return
        self.radio_fav.remove(k)
        self.radio_fav_save()
        self.osd = ("已取消收藏", time.monotonic() + 1.2)
        n = len(self.radio_list())
        if self.radio_grp == 0 and n:
            self.rsel = min(self.rsel, n - 1)

    # ── Oneapp 账户 kv 云同步 (radio_fav / watch; noteserver /v1/kv) ──
    def watch_save(self):
        netmod.save_json_file(WATCH_FILE, self.watch)
        self.kv_push("watch")

    def radio_fav_save(self):
        netmod.save_json_file(RADIO_FAV_CACHE, self.radio_fav)
        self.kv_push("radio_fav")

    def tv_fav_save(self):
        netmod.save_json_file(TV_FAV_CACHE, self.tv_fav)
        self.kv_push("tv_fav")

    def news_feeds_save(self):
        """新闻源表落盘 + kv 推云 + 抓取层换生效源表 (在线时立即重抓)."""
        netmod.save_json_file(NEWS_FEEDS_FILE, self.news_feeds)
        news_rss.set_feeds(self.news_feeds)
        self.net.invalidate(("job", "news"))
        if self.net.mode == "online":
            self.net.job("news", news_rss.news_fetch_all, 600)
        self.kv_push("news_feeds")

    def _cloud_base(self):
        return (self.cfg.get("cloud_url") or CLOUD_URL_DEFAULT).rstrip("/")

    def kv_push(self, k):
        """本地文件变更后推云端 (登录态才推; 失败静默, 下次变更/开机补).
        显式变更 = 归属落位: 残留 kv_owner(旧号/善后态) ≠ 当前号 → 落位
        当前号 — 用户在哪个号上手改数据, 文件就归哪个号 (静默开机补推/
        首推路径仍被归属闸拦, 不经本落位)."""
        if not self.cfg.get("cloud_tok"):
            return
        if self.cfg.get("kv_owner") not in (None, "") \
                and self.cfg["kv_owner"] != self.cfg.get("cloud_user"):
            self.cfg["kv_owner"] = self.cfg.get("cloud_user") or ""
            self.cfg_save()
        self.net.invalidate(("job", "kvput:" + k))
        self.net.job("kvput:" + k, lambda k=k: self._kv_put_fn(k), ttl=10)

    def _kv_put_fn(self, k):
        tok = (self.cfg.get("cloud_tok") or "").strip()
        try:
            data = open(KV_FILES[k]).read()
            json.loads(data)
        except Exception:
            return {"ok": 0, "st": 0}
        st, _b = netmod.http_raw(self._cloud_base() + "/v1/kv/" + k,
                                 method="PUT", body=data.encode(),
                                 timeout=8,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + tok})
        return {"ok": 1 if st == 200 else 0, "st": st}

    def _kv_get_fn(self, k):
        tok = (self.cfg.get("cloud_tok") or "").strip()
        st, body = netmod.http_raw(self._cloud_base() + "/v1/kv/" + k,
                                   timeout=8,
                                   headers={"Authorization":
                                            "Bearer " + tok})
        if st == 0:                       # 网络不可达: 返回 None → 结果不落
            return None                   #   job 缓存 (可重试) 且不进裁决
        try:
            j = json.loads(body.decode("utf-8", "replace"))
        except Exception:
            j = {}
        return {"ok": 1 if (st == 200 and j.get("ok")) else 0, "st": st,
                "v": j.get("v"), "updated": j.get("updated") or 0}

    def kv_start_pull(self):
        """开机静默 LWW: 云端 updated 比本地文件 mtime 新 → 云端覆盖;
        本地更新 → 补推; 相近 (±2s) 不动."""
        for k in KV_FILES:
            self.net.invalidate(("job", "kvsync:" + k))
            self.net.job("kvsync:" + k, lambda k=k: self._kv_get_fn(k),
                         ttl=10)

    @staticmethod
    def _kv_nonempty(v):
        """kv 值非空判定 (列表类/字典类通用): 收藏清零防护用."""
        if isinstance(v, list):
            return len(v) > 0
        if isinstance(v, dict):
            return len(v.get("items", []) or []) > 0
        return False

    def kv_pull(self, k, ttl=60):
        """页面级静默拉取 (开机 LWW 的兜底): 开机拉取撞上 WiFi 未就绪
        或别的端刚改过 → 进页补拉. TTL 节流; 失败结果不落缓存 →
        每秒 poll 自动重试到成功为止. 裁决同 kvsync LWW."""
        if self.cfg.get("cloud_tok"):
            self.net.job("kvsync:" + k, lambda k=k: self._kv_get_fn(k),
                         ttl=ttl)

    def kv_align_trigger(self):
        """登录成功后: 逐服务本地 vs 云端对齐; 本地有记录→确认框."""
        self.kv_confirm = None
        self.kv_queue = list(KV_FILES)
        self.kv_align_next()

    def kv_align_next(self):
        if not self.kv_queue or not self.cfg.get("cloud_tok"):
            self.kv_queue = []
            self.kv_switch = False             # 队列排空/中止 → 复位
            return
        k = self.kv_queue[0]
        self.net.invalidate(("job", "kvalign:" + k))
        self.net.job("kvalign:" + k, lambda k=k: self._kv_get_fn(k), ttl=10)

    def _kv_align_step(self, k, b):
        """拉取结果裁决: 双空/失败跳过; 本地空→直接跟随云端;
        本地与云端 (归一后) 一致→静默过; 真不一致→确认框
        (Enter 上传 / Esc 云端覆盖).
        换号 (kv_switch): 本地是旧号残留 — 静默跟随新号云端,
        404/失败清空本地 (旧数据已在旧号云端, 清本地无损)."""
        if self.kv_queue and self.kv_queue[0] == k:
            self.kv_queue.pop(0)
        if self.kv_switch:
            self.kv_apply(k, b.get("v") if b.get("ok") else None)
            self.cfg["kv_owner"] = self.cfg.get("cloud_user") or ""
            self.cfg_save()                    # 该文件已归新号
            return self.kv_align_next()
        if not b.get("ok"):
            return self.kv_align_next()
        local = netmod.load_json_file(KV_FILES[k], None)
        cv = b.get("v")
        if local:
            if cv is not None and \
                    self._kv_norm(k, local) == self._kv_norm(k, cv):
                return self.kv_align_next()    # 已一致: 免确认静默过
            n = len(local) if isinstance(local, list) else \
                len(local.get("items", []) or [])
            cn = len(cv) if isinstance(cv, list) else \
                len((cv or {}).get("items", []) or [])
            self.kv_confirm = {"k": k, "n": n, "cn": cn, "v": cv}
            return                             # 模态: set_key 裁决后继续
        if cv is not None:
            self.kv_apply(k, cv)
        self.kv_align_next()

    def _kv_norm(self, k, v):
        """kv 对齐比较用归一 — 两侧各过一遍再比, 与 kv_apply 落地形状
        同构 (云端旧形状/缺新内置源不算差异); 一致 → 不弹确认框."""
        if k in ("radio_fav", "tv_fav"):
            return [x for x in (v or []) if isinstance(x, str)]
        if k == "watch":
            return (v or {}).get("items", []) \
                if isinstance(v, dict) else []
        if k == "news_feeds":
            feeds = [dict(f) for f in (v or []) if isinstance(f, dict)
                     and f.get("id") and f.get("url")]
            self._news_feeds_merge(feeds)      # 内置补齐 (原地追加种子)
            for f in feeds:
                f["on"] = bool(f.get("on", True))
            return feeds
        return v

    def kv_apply(self, k, v):
        """云端覆盖本地 (内存+文件; 直写不走 save helpers 防回推)."""
        if k == "watch":
            items = (v or {}).get("items", []) if isinstance(v, dict) else []
            self.watch = {"items": items}
            self.fsel = self.fscroll = 0
            netmod.save_json_file(KV_FILES[k], self.watch)
            return
        if k == "news_feeds":
            feeds = [f for f in (v or []) if isinstance(f, dict)
                     and f.get("id") and f.get("url")]
            if not feeds:
                # 云端空/404/换号清空 → 内置种子兜底 (不允许零源;
                # 清零事故同款哲学: 空态不落地)
                feeds = news_rss.defaults()
            self._news_feeds_merge(feeds)        # 云端旧列表缺新内置源 → 补齐
            for f in feeds:
                f["on"] = bool(f.get("on", True))
            self.news_feeds = feeds
            news_rss.set_feeds(feeds)
            self.news_src_i = 0                  # 可见源集可能变短
            self.news_sel = self.news_scroll = 0
            self.news_detail = None
            self.ns_sel = self.ns_scroll = 0     # 设置视图开着也回到列表头
            self.ns_input = 0                    # 输入中列表被云端换 → 收掉
            self.ns_url = ""
            self.ns_edit_id = None
            self.buf = ""
            netmod.save_json_file(KV_FILES[k], feeds)   # 直写防回推
            return
        if k in ("radio_fav", "tv_fav"):
            val = [x for x in (v or []) if isinstance(x, str)]
            setattr(self, k, val)
            netmod.save_json_file(KV_FILES[k], val)
            if k == "radio_fav" and self.radio_grp == 0:
                # 页面正开着收藏组: 列表可能变短, 光标钳回 (缺台被剔再缩一截)
                self.rsel = min(self.rsel,
                                max(0, len(self.radio_list()) - 1))
            elif k == "tv_fav" and self.tv_grp == 0:
                self.tv_sel = min(self.tv_sel,
                                  max(0, len(self.tv_list()) - 1))

    def radio_st_by_key(self, key):
        import radio_stations
        return next((s for s in radio_stations.STATIONS
                     if s["key"] == key), None)

    def _raop_avail(self):
        """当前网段可用的音箱: 缓存里的家里音箱在外地 (不同网段) 根本连不
        上, 输出循环不提供死路 (按 O/5 一路跳进不可达音箱 = 卡死无声音的
        根因, 2026-11-19); 网段拿不到 (无路由) 时放行不过滤."""
        net = _raop_local_net()
        if not net:
            return self.raop
        return [d for d in self.raop
                if (d.get("ip") or "").rsplit(".", 1)[0] == net]

    def radio_out_name(self):
        """输出名: 本机 / AirPlay·<设备名> (只按可用设备表取名)."""
        raop = self._raop_avail()
        if self.radio_out == 0 or not raop:
            return "本机"
        d = raop[min(self.radio_out - 1, len(raop) - 1)]
        return "AirPlay·%s" % d.get("name", "?")

    def _raop_dev(self):
        """当前输出对应的 RAOP 设备 (越界/不在本网段 → None=本机)."""
        raop = self._raop_avail()
        if self.radio_out >= 1 and raop:
            return raop[min(self.radio_out - 1, len(raop) - 1)]
        return None

    def radio_out_cycle(self):
        """O 键: 输出 本机→AirPlay 设备循环; 播放中换输出 = 旧输出停 +
        新输出续播同台 (dash 电台 o 键同款). 本网段无可用设备时归位本机
        (外地/换网: 不再循环进不可达的死路音箱)."""
        raop = self._raop_avail()
        if not raop:
            self._raop_scan_lazy()            # 首按 O 顺带触发一次后台补扫
            self.radio_out = 0                # 死路选择归位本机
            netmod.save_json_file(RADIO_OUT_FILE, "")
            self.osd = ("无 AirPlay 设备 (本网段无音箱)",
                        time.monotonic() + 1.5)
            if self.playing_key:              # AirPlay 僵会话 → 回本机重放
                st = self.radio_st_by_key(self.playing_key)
                self.radio_stop()
                if st:
                    self.radio_play(st)
            return
        prev = self.playing_key
        if prev:
            self.radio_stop()                 # 先停旧输出 (必须在改 radio_out 前)
        self.radio_out = (self.radio_out + 1) % (len(raop) + 1)
        netmod.save_json_file(RADIO_OUT_FILE,
                              raop[self.radio_out - 1].get("id", "")
                              if self.radio_out >= 1 else "")
        self.osd = ("输出: %s" % self.radio_out_name(),
                    time.monotonic() + 1.5)
        if prev:
            st = self.radio_st_by_key(prev)
            if st:
                self.radio_play(st)

    def _raop_scan_lazy(self):
        """电台页按需补扫一次 (空缓存才扫; 结果落 RAOP_CACHE). 不阻塞 UI."""
        if self._raop_scanned:
            return
        self._raop_scanned = True

        def _run():
            try:
                devs = raop_scan(12)
            except Exception:
                devs = []
            if devs:
                self.raop = devs
                netmod.save_json_file(RAOP_CACHE, devs)

        threading.Thread(target=_run, daemon=True).start()

    def radio_play(self, st):
        self.radio_stop()
        if self.mu_proc or self.mu_playing:   # 音频焦点: 音乐让路
            self.mu_stop()
        if self.tv_playing:                   # 音频焦点: 电视让路
            self.tv_stop()
        self.raop_up_at = None                # 新会话: RAOP-UP 重新等
        dev = self._raop_dev()
        if dev:                               # AirPlay 输出: RAOP 直推
            if self._radio_play_raop(st, dev):
                return
            self.radio_out = 0                # 起播失败回落本机 (继续往下走)
        # 本机: 直连公开流 URL. -reconnect*: WiFi 停顿导致 TCP 断时 ffplay
        # 自动重拉同 URL (<5s 退避) 恢复播放, 而不是死掉报"流已断"; -infbuf
        # 不限输入缓冲, 吸收直播上游的突发空窗 (HLS 几秒一批).
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
               "-vn", "-infbuf"] + _ff_slow_net_flags() + \
              ["-reconnect", "1", "-reconnect_streamed", "1",
               "-reconnect_delay_max", "5", st["url"]]
        try:
            self.player = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL,
                                           env=_AUDIO_ENV, **_FFPLAY_KW)
            self.playing_key = st["key"]
            self.play_at = time.monotonic()
            self.lvl.start_parec()            # VU: pulse monitor 采本机输出
            self.osd = (st["name"], time.monotonic() + 1.5)
        except Exception:
            self.osd = ("播放失败", time.monotonic() + 1.2)

    def _radio_play_raop(self, st, dev):
        """AirPlay 播放: ffmpeg (-re 节流) → flac 管道 → atvremote stream=-.
        成功置 playing_key 返回 True; 失败返回 False (radio_play 回落本机).
        VU 旁路 FIFO wav (AirPlay 不走本地 pulse, monitor 采不到).
        音量控制 FIFO (O_RDWR 自持不互等) → 消费端线程 → 同会话 SET_PARAMETER."""
        vufifo = "/tmp/oneapp_vu_%d.fifo" % int(time.monotonic() * 1000)
        self._vol_fifo_close()                # 上一会话残留 (异常退出兜底)
        try:
            os.unlink(vufifo)
        except Exception:
            pass
        os.mkfifo(vufifo, 0o600)
        volfifo = "/tmp/oneapp_vol_%d.fifo" % int(time.monotonic() * 1000)
        try:
            os.unlink(volfifo)
        except Exception:
            pass
        os.mkfifo(volfifo, 0o600)
        self.vol_fifo = volfifo
        self.vol_w = os.open(volfifo, os.O_RDWR | os.O_NONBLOCK)
        apfh = None
        try:                                  # stderr 落盘: 无声/失败可回查
            aplog = "/tmp/oneapp_radio_ap.log"
            if os.path.exists(aplog) and os.path.getsize(aplog) > 200 * 1024:
                os.truncate(aplog, 0)
            apfh = open(aplog, "ab")
            apfh.write(("\n==== %s dev=%s %s ====\n" % (
                time.strftime("%m-%d %H:%M:%S"),
                dev.get("name"), dev["ip"])).encode())
        except Exception:
            apfh = None
        try:
            cons = subprocess.Popen(
                [sys.executable, "-c", _ATVR_PLAY, dev["ip"],
                 str(dev.get("port", 7000)), dev.get("id", ""),
                 (st.get("name") or "")[:120], "", volfifo],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=apfh or subprocess.DEVNULL, env=_AUDIO_ENV,
                preexec_fn=_FFPLAY_KW.get("preexec_fn"), close_fds=True)
        except Exception:
            self._vol_fifo_close()
            self._raop_cleanup(vufifo, apfh)
            self.osd = ("AirPlay 启动失败 (pip install pyatv==0.18.0)",
                        time.monotonic() + 2.0)
            return False
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-vn", "-re"]
        # -y: FIFO 路径已存在时 ffmpeg 默认交互询问后退出 (无声坑); -re:
        # 直播流按原速读, 不限速会把上游突发全缓冲 → 延迟无限增大
        cmd += ["-reconnect", "1", "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5"] + _ff_slow_net_flags()
        cmd += ["-i", st["url"], "-f", "s16le", "-ar", "44100", "-ac", "2",
                "-", "-f", "wav", vufifo]
        try:
            self.player = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=cons.stdin,
                stderr=apfh or subprocess.DEVNULL, env=_AUDIO_ENV,
                preexec_fn=_FFPLAY_KW.get("preexec_fn"), close_fds=True)
        except Exception:
            self._radio_kill(cons)
            self._vol_fifo_close()
            self._raop_cleanup(vufifo, apfh)
            self.osd = ("AirPlay 解码启动失败", time.monotonic() + 1.5)
            return False
        try:
            cons.stdin.close()                # 父端 stdin 副本即关 — ffmpeg
        except Exception:                     # 死=真 EOF, 消费端不再无限等流
            pass
        if apfh:
            try:
                apfh.close()                  # 子进程持有副本, 父端即关
            except Exception:
                pass
        self.raop_cons = cons
        threading.Thread(target=self._raop_watch, args=(cons,),
                         daemon=True).start()
        self.lvl.start_fifo(vufifo)           # 读端 NONBLOCK 开, 不与写端互等
        self.playing_key = st["key"]
        self.play_at = time.monotonic()
        self.osd = (st["name"], time.monotonic() + 1.5)
        return True

    def _vol_fifo_close(self):
        """音量控制 FIFO 收尾 (写端关闭 → 消费端命令线程 EOF 退出)."""
        if self.vol_w is not None:
            try:
                os.close(self.vol_w)
            except Exception:
                pass
            self.vol_w = None
        if self.vol_fifo:
            try:
                os.unlink(self.vol_fifo)
            except Exception:
                pass
            self.vol_fifo = None

    def _raop_watch(self, proc):
        """消费端 stdout: RAOP-UP → 出声锚点 (看门狗判未连上用); RAOPVOL <n>
        → 采纳 pyatv 视角会话音量 (音箱端/iPhone 侧变更会被 pyatv 拦截回流)."""
        try:
            for line in proc.stdout:
                line = line.decode(errors="replace").strip()
                if line.startswith("RAOP-UP"):
                    self.raop_up_at = time.monotonic()
                elif line.startswith("RAOPVOL "):
                    try:
                        self.raop_vol = max(0,
                                            min(100, int(float(line.split()[1]))))
                    except Exception:
                        pass
        except Exception:
            pass

    def _raop_volume(self, code, out_name=None):
        """AirPlay 会话内音量 (电台/音乐共用): SETVOL 走控制 FIFO → 消费端
        atv.audio.set_volume (同 RTSP 会话直达音箱, iPhone 播放条同款).
        不新连会话 — 频繁重连触发音箱冷却拒连 (dash 实证). 音量记忆落盘."""
        if code == 113:                       # Fn+A 静音 ↔ 恢复
            if self.raop_mute_prev is None:
                self.raop_mute_prev = max(self.raop_vol, 20)
                v = 0
            else:
                v, self.raop_mute_prev = self.raop_mute_prev, None
        else:                                 # Fn+S+/Fn+D- ±5, 钳 0-100
            self.raop_mute_prev = None
            v = max(0, min(100, self.raop_vol + (-5 if code == 114 else 5)))
        self.raop_vol = v
        try:
            netmod.save_json_file(RAOP_VOL_FILE, v)
        except Exception:
            pass
        if self.vol_w is not None:
            try:
                os.write(self.vol_w, ("SETVOL %d\n" % v).encode())
            except Exception:
                pass
        self.osd = ("%s %d%% · %s" % ("静音" if v == 0 else "音量",
                                      v, out_name or self.radio_out_name()),
                    time.monotonic() + 1.2)

    @staticmethod
    def _raop_cleanup(vufifo, apfh):
        if apfh:
            try:
                apfh.close()
            except Exception:
                pass
        try:
            os.unlink(vufifo)
        except Exception:
            pass

    @staticmethod
    def _radio_kill(proc, grace=0.4):
        """后台补刀: terminate → grace 秒 → SIGKILL. 设备 ffmpeg 7.1.5 收
        SIGTERM 会卡死在 futex (双输出 muxer 优雅退出互锁, 2026-11-14 实证:
        僵死进程 hold 直播流+fifo 写端, 每次换台泄漏一个 → 越用越卡), 必须
        SIGKILL; 线程化不阻塞 UI, wait() 顺带收尸防僵尸."""
        if not proc:
            return

        def _fin():
            try:
                proc.terminate()
            except Exception:
                return
            try:
                proc.wait(timeout=grace)
                return
            except Exception:
                pass
            try:
                proc.kill()
                proc.wait(timeout=1.5)
            except Exception:
                pass

        threading.Thread(target=_fin, daemon=True).start()

    def radio_stop(self):
        self._radio_kill(self.player)         # 产端 (ffplay 本机 / ffmpeg AirPlay)
        self.player = None
        # 消费端长宽限: SIGTERM → 优雅取消 stream → finally RTSP TEARDOWN
        # (暴力拆连让音箱进冷却拒连, 下一次换台秒死 — 实证); futex 卡死才兜底 K9
        self._radio_kill(self.raop_cons, grace=2.5)
        self.raop_cons = None
        self._vol_fifo_close()                # 音量命令通道随会话关
        self.raop_up_at = None
        self.playing_key = None
        self.lvl.stop()                       # 电平源随播放进程一起断
        self.vu = [3.0] * 12
        self.vu_pk = [3.0] * 12

    def radio_toggle(self):
        sts = self.radio_list()
        if not (0 <= self.rsel < len(sts)):
            return
        if self.playing_key == sts[self.rsel]["key"]:
            self.radio_stop()               # 同台: Enter = 播/停
            return
        self.radio_play(sts[self.rsel])     # 异台: 一次 Enter 直接切台
        # (radio_play 内部先 radio_stop 旧会话; 旧码播放中一律先停, 切台要按两次)

    # ── 音乐 app: 本地曲库直读 + QQ/网易直连 ──
    def mu_rescan(self):
        """进列表页重扫 (60s 节流); 扫描中当前播放文件可能换序 → idx 失效则止播."""
        now = time.monotonic()
        if self.mu_files and now - self.mu_scan_at < 60:
            return
        keep = (self.mu_playing or {}).get("path")
        self.mu_files = scan_music_dir(MUSIC_DIR)
        self.mu_scan_at = now
        if keep:                              # 正在播的曲子重新对位 (续播不中断)
            self.mu_play_idx = next((i for i, f in enumerate(self.mu_files)
                                     if f["path"] == keep), self.mu_play_idx)
        if not 0 <= self.mu_sel < len(self.mu_files):
            self.mu_sel = 0

    def mu_wall_pos(self):
        """走秒位置 = -ss 起点 + 出声后 monotonic 差 (电台/油管同款墙钟).
        AirPlay 起播期 (RAOP-UP 未到, ~15s) 冻结在续播位 — 歌词/进度条/
        假EOF 判据都以它为准, 否则导入+握手期歌词空走 15s (用户报不同步)."""
        if not self.mu_playing:
            return 0.0
        if self.mu_paused:
            return self.mu_pos0
        if self.mu_aud_up is None:
            return self.mu_pos0
        pos = self.mu_pos0 + max(0.0, time.monotonic() - self.mu_aud_up)
        return min(pos, self.mu_dur) if self.mu_dur else pos

    def mu_play(self, idx, pos=0.0):
        """起播/续播. 电台先让路 (音频焦点)."""
        if not 0 <= idx < len(self.mu_files):
            return
        self.mu_sess += 1                     # 会话号 (锚点线程守卫)
        self.mu_aud_up = None                 # 出声时刻 (VU 首响即置)
        if self.playing_key:
            self.radio_stop()
        if self.tv_playing:                   # 音频焦点: 电视让路
            self.tv_stop()
        if self.mu_proc:
            self.mu_kill()
        f = self.mu_files[idx]
        target = f.get("url") or f.get("path")     # 本地路径 | QQ/网易流 URL
        if not target:
            self.osd = ("先按 Enter 解析这首", time.monotonic() + 1.8)
            return                                  # 未解析在线曲目: 不起播
        dur = f.get("dur") or 0
        if not dur and target and not target.startswith("http"):
            dur = mu_probe_dur(target)        # 起播前探: 无 ffplay 抢 CPU
            if dur > 0:
                f["dur"] = dur                # 记回条目: 之后播放/续播/seek/
                                              # 切输出不再在 UI 线程跑 ffprobe
        if f.get("preview"):
            dur = min(dur, 30) or 30          # TRY 试听: 进度条按 30s 计
        if self.mu_out >= 1:
            raop = self._raop_avail()
            dev = raop[min(self.mu_out - 1, len(raop) - 1)] \
                if raop else None
            if dev and self._mu_play_raop(idx, f, target, pos, dur, dev):
                return
            self.mu_out = 0                   # 无设备/起播失败 → 回落本机
            if dev is None:
                self.osd = ("无可用 AirPlay 音箱, 已切本机",
                            time.monotonic() + 2.0)
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-vn"]
        if target.startswith("http"):
            cmd += ["-infbuf", "-reconnect", "1", "-reconnect_streamed", "1",
                    "-reconnect_delay_max", "5"]   # 电台同款断流自愈
            cmd += _ff_slow_net_flags()            # 热点/慢网 15s 超时+网络错重连
        if pos > 0.5:
            cmd += ["-ss", "%.1f" % pos]
        cmd += ["-i", target]
        try:
            self.mu_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL,
                                            env=_AUDIO_ENV, **_FFPLAY_KW)
        except Exception:
            self.osd = ("播放失败", time.monotonic() + 1.2)
            return
        self.mu_ff = None                     # 本机路径 (锚点交给 VU 首响)
        self.mu_proc_at = time.monotonic()    # 出声锚点交给 VU 首响
        self.mu_is_raop = False               # 连接超时看门狗只管 AirPlay 会话
        self.mu_raop_t0 = None
        self.lvl.start_parec()                # pulse monitor 采电平
        self._mu_common(idx, f, pos, dur)

    def _mu_play_raop(self, idx, f, target, pos, dur, dev):
        """音乐 AirPlay 播放 (电台链同款): ffmpeg -re 节流 (有限文件必须,
        否则整曲秒读进管道) → s16le PCM → _ATVR_PLAY 消费端 (PcmSource 大端
        直通 + RAOP 元数据带歌名/歌手 + 音量 FIFO 同会话直达)。
        成功置 mu_proc(产端)/mu_ff(消费端) 返回 True; 失败 False (回落本机)."""
        vufifo = "/tmp/oneapp_muvu_%d.fifo" % int(time.monotonic() * 1000)
        self._vol_fifo_close()                # 上一会话残留 (异常退出兜底)
        try:
            os.unlink(vufifo)
        except Exception:
            pass
        os.mkfifo(vufifo, 0o600)
        volfifo = "/tmp/oneapp_muvol_%d.fifo" % int(time.monotonic() * 1000)
        try:
            os.unlink(volfifo)
        except Exception:
            pass
        os.mkfifo(volfifo, 0o600)
        self.vol_fifo = volfifo
        self.vol_w = os.open(volfifo, os.O_RDWR | os.O_NONBLOCK)
        apfh = None
        try:                                  # stderr 落盘: 无声/失败可回查
            aplog = "/tmp/oneapp_mu_ap.log"
            if os.path.exists(aplog) and os.path.getsize(aplog) > 200 * 1024:
                os.truncate(aplog, 0)
            apfh = open(aplog, "ab")
            apfh.write(("\n==== %s idx=%d pos=%.1f dev=%s %s ====\n" % (
                time.strftime("%m-%d %H:%M:%S"),
                idx, pos, dev.get("name"), dev["ip"])).encode())
        except Exception:
            apfh = None
        try:
            cons = subprocess.Popen(
                [sys.executable, "-c", _ATVR_PLAY, dev["ip"],
                 str(dev.get("port", 7000)), dev.get("id", ""),
                 (f.get("title") or "")[:120],
                 (f.get("artist") or "")[:120], volfifo],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=apfh or subprocess.DEVNULL, env=_AUDIO_ENV,
                preexec_fn=_FFPLAY_KW.get("preexec_fn"), close_fds=True)
        except Exception:
            self._vol_fifo_close()
            self._raop_cleanup(vufifo, apfh)
            self.osd = ("AirPlay 启动失败 (pip install pyatv==0.18.0)",
                        time.monotonic() + 2.0)
            return False
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-vn", "-re"]
        # -y: FIFO 路径已存在时 ffmpeg 默认交互询问后退出 (无声坑); -re:
        # 有限文件必须按原速读 (家庭版实证: 缺失=响几声即断, 管道须时钟节流)
        if pos > 0.5:
            cmd += ["-ss", "%.1f" % pos]
        if target.startswith("http"):
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1",
                    "-reconnect_delay_max", "5"] + _ff_slow_net_flags()
        cmd += ["-i", target, "-f", "s16le", "-ar", "44100", "-ac", "2",
                "-", "-f", "wav", vufifo]
        try:
            self.mu_proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=cons.stdin,
                stderr=apfh or subprocess.DEVNULL, env=_AUDIO_ENV,
                preexec_fn=_FFPLAY_KW.get("preexec_fn"), close_fds=True)
        except Exception:
            self._radio_kill(cons)
            self._vol_fifo_close()
            self._raop_cleanup(vufifo, apfh)
            self.osd = ("AirPlay 解码启动失败", time.monotonic() + 1.5)
            return False
        try:
            cons.stdin.close()                # 父端 stdin 副本即关 — ffmpeg
        except Exception:                     # 死=真 EOF, 消费端不再无限等流
            pass
        if apfh:
            try:
                apfh.close()                  # 子进程持有副本, 父端即关
            except Exception:
                pass
        self.mu_ff = cons
        sess = self.mu_sess
        self.mu_is_raop = True                # 连接超时看门狗生效 (16s 无声→回本机)
        self.mu_raop_t0 = time.monotonic()
        threading.Thread(target=self._mu_raop_watch, args=(cons, sess),
                         daemon=True).start()
        self.lvl.start_fifo(vufifo)           # 读端 NONBLOCK 开, 不与写端互等
        self._mu_common(idx, f, pos, dur)
        return True

    def _mu_raop_watch(self, proc, sess):
        """消费端 stdout: RAOP-UP → 出声锚点 (+1.2s: stream 启动+RTSP setup
        实测 ~1-1.5s, mu_wall_pos 冻结到锚点过); RAOPVOL → 采纳会话音量."""
        try:
            for line in proc.stdout:
                line = line.decode(errors="replace").strip()
                if line.startswith("RAOP-UP"):
                    if self.mu_sess == sess and self.mu_aud_up is None:
                        self.mu_aud_up = time.monotonic() + 1.2
                elif line.startswith("RAOPVOL "):
                    try:
                        self.raop_vol = max(0,
                            min(100, int(float(line.split()[1]))))
                    except Exception:
                        pass
        except Exception:
            pass

    def _mu_common(self, idx, f, pos, dur=0.0):
        """起播状态落位 + 封面/歌词懒取."""
        self.mu_playing = f
        self.mu_play_idx = idx
        self.mu_advance = True                # 自然播完 → 连播下一首
        self.mu_paused = False
        self.mu_pos0 = pos
        self.mu_t0 = time.monotonic()
        self.mu_dur = dur
        self.mu_view = "play"
        self.mu_retries = 3                   # 每曲重连额度 (源流断/假EOF)
        self.mu_retry_at = 0.0
        self.mu_session_save()
        if self.mu_pic_path != (f.get("cover") or f.get("path")):
            # 封面: 本地=内嵌图懒取; QQ/网易=CDN 外链 net.bin 直连 (换曲才取)
            self.mu_pic_path = f.get("cover") or f.get("path")
            self.mu_pic = None
            if f.get("cover"):
                self.mu_pic_seq += 1
                self.net.bin(None, f["cover"], "mupic:%d" % self.mu_pic_seq,
                             1800)
            else:
                # 内嵌图: 线程里只解字节, Surface 转换留主线程 (SDL 非线程安全)
                self.mu_pic_raw = mu_extract_cover(f["path"])
        key = (f.get("title"), f.get("artist"), int(dur or 0))
        if key != self.mu_lyr_key:            # 换曲 → 清旧词 + 拉新词
            self.mu_lyr_key = key
            self.mu_lyr = []
            if f.get("title"):
                # 直连取词 (LRCLIB + 网易兜底, musrc.lrc_fetch); tag 按曲名
                # 区分 (Net 按 tag 缓存, 同 tag 会压掉下一首)
                self.net.job(
                    "mulyr:" + (f.get("title") or "")[:24],
                    lambda t=f.get("title"), a=f.get("artist") or "", d=int(dur or 0):
                    musrc.lrc_fetch(t, a, d), 1800)

    # ── 音乐 M2: QQ 直连 (扫码绑定 / 搜索 / 我喜欢 / 播放解析) ──────────────
    def _mu_enter_source(self, sid):
        """切源: 保存当前源队列, 载入目标源队列 (每源独立播放列表); 正在
        播放且换源 → 停止 (换源即换内容)."""
        if sid == self.mu_src:
            return
        if self.mu_view in ("list", "search"):
            self.mu_lists[self.mu_src] = self.mu_files
        if self.mu_proc or self.mu_playing:
            self.mu_stop()
        self.mu_src = sid
        self.mu_files = self.mu_lists.get(sid, [])

    def _mu_open_qq(self):
        """进 QQ 源: 未绑定 → 扫码页; 已绑定 → 搜索页 (后台续期 musickey)."""
        if not musrc.qq_ok():
            self.osd = ("QQ: 需装 qqmusic-api (HANDOFF 部署节)",
                        time.monotonic() + 3.0)
            return
        self._mu_enter_source("qq")
        if musrc.qq_bound():
            self._mu_open_search()
            threading.Thread(target=musrc.qq_refresh_if_needed,
                             daemon=True).start()
        else:
            self.mu_view = "qr"
            self.mu_qr_kind = "qq"
            self.mu_qr = musrc.QQLogin()
            self.mu_qr.start()

    def _mu_open_ne(self):
        """进网易云源: 未绑定 → 扫码页 (s 免登录跳过); 已绑定 → 搜索页.
        2026-11-14 后端游客态修复, 绑定闭环实测可拿 MUSIC_U."""
        if not musrc.ne_ok():
            self.osd = ("网易云: 需 cryptography (pip3 install cryptography)",
                        time.monotonic() + 3.0)
            return
        self._mu_enter_source("ne")
        if musrc.ne_bound():
            self._mu_open_search()
        else:
            self.mu_view = "qr"
            self.mu_qr_kind = "ne"
            self.mu_qr = musrc.NeLogin()
            self.mu_qr.start()

    def _mu_open_search(self):
        self.mu_view = "search"
        self.mu_sel = 0
        self.mu_q = ""
        self.mu_input = False
        self.mu_files = self.mu_lists.get(self.mu_src, [])   # 恢复该源队列

    def _mu_open_fav(self):
        self.mu_view = "fav"
        self.mu_sel = 0
        self.mu_files = []
        if self.mu_busy:
            return
        self.mu_busy = True

        def _w():
            try:
                res = (musrc.ne_favorites() if self.mu_src == "ne"
                       else musrc.qq_favorites())
                if not res:
                    self.osd = ("我喜欢为空 (未绑定/无收藏)", time.monotonic() + 2)
            except Exception as e:
                res = []
                self.osd = ("收藏拉取失败 %s" % str(e)[:16],
                            time.monotonic() + 2)
            self.mu_files = res
            self.mu_busy = False
        threading.Thread(target=_w, daemon=True).start()

    def _mu_search_start(self):
        """n 输入 Enter → 后台搜索 (凭据; QQ 风控拦匿名, 未绑定进不来这里)."""
        q = self.mu_q.strip()
        if not q or self.mu_busy:
            return
        self.mu_busy = True
        self.mu_files = []
        self.mu_sel = 0

        def _w():
            try:
                res = (musrc.ne_search(q) if self.mu_src == "ne"
                       else musrc.qq_search(q))
            except Exception as e:
                res = []
                self.osd = ("搜索失败: %s" % str(e)[:18],
                            time.monotonic() + 2.5)
            self.mu_files = res
            self.mu_lists[self.mu_src] = res  # 搜索结果即该源队列
            self.mu_busy = False
        threading.Thread(target=_w, daemon=True).start()

    def _mu_play_item(self, idx=None, pos=0.0):
        """列表条目 → 播放. QQ/网易曲目先线程解析 URL (1-2s), 成功回填再起播
        (pos>0 = 恢复态续播); 解析期间用户换页/换选 → 丢弃 (item 身份校验)."""
        if self.mu_busy or not self.mu_files:
            return
        if idx is None:
            idx = self.mu_sel
        item = self.mu_files[idx]
        if item.get("url") or item.get("path"):
            self.mu_play(idx, pos)
            return
        if not item.get("id"):                # 本地条目直接播
            self.mu_play(idx, pos)
            return
        self.mu_busy = True
        self.osd = ("解析中…", time.monotonic() + 2.5)

        def _w():
            url = preview = None
            try:
                if item.get("src") == "ne":
                    url, preview = musrc.ne_resolve_url(item)
                else:
                    url, preview = musrc.qq_resolve_url(item)
            except Exception as e:
                self.osd = ("解析失败 %s" % str(e)[:16],
                            time.monotonic() + 2)
            self.mu_busy = False
            if not url:
                self.osd = ("解析不到链接 (风控/下架), 稍后再试",
                            time.monotonic() + 2.5)
                return
            if self.mu_files[idx] is not item:
                return                        # 列表已变 (换页/重搜)
            item["url"] = url
            item["preview"] = preview
            if preview:
                self.osd = ("非 VIP → 30s 试听", time.monotonic() + 2.5)
            self.mu_play(idx, pos)
        threading.Thread(target=_w, daemon=True).start()

    def _mu_qr_poll(self):
        """扫码页状态机 (poll_tool 秒级): ok → 进搜索; fail → 弹错回源页."""
        if self.mu_view != "qr":
            return
        st = self.mu_qr.state
        if st == "ok":
            self.mu_qr.stop()
            self.osd = ("已绑定 ✓", time.monotonic() + 2.0)
            self._mu_open_search()
        elif st == "fail":
            err = self.mu_qr.err
            self.mu_qr.stop()
            self.osd = ("扫码失败: %s" % err[:20], time.monotonic() + 3.0)
            self.mu_view = "src"

    def mu_kill(self):
        """只杀子进程, 不清状态 (暂停/seek/续播走这里). 产端走 _radio_kill
        (AirPlay 时是 ffmpeg 双输出 — SIGTERM futex 僵死须补刀 SIGKILL,
        765d0d2 实证); 消费端长宽限: SIGTERM → 优雅取消 stream → finally 真
        RTSP TEARDOWN (暴力拆连让音箱进冷却拒连 — 实测退净须 ~2s)."""
        self.lvl.stop()                       # 电平源随播放进程一起断
        self._radio_kill(self.mu_proc)        # ffplay 本机 / ffmpeg AirPlay 产端
        self.mu_proc = None
        self._radio_kill(self.mu_ff, grace=2.5)   # AirPlay 消费端 (本机=None)
        self.mu_ff = None
        self._vol_fifo_close()                # 音量命令通道随会话关
        self.mu_prod_dead_at = None
        self.mu_raop_t0 = None                # 连接超时看门狗随会话重置
        self.mu_is_raop = False

    def mu_stop(self):
        """x 键/切换曲目/退出连播末尾: 全停 (状态清空, 回列表)."""
        self.mu_kill()
        self.mu_playing = None
        self.mu_play_idx = -1
        self.mu_advance = False
        self.mu_paused = False

    def mu_session_save(self):
        """上次播放会话落盘 (跨重启恢复: 队列元数据+曲目位+暂停态; 在线
        url 不存 — 时效短, 恢复后走解析链重取)."""
        if not self.mu_playing:
            return
        files = [{k: v for k, v in it.items() if k != "url"}
                 for it in self.mu_files[:60]]
        netmod.save_json_file(MU_LAST_CACHE, {
            "src": self.mu_src, "files": files,
            "idx": self.mu_play_idx,
            "pos": (self.mu_pos0 if self.mu_paused
                    else self.mu_wall_pos()),
            "paused": bool(self.mu_paused),
            "ts": int(time.time())})

    def mu_session_restore(self):
        """进音乐 app 空态时恢复上次会话: 播放页显示上次曲目 (暂停在位),
        4/Enter 续播 (在线曲走解析链)."""
        s = netmod.load_json_file(MU_LAST_CACHE, {})
        try:
            files = s.get("files") or []
            idx = int(s.get("idx", -1))
            if not files or not 0 <= idx < len(files):
                return
            self.mu_src = s.get("src") if s.get("src") in self.mu_lists \
                else "local"
            self.mu_lists[self.mu_src] = files   # 选源页队列同恢复
            self.mu_files = files
            self.mu_play_idx = idx
            self.mu_playing = files[idx]
            self.mu_pos0 = max(0.0, float(s.get("pos") or 0.0))
            self.mu_dur = float(files[idx].get("dur") or 0.0)
            self.mu_paused = True                # 显示暂停态, 4=续播
            self.mu_advance = False              # 静止恢复态, 播放时再置
        except Exception:
            pass

    def mu_pause_toggle(self):
        """p 键: 暂停 = 记位杀进程; 恢复 = -ss 续播 (ffplay 无暂停, 本地可重启)."""
        if not self.mu_playing:
            return
        if self.mu_paused:
            f = self.mu_playing or {}
            if not f.get("url") and not f.get("path") and f.get("id"):
                self._mu_play_item(self.mu_play_idx, self.mu_pos0)
                return                        # 在线曲恢复态 → 先解析再 -ss 续播
            self.mu_play(self.mu_play_idx, self.mu_pos0)
        elif self.mu_proc:
            self.mu_pos0 = self.mu_wall_pos()
            self.mu_kill()
            self.mu_paused = True
            self.mu_session_save()

    def mu_seek(self, d):
        """←/→ ±10s: 重启进程拼 -ss (油管墙钟同款)."""
        if not self.mu_playing or self.mu_paused:
            return
        pos = max(0.0, self.mu_wall_pos() + d)
        if self.mu_dur:
            pos = min(pos, max(0.0, self.mu_dur - 2))
        self.mu_kill()
        self.mu_play(self.mu_play_idx, pos)

    def mu_out_name(self):
        """音乐输出名: 本机 / AirPlay·<设备名> (电台共用设备表, 选择独立)."""
        raop = self._raop_avail()
        if self.mu_out == 0 or not raop:
            return "本机"
        d = raop[min(self.mu_out - 1, len(raop) - 1)]
        return "AirPlay·%s" % d.get("name", "?")

    def _mu_out_save(self):
        """输出选择持久化 (按设备 id; 重启恢复上次输出)."""
        try:
            raop = self._raop_avail()
            did = (raop[self.mu_out - 1].get("id")
                   if 1 <= self.mu_out <= len(raop) else "")
            netmod.save_json_file(MU_OUT_CACHE, {"id": did})
        except Exception:
            pass

    def mu_out_cycle(self):
        """5/7 键: 本机 → 各 AirPlay 设备 → 本机 循环 (只循环本网段可用
        音箱); 播放中换输出 = 停旧进程 + 同曲同位续播 (暂停中只切路由,
        恢复时才起新进程). 外地/换网时缓存音箱不可达 → 归位本机不中断."""
        raop = self._raop_avail()
        if not raop:
            if self.mu_out:
                self.mu_out = 0               # 死路选择归位本机
                self._mu_out_save()
            if self.raop:                     # 有缓存但不在本网段 (外地)
                self.osd = ("无可用 AirPlay 音箱 (本网段)",
                            time.monotonic() + 2.0)
                if (self.mu_playing and not self.mu_paused
                        and self.mu_proc is not None
                        and self.mu_ff is not None):
                    pos = self.mu_wall_pos()  # AirPlay 僵会话 → 回本机续播
                    self.mu_kill()
                    self.mu_play(self.mu_play_idx, pos)
                return
            self._raop_scan_lazy()            # 真没扫到过: 触发一次后台补扫
            self.osd = ("无 AirPlay 设备 (扫描中…, 稍后再按)",
                        time.monotonic() + 2.0)
            return
        self.mu_out = (self.mu_out + 1) % (len(raop) + 1)
        self._mu_out_save()
        if self.mu_playing is None:
            self.osd = ("输出: %s" % self.mu_out_name(),
                        time.monotonic() + 1.5)
            return
        if self.mu_paused or self.mu_proc is None:
            self.osd = ("输出: %s (恢复时生效)" % self.mu_out_name(),
                        time.monotonic() + 1.5)
            return                           # 暂停/无进程: 状态原样
        if self.mu_out == 0:                 # 切回本机: 秒切, 别再说"连接中"
            self.osd = ("输出: 本机", time.monotonic() + 1.5)
        else:
            self.osd = ("%s 连接中… (~15s)" % self.mu_out_name(),
                        time.monotonic() + 3.0)
        pos = self.mu_wall_pos()
        self.mu_kill()
        self.mu_play(self.mu_play_idx, pos)

    def mu_tick(self):
        """连播/出错检测 (poll 每秒 + 播放页每帧都调). 判据进程退出 = 流结束:
        rc=0 自然播完 → 连播下一首; 提前退出 (源流断/假EOF) → 额度内同位续流."""
        if self.mu_busy and time.monotonic() - self.mu_busy_at > 30:
            self.mu_busy = False              # 取数线程卡死兜底 (30s)
            self.osd = ("取数超时, 请重试", time.monotonic() + 1.5)
        if self.mu_playing and not self.mu_paused \
                and time.monotonic() - self.mu_saved_at > 15.0:
            self.mu_saved_at = time.monotonic()
            self.mu_session_save()            # 播放位 15s 节流落盘 (断电保位)
        if not self.mu_advance:
            return
        if self.mu_proc is None:
            if self.mu_retry_at:
                if time.monotonic() >= self.mu_retry_at:
                    self.mu_retry_at = 0.0
                    r = self.mu_retries           # 重试重生不刷新额度 (防无限)
                    self.mu_play(self.mu_play_idx, self.mu_pos0)
                    self.mu_retries = r
            return
        # AirPlay 连接超时看门狗: RAOP-UP 16s 未到 (音箱不可达/掉线/外地死
        # 路) → 消费端每 3s 重试永远"活着", 产端活着喂管道 = 僵会话永不出
        # 声也不报错 (2026-11-19 用户外地实证) → 回本机同位续播. 每次起播
        # 重置 t0, 家里正常连接 ~10s 内不会误伤.
        if (self.mu_is_raop and self.mu_aud_up is None and self.mu_raop_t0
                and time.monotonic() - self.mu_raop_t0 > 16.0):
            self.mu_is_raop = False
            self.mu_raop_t0 = None
            pos = self.mu_pos0                    # 墙钟冻结着, 直接用起点位
            self.mu_out = 0
            self._mu_out_save()
            self.osd = ("音箱连不上, 已切本机", time.monotonic() + 2.5)
            self.mu_kill()
            self.mu_play(self.mu_play_idx, pos)
            return
        is_raop = self.mu_ff is not None
        if is_raop and self.mu_proc.poll() is not None \
                and self.mu_ff.poll() is None:
            # 产端先死而消费端未退: 正常收尾时消费端应 ~1s 内随 EOF 退;
            # >3s = 流断裂挂死 (家庭版实证: ffmpeg 死成僵尸, 消费端无限等
            # EOF, 判据永不触发) → 杀掉让下一拍走中断/续流
            if self.mu_prod_dead_at is None:
                self.mu_prod_dead_at = time.monotonic()
                return
            if time.monotonic() - self.mu_prod_dead_at > 3.0:
                self.mu_prod_dead_at = None
                self.osd = ("AirPlay 推流中断, 续流…", time.monotonic() + 1.5)
                try:
                    self.mu_ff.kill()
                except Exception:
                    pass
            return
        self.mu_prod_dead_at = None
        rc = (self.mu_ff if is_raop else self.mu_proc).poll()
        if rc is None:
            if is_raop:
                self.mu_proc.poll()           # 产端收尸 (防 defunct 堆积)
            return
        wall = self.mu_wall_pos()             # 死亡时刻的真实进度 (kill 前取)
        life = (time.monotonic() - self.mu_proc_at
                if self.mu_proc_at else 0.0)
        self.mu_kill()
        dur = self.mu_dur
        # 本机声音未锚定 (静音曲头/无监视器环境): 墙钟冻结在 0, 假EOF判据会
        # 误杀 → 退化为进程寿命判据: 寿命≥85% 时长=真播完, 否则=早死走重试
        if not is_raop and self.mu_aud_up is None:
            done = dur > 0 and life >= dur * 0.85
        else:
            done = dur == 0 or wall >= dur * 0.85
        if rc != 0 and done:
            done = True                       # 尾段崩 (差几秒) → 按播完算
        if not done and self.mu_retries > 0:
            # 提前收工统一重连: 中途源流断 (QQ URL 断流实测) / AirPlay 会话
            # 掉 — 同位续播不假推进 (消费端 connect 自带拒连重试)
            self.mu_retries -= 1
            self.mu_pos0 = min(wall, dur) if dur else wall
            self.mu_retry_at = time.monotonic() + \
                (2.0 if time.monotonic() - self.mu_t0 >= 15.0 else 3.0)
            self.osd = ("AirPlay 续流…" if is_raop else "重连…",
                        time.monotonic() + 1.5)
            return
        if rc != 0 or not done:
            self.mu_playing = None
            self.mu_play_idx = -1
            self.mu_advance = False
            self.osd = ("AirPlay 中断/失败" if is_raop
                        else "播放失败 (格式不支持?)", time.monotonic() + 1.5)
            self.mu_view = "list"
            return
        nxt = self.mu_play_idx + 1
        if nxt >= len(self.mu_files):
            self.mu_stop()
            self.mu_view = "src"              # 列表播完回源选择页
            return
        f2 = self.mu_files[nxt]
        if not (f2.get("url") or f2.get("path")) and f2.get("id"):
            self.mu_sel = nxt
            self._mu_play_item()              # 连播到未解析曲 → 走解析流
            return
        self.mu_play(nxt)

    def mu_key(self, k, ch):
        if self.mu_view == "src":
            n = len(MUSIC_SRCS)
            if k in (pygame.K_UP, pygame.K_DOWN):
                d = -1 if k == pygame.K_UP else 1
                self.mu_sel = (self.mu_sel + d) % n   # 源光标借用 mu_sel
            elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                sid = MUSIC_SRCS[self.mu_sel][0]
                if sid == "local":
                    self.mu_view = "list"
                    self.mu_sel = 0              # src 光标 ≠ 曲目光标, 进列表归位
                    self.mu_rescan()
                elif sid == "qq":
                    self._mu_open_qq()
                else:
                    self._mu_open_ne()
            elif k == pygame.K_7:
                self.mu_out_cycle()              # 7=输出 (源选择页也可切)
            return True
        if self.mu_view == "qr":
            if k in (pygame.K_q, pygame.K_BACKSPACE):
                self.mu_qr.stop()          # Esc 在全局层=退工具 (先掐线程)
                self.mu_view = "src"
            elif self.mu_qr_kind == "ne" and k == pygame.K_s:
                self.mu_qr.stop()
                self._mu_open_search()     # 网易免登录跳过 (免费曲库直搜)
            return True
        if self.mu_view == "search":
            if self.mu_input:
                if k == pygame.K_SLASH:       # 中/英切换 (IME)
                    self.ime_mode = not self.ime_mode
                    self.ime_clear()
                    return True
                if self.ime_mode:             # 拼音 IME
                    if not self.ime_buf:
                        if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                            self.ime_clear()
                            self.mu_input = False
                            self._mu_search_start()
                            return True
                    act = self.ime_key(k, ch)
                    if act is None:
                        return True
                    tag, val = act
                    if tag == "del":
                        self.mu_q = self.mu_q[:-1]
                    elif tag == "submit":
                        self.ime_clear()
                        self.mu_input = False
                        self._mu_search_start()
                    else:                     # char/word
                        self.mu_q = (self.mu_q + val)[:40]
                    return True
                # 英文直输 (原行为)
                if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self.mu_input = False
                    self._mu_search_start()
                elif k == pygame.K_BACKSPACE:
                    self.mu_q = self.mu_q[:-1]
                elif ch and 0x20 <= ord(ch) < 0x7F:
                    if len(self.mu_q) < 40:
                        self.mu_q += ch
                return True
            if k in (pygame.K_n, pygame.K_e):
                self.ime_clear()
                self.mu_input = True             # n=搜索 (想法页同款)
            elif k in (pygame.K_UP, pygame.K_DOWN):
                d = -1 if k == pygame.K_UP else 1
                self.mu_sel = max(0, min(len(self.mu_files) - 1,
                                         self.mu_sel + d))
            elif k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_4):
                self._mu_play_item()             # 4=播放 (对齐实体键 4)
            elif k == pygame.K_5:
                self._mu_open_fav()              # 5=收藏 (对齐实体键 5;
                                                 #  2026-11-14 网易绑定闭环拆占位门)
            elif k == pygame.K_6:
                self.mu_view = "src"             # 6=返回源选择 (实体键 6)
            elif k in (pygame.K_0, pygame.K_9):
                self.mu_view = "src"             # 9=选源 (2026-11-16 用户定版;
                                                 #   0 兼容)
            elif k == pygame.K_8:
                self.mu_stop()                   # 8=停止 (实体键 8)
            return True
        if self.mu_view == "fav":
            if k in (pygame.K_UP, pygame.K_DOWN):
                d = -1 if k == pygame.K_UP else 1
                self.mu_sel = max(0, min(len(self.mu_files) - 1,
                                         self.mu_sel + d))
            elif k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_4):
                self._mu_play_item()             # 4=播放 (对齐实体键 4)
            elif k == pygame.K_7:
                self.mu_out_cycle()              # 7=输出
            elif k == pygame.K_6:
                self._mu_open_search()           # 6=回搜索 (实体键 6)
            elif k in (pygame.K_0, pygame.K_9):
                self.mu_view = "src"             # 9=选源 (2026-11-16 用户定版;
                                                 #   0 兼容)
            elif k == pygame.K_8:
                self.mu_stop()                   # 8=停止 (实体键 8)
            elif k == pygame.K_7:
                self.mu_out_cycle()              # 7=输出
            elif k == pygame.K_r and not self.mu_busy:
                self._mu_open_fav()              # r=重拉收藏
            return True
        if self.mu_view == "list":
            if k in (pygame.K_UP, pygame.K_DOWN):
                d = -1 if k == pygame.K_UP else 1
                self.mu_sel = max(0, min(len(self.mu_files) - 1,
                                         self.mu_sel + d))
            elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if self.mu_files:
                    self.mu_play(self.mu_sel)
            elif k == pygame.K_7:
                self.mu_out_cycle()              # 7=输出
            elif k == pygame.K_r:
                self.mu_scan_at = 0.0          # r 强制重扫
                self.mu_rescan()
                self.osd = ("已重扫 %d 首" % len(self.mu_files),
                            time.monotonic() + 1.2)
            elif k in (pygame.K_0, pygame.K_9):
                self.mu_view = "src"           # 9=选源 (2026-11-16 用户定版;
                                               #   0 兼容)
            return True
        # play 视图: 4/Enter/P 播停, 5 输出, 7 停止, 8 选源 (2026-11-16 定版)
        if k in (pygame.K_4, pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_p):
            self.mu_pause_toggle()        # 4=暂停 (2026-11-14 用户定版, 对齐
                                          #   实体键 4; P 保留)
        elif k == pygame.K_5:
            self.mu_out_cycle()           # 5=输出 (2026-11-16 用户定版)
        elif k == pygame.K_7:
            self.mu_stop()
            self.mu_view = "src"          # 7=停止 (2026-11-16 用户定版)
        elif k in (pygame.K_0, pygame.K_8):
            self.mu_view = "src"          # 8=选源 (2026-11-16 用户定版; 0 兼容)
                                          #   播放后台继续
        elif k == pygame.K_LEFT:
            self.mu_seek(-10)
        elif k == pygame.K_RIGHT:
            self.mu_seek(10)
        elif k in (pygame.K_UP, pygame.K_DOWN):
            # 播放页 ↑↓ = 切上一/下一首; 下一首未解析 → 走解析流 (真机实证:
            # 直接 mu_play 会在 target=None startswith 崩整 app)
            d = -1 if k == pygame.K_UP else 1
            nxt = self.mu_play_idx + d
            if 0 <= nxt < len(self.mu_files):
                f2 = self.mu_files[nxt]
                if f2.get("url") or f2.get("path") or not f2.get("id"):
                    self.mu_play(nxt)
                else:
                    self.mu_sel = nxt
                    self._mu_play_item()
        return True

    def vu_tick(self, now, b, m, t):
        """car-dash vuTick 移植: 钟形频段权重 / 快升慢降 / 峰值帽.
        三带电平由调用方给 (电台/音乐=self.lvl.lvl, MusicLvl),
        门控必须低于主循环帧距 (10fps→100ms), 否则 tick 被饿死."""
        if now - self._vu_at < 0.09:
            return
        self._vu_at = now
        for i in range(12):
            w = i / 11
            wb = 1 - 2 * w if w < 0.5 else 0.0
            wt = 2 * w - 1 if w > 0.5 else 0.0
            wm = 1 - wb - wt
            target = 2 + (b * wb + m * wm + t * wt) * 0.26
            if target > 28:
                target = 28
            k = 0.45 if target > self.vu[i] else 0.10 + (i % 5) * 0.04
            self.vu[i] += (target - self.vu[i]) * k
            h = max(2, min(28, int(round(self.vu[i]))))
            pk = self.vu_pk[i]
            pk = h if h >= pk else pk * 0.92
            self.vu_pk[i] = max(pk, h)

    def submit_idea(self):
        txt = self.buf.strip()
        if not txt:
            self.in_input = False
            return
        self.ideas.setdefault("texts", []).append({
            "id": "%d-%s" % (int(time.time() * 1000),
                             "".join(random.choice("0123456789abcdef") for _ in range(6))),
            "cid": _cloud_id(),               # 云端 id (纯 hex, 幂等上传键)
            "owner": self.cfg.get("cloud_user") or "",   # 账户归属 (换号隔离)
            "text": txt, "createdAt": int(time.time() * 1000),
        })
        netmod.save_json_file(IDEAS_FILE, self.ideas)
        self.buf = ""
        self.in_input = False
        self.cloud_sync_trigger()             # 后台上云 (本地已即时可见)
        self.osd = ("已保存", time.monotonic() + 1.2)

    def delete_selected(self):
        items = self.idea_items()
        if not (0 <= self.sel < len(items)):
            return
        it = items[self.sel]
        if it.get("cid"):
            self.ideas.setdefault("cdel", []).append(it["cid"])
            self.cloud_sync_trigger()         # 云端联动删除 (cdel 队列)
        if it["kind"] == "voice":
            self.ideas["voices"] = [x for x in self.ideas.get("voices", [])
                                    if x.get("id") != it["id"]]
            try:
                if it.get("file") and os.path.exists(it["file"]):
                    os.remove(it["file"])
            except Exception:
                pass
            if self.playing_voice and self.playing_voice.get("id") == it["id"]:
                self.stop_player()
                self.playing_voice = None
        else:
            self.ideas["texts"] = [x for x in self.ideas.get("texts", [])
                                   if x.get("id") != it["id"]]
        netmod.save_json_file(IDEAS_FILE, self.ideas)
        self.sel = max(0, self.sel - 1)
        self.osd = ("已删除", time.monotonic() + 1.2)

    # ── 想法云同步 (noteserver ECS: 双写上传 + tombstone 双向删除) ──
    def _cloud_cfg(self):
        return ((self.cfg.get("cloud_url") or "").strip().rstrip("/"),
                (self.cfg.get("cloud_tok") or "").strip())

    def ensure_cids(self):
        """存量想法补 cid/owner (首次上云前补齐, UI 线程内落盘).
        owner = 当前登录账户 — 换账户后列表只显示本账户条目."""
        user = self.cfg.get("cloud_user") or ""
        ch = False
        for key in ("texts", "voices"):
            for it in self.ideas.get(key, []):
                if not it.get("cid"):
                    it["cid"] = _cloud_id()
                    ch = True
                if user and not it.get("owner") and not it.get("up"):
                    it["owner"] = user   # 只补从未上传的; up=1 保持原归属
                    ch = True
        if ch:
            netmod.save_json_file(IDEAS_FILE, self.ideas)

    def _cloud_has_pending(self):
        """有无待上传/待删条目 (节流豁免依据)."""
        for key in ("texts", "voices"):
            for it in self.ideas.get(key, []):
                if not it.get("up"):
                    return True
        return bool(self.ideas.get("cdel"))

    def cloud_sync_trigger(self):
        """后台双写: 上传 pending + 处理 cdel + tombstone 对账.
        节流只限例行对账 — 有 pending 时不节流 (否则紧随上一轮的事件
        会被窗口吞掉, 用户离开页面后无人补跑)."""
        url, tok = self._cloud_cfg()
        if not (url and tok):
            return
        if self._cloud_has_pending():
            self._cloud_trig = time.monotonic()   # pending 永不节流
        elif time.monotonic() - self._cloud_trig < 15:
            return
        else:
            self._cloud_trig = time.monotonic()
        if not self.cfg.get("cloud_user"):
            self.cloud_ping_trigger()      # 存量 token 补拉用户名
        self.ensure_cids()
        self._cloud_pend_n = (
            sum(1 for it in self.ideas.get("texts", []) if not it.get("up"))
            + sum(1 for it in self.ideas.get("voices", []) if not it.get("up"))
            + len(self.ideas.get("cdel", [])))
        self.net.invalidate(("job", "cloudsync"))
        self.net.job("cloudsync", self._cloud_sync_fn, ttl=30)

    def _cloud_sync_fn(self):
        """worker 线程: 只读快照+网络 IO, 状态变更全在 on_cloud_done.
        顺序 = tombstone 对账 → cdel → 上传 (对账最便宜且不能被
        pending 上传的持续失败阻塞)."""
        url, tok = self._cloud_cfg()
        if not (url and tok):
            return {"skip": 1}
        H = {"Authorization": "Bearer " + tok}
        d = {"up_ids": [], "gone": [], "miss": [], "del_done": [],
             "gone_tomb": [], "csync": None, "skip": 0, "err": None}
        st, body = netmod.http_raw(                # 云端删 → 本地对账
            url + "/v1/tombstones?since=%s" % self.ideas.get("csync", 0),
            headers=H)
        if st == 200:
            try:
                j = json.loads(body.decode("utf-8", "replace"))
            except ValueError:
                j = {}
            local = {}
            for key in ("texts", "voices"):
                for it in self.ideas.get(key, []):
                    if it.get("cid"):
                        local[it["cid"]] = (key, it.get("file"))
            for cid in j.get("ids") or []:
                if cid in local:
                    d["gone_tomb"].append((cid,) + local[cid])
            d["csync"] = j.get("now")
        elif st:                                   # 对账失败: 继续删/传 (不早退)
            d["err"] = "对账失败 HTTP %s" % st
        st, body = netmod.http_raw(                # 云端全量 → 本地镜像对齐
            url + "/v1/notes?limit=300", headers=H)
        if st == 200:
            try:
                j2 = json.loads(body.decode("utf-8", "replace"))
                d["cloud_list"] = j2.get("notes") or []
            except ValueError:
                d["cloud_list"] = []
        elif not d["err"]:
            d["err"] = "列表 HTTP %s" % st
        for cid in self.ideas.get("cdel", []):     # 本地删 → 云端联动删
            st, _ = netmod.http_raw(url + "/v1/notes/" + cid,
                                    method="DELETE", headers=H)
            if st in (200, 404):
                d["del_done"].append(cid)
            else:
                d["err"] = "删除失败 HTTP %s" % st
                return d
        for key in ("texts", "voices"):
            for it in self.ideas.get(key, []):
                if it.get("up") or not it.get("cid"):
                    continue
                if it.get("cid") in d["up_ids"]:   # 同 cid 去重
                    continue
                if key == "texts":
                    body = json.dumps({"id": it["cid"],
                                       "text": it.get("text", ""),
                                       "ts": (it.get("createdAt") or 0) / 1000.0}).encode()
                    st, _ = netmod.http_raw(url + "/v1/notes", method="POST",
                                            body=body,
                                            headers=dict(H, **{"Content-Type": "application/json"}))
                else:
                    try:
                        with open(it.get("file") or "", "rb") as f:
                            raw = f.read()
                    except OSError:
                        d["miss"].append(it["cid"])
                        continue
                    st, _ = netmod.http_raw(
                        url + "/v1/voice/%s?dur=%s" % (
                            it["cid"], it.get("dur") or 0),
                        method="POST", body=raw,
                        headers=dict(H, **{"Content-Type": "audio/wav"}))
                if st == 200:
                    d["up_ids"].append(it["cid"])
                elif st == 409:                    # delete-wins: 本地丢弃
                    d["gone"].append(it["cid"])
                else:
                    d["err"] = "HTTP %s" % st if st else "网络不可达"
                    return d                       # 已收集进度照常回放
        return d

    def on_cloud_done(self, d):
        """UI 线程应用同步结果: 标 up / 执行删除 / 收 cdel / 存 csync."""
        if not isinstance(d, dict) or d.get("skip"):
            return
        ups = set(d.get("up_ids") or [])
        gone = set(d.get("gone") or [])
        miss = set(d.get("miss") or [])
        tombset = {t[0] for t in (d.get("gone_tomb") or [])}
        for key in ("texts", "voices"):
            for it in self.ideas.get(key, []):
                if it.get("cid") in ups:
                    it["up"] = 1
        dels = 0
        for key in ("voices", "texts"):
            keep = []
            for it in self.ideas.get(key, []):
                cid = it.get("cid")
                if cid in gone or cid in miss or cid in tombset:
                    dels += 1
                    if key == "voices" and it.get("file"):
                        try:
                            os.remove(it["file"])
                        except OSError:
                            pass
                    continue
                keep.append(it)
            self.ideas[key] = keep
        if d.get("del_done"):
            done = set(d["del_done"])
            self.ideas["cdel"] = [c for c in self.ideas.get("cdel", [])
                                  if c not in done]
            dels += len(done)
        if d.get("csync"):
            self.ideas["csync"] = d["csync"]
        user = self.cfg.get("cloud_user") or ""
        cl = d.get("cloud_list") or []
        if cl:
            local = {}
            for key in ("texts", "voices"):
                for it in self.ideas.get(key, []):
                    if it.get("cid"):
                        local[it["cid"]] = (key, it)
            for cn in cl:
                cid = cn["id"]
                created = int((cn.get("ts") or 0) * 1000)
                if cid in local:
                    key, it = local[cid]
                    it.setdefault("owner", user)
                    if it.get("up"):
                        it["text"] = cn.get("text") or it.get("text", "")
                        if cn.get("ts"):
                            it["createdAt"] = created
                        if key == "voices":
                            it["dur"] = round(cn.get("dur")
                                              or it.get("dur", 0))
                else:                          # 云端有本地无 (他端/换号前) → 镜像入
                    base = {"id": cid, "cid": cid, "owner": user, "up": 1,
                            "text": cn.get("text") or "",
                            "createdAt": created}
                    if cn["kind"] == "voice":
                        base.update({"dur": round(cn.get("dur") or 0),
                                     "file": ""})   # 文件懒下载 (播放时)
                        self.ideas.setdefault("voices", []).append(base)
                    else:
                        self.ideas.setdefault("texts", []).append(base)
            # 本地 up=1 但云端没有的 → 已被他端删 (tombstone 兜底, 这里硬对齐)
            cloud_ids = {n["id"] for n in cl}
            for key in ("voices", "texts"):
                keep = []
                for it in self.ideas.get(key, []):
                    if it.get("owner") == user and it.get("up") \
                            and it.get("cid") not in cloud_ids:
                        if key == "voices" and it.get("file"):
                            try:
                                os.remove(it["file"])
                            except OSError:
                                pass
                        continue
                    keep.append(it)
                self.ideas[key] = keep
            self.sel = 0
        netmod.save_json_file(IDEAS_FILE, self.ideas)   # 无条件落盘 (up/删除/merge)
        if ups or dels:
            self.osd = ("云同步 +%d -%d" % (len(ups), dels),
                        time.monotonic() + 1.5)
        if self._cloud_has_pending():
            self._cloud_trig = 0.0         # 同步期间新落的事件 → 补跑一轮
            self.cloud_sync_trigger()

    def cloud_list_fetch(self):
        self.net.invalidate(("job", "cloudlist"))
        self.net.job("cloudlist", self._cloud_list_fn, ttl=30)

    def _cloud_list_fn(self):
        url, tok = self._cloud_cfg()
        if not (url and tok):
            return {"skip": 1}
        st, body = netmod.http_raw(url + "/v1/notes?limit=100", headers={
            "Authorization": "Bearer " + tok})
        if st != 200:
            return None
        j = json.loads(body.decode("utf-8", "replace"))
        return {"ok": 1, "notes": j.get("notes") or []}

    def cloud_voice_sel(self):
        self.net.invalidate(("job", "cloudvoice"))
        self.net.job("cloudvoice", self._cloud_voice_fn, ttl=30)
        self.osd = ("下载中…", time.monotonic() + 3)

    def _cloud_voice_fn(self):
        url, tok = self._cloud_cfg()
        if not (url and tok):
            return {"skip": 1}
        req = getattr(self, "_play_req", None)   # 懒下载 (本地视图缺文件)
        if req:
            cid, dur, lid = req
        else:                                    # 云端视图选中项
            it = self.cloud_notes[self.csel] \
                if 0 <= self.csel < len(self.cloud_notes) else None
            if not it or it.get("kind") != "voice":
                return {"skip": 1}
            cid, dur, lid = it["id"], it.get("dur") or 0, None
        st, body = netmod.http_raw(url + "/v1/voice/" + cid,
                                   headers={"Authorization": "Bearer " + tok})
        if st != 200:
            return None
        return {"id": cid, "dur": dur, "local_id": lid, "data": body}

    def cloud_del_sel(self):
        self.net.invalidate(("job", "clouddel"))
        self.net.job("clouddel", self._cloud_del_fn, ttl=30)

    def _cloud_del_fn(self):
        url, tok = self._cloud_cfg()
        it = self.cloud_notes[self.csel] \
            if 0 <= self.csel < len(self.cloud_notes) else None
        if not (url and tok) or not it:
            return {"skip": 1}
        st, _ = netmod.http_raw(url + "/v1/notes/" + it["id"],
                                method="DELETE",
                                headers={"Authorization": "Bearer " + tok})
        return {"id": it["id"], "st": st}

    def cloud_ping_trigger(self):
        self.net.invalidate(("job", "cloudping"))
        self.net.job("cloudping", self._cloud_ping_fn, ttl=10)

    def _cloud_ping_fn(self):
        url, tok = self._cloud_cfg()
        if not (url and tok):
            return {"skip": 1}
        st, body = netmod.http_raw(url + "/v1/ping", timeout=8,
                                   headers={"Authorization": "Bearer " + tok})
        if st == 200:
            try:
                j = json.loads(body.decode("utf-8", "replace"))
            except ValueError:
                j = {}
            return {"ok": 1, "user": j.get("user") or "?"}
        return {"ok": 0, "st": st}

    # ── 语音想法: 录音存本地 / 本地播放 ──
    def rec_toggle(self):
        """5/r: 开始或停止录音. 录完裁静音头 → voices/<id>.wav + ideas 索引."""
        if self.rec_proc:
            self.rec_stop()
            return
        if self.playing_key:                  # 音频焦点: 电台/音乐让路
            self.radio_stop()
        if self.mu_proc or self.mu_playing:
            self.mu_stop()
        try:
            if os.path.exists(self.rec_path):
                os.remove(self.rec_path)
            os.makedirs(VOICES_DIR, exist_ok=True)
            ok, bad = rec_mixer_apply()
            if not ok:
                self.osd = ("麦克风配置失败 %s" % bad[:38],
                            time.monotonic() + 3.0)
                return
            self.rec_proc = subprocess.Popen(
                ["arecord", "-D", REC_DEV, "-f", "S16_LE",
                 "-r", "16000", "-c", "1", "-d", str(REC_MAX),
                 self.rec_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=_AUDIO_ENV, **_FFPLAY_KW)
            self.rec_started = time.monotonic()
            self.osd = ("录音中… 5 停止", time.monotonic() + 2)
        except Exception:
            self.rec_proc = None
            self.osd = ("录音不可用", time.monotonic() + 1.5)

    def rec_stop(self):
        if not self.rec_proc:
            return
        try:
            self.rec_proc.terminate()
            self.rec_proc.wait(timeout=5)     # arecord 写 WAV 尾
        except Exception:
            pass
        self.rec_proc = None
        dur = int(time.monotonic() - self.rec_started)
        if dur < 1:
            self.osd = ("太短未保存", time.monotonic() + 1.2)
            return
        self.osd = ("语音处理中…", time.monotonic() + 4)
        threading.Thread(target=self._voice_save_worker, args=(dur,),
                         daemon=True).start()

    def _voice_save_worker(self, dur):
        """裁静音头 + 高通/噪声门/归一 (clean_voice) → voices/<id>.wav + 索引."""
        try:
            real = trim_silence_head(self.rec_path) or dur
            real = clean_voice(self.rec_path) or real
            vid = "%d-%s" % (int(time.time() * 1000),
                             "".join(random.choice("0123456789abcdef")
                                     for _ in range(6)))
            dst = os.path.join(VOICES_DIR, vid + ".wav")
            shutil.move(self.rec_path, dst)
            self.ideas.setdefault("voices", []).append(
                {"id": vid, "cid": _cloud_id(),
                 "owner": self.cfg.get("cloud_user") or "",
                 "file": dst, "dur": real,
                 "createdAt": int(time.time() * 1000)})
            netmod.save_json_file(IDEAS_FILE, self.ideas)
            self.cloud_sync_trigger()
            self.osd = ("语音已保存 %ds" % real, time.monotonic() + 1.5)
        except Exception:
            self.osd = ("保存失败", time.monotonic() + 1.5)

    def play_voice(self, item):
        """8/Enter (选中语音条): 播放本地 wav; 再按 = 停."""
        if self.playing_voice                 and self.playing_voice.get("id") == item.get("id"):
            self.stop_player()
            self.playing_voice = None
            return
        if self.playing_key:
            self.radio_stop()
        if self.mu_proc or self.mu_playing:
            self.mu_stop()
        self.stop_player()
        f = item.get("file") or ""
        if (not f or not os.path.exists(f)) and item.get("cid") \
                and self._cloud_cfg()[0]:
            self._play_req = (item["cid"], item.get("dur") or 0,
                              item.get("id"))
            self.net.invalidate(("job", "cloudvoice"))
            self.net.job("cloudvoice", self._cloud_voice_fn, ttl=10)
            self.osd = ("云端拉取中…", time.monotonic() + 5)
            return
        if not f or not os.path.exists(f):
            self.osd = ("音频缺失", time.monotonic() + 1.5)
            return
        try:
            # wav → paplay (毫秒级启动; ffplay 冷启动 ~5s 于 CM0, 语音短
            # 进度条走完声音才出的根因); 非 wav (webm 等云端语音) 仍 ffplay;
            # paplay 缺席回退 ffplay (慢但能响)
            if _is_pcm_wav(f):
                cmd = ["paplay", f]
            else:
                cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                       "-vn", f]
            try:
                self.player = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=_AUDIO_ENV, **_FFPLAY_KW)
            except FileNotFoundError:
                self.player = subprocess.Popen(
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                     "-vn", f],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=_AUDIO_ENV, **_FFPLAY_KW)
        except Exception:
            self.osd = ("播放失败", time.monotonic() + 1.2)
            return
        self.playing_voice = item
        self._voice_at = time.monotonic()

    # ── 电视 (工具第 8 卡): 直播源内置 tv_channels.py; 播放链移植官方
    #    cardputer-zero-iptv 的 panel.py — 单 ffmpeg 进程: HLS →
    #    fps+scale 滤镜 → rgb24 rawvideo 管道 (读帧线程只留最新, 主循环
    #    10fps blit), 音频同进程 -f pulse; 硬解 h264_v4l2m2m 失败回退软解.
    #    滤镜顺序/不加 reconnect 旗标均照 panel.py 真机实证 (强加 reconnect
    #    会致 HLS 分段边沿卡顿). ─────────────────────────────────────────
    TV_FPS = 12
    TV_FIRST_TIMEOUT = 20.0                 # 起播无首帧判死 (白天源拥堵实测 10-17s)
    TV_STALL_TIMEOUT = 8.0                  # 播放中断流判定

    @staticmethod
    def tv_tabs():
        """分组页签: 收藏在最前 (打开即看), 后接四大分组."""
        import tv_channels
        return ["收藏"] + tv_channels.GROUPS

    def tv_list(self, grp=None):
        """分组频道表 (grp=None → 当前组). grp 0=收藏 (名字→频道, 缺名剔),
        1+ = tv_channels.GROUPS[grp-1]."""
        import tv_channels
        g = self.tv_grp if grp is None else grp
        if g == 0:
            by = {c[0]: c for c in tv_channels.CHANNELS}
            return [by[n] for n in self.tv_fav if n in by]
        return [c for c in tv_channels.CHANNELS
                if c[1] == tv_channels.GROUPS[g - 1]]

    def tv_restore(self):
        """进 app 恢复上次分组/光标 (tv_last.json; grp 存页签名).
        旧格式 int grp (无收藏位 0) 顺移 +1."""
        tabs = self.tv_tabs()
        v = netmod.load_json_file(TV_LAST_CACHE, None)
        if isinstance(v, dict):
            g = v.get("grp")
            if isinstance(g, str) and g in tabs:
                self.tv_grp = tabs.index(g)
            elif isinstance(g, int) and 0 <= g < len(tabs) - 1:
                self.tv_grp = g + 1             # 旧格式迁移
            s = v.get("sel", 0)
            n = len(self.tv_list())
            if isinstance(s, int) and n:
                self.tv_sel = max(0, min(s, n - 1))
                self.tv_sels[self.tv_grp] = self.tv_sel

    def tv_save(self):
        netmod.save_json_file(TV_LAST_CACHE,
                              {"grp": self.tv_tabs()[self.tv_grp],
                               "sel": self.tv_sel})

    def tv_fav_toggle(self, name=None):
        """5 键收藏/取消 (播放中=当前台, 列表=选中台); 落 tv_fav.json.
        取消需二次确认 (防误按, 与电台同款)."""
        lst = self.tv_list()
        name = name or (self.tv_playing[0] if self.tv_playing else
                        lst[self.tv_sel][0] if 0 <= self.tv_sel < len(lst)
                        else None)
        if not name:
            return
        if name in self.tv_fav:
            self.dialog = ("untvfav", name)
            return
        self.tv_fav.append(name)
        self.osd = ("已收藏 · %s" % name, time.monotonic() + 1.5)
        self.tv_fav_save()
        n = len(self.tv_list())                # 收藏组可能变短, 光标钳回
        if self.tv_grp == 0 and n:
            self.tv_sel = min(self.tv_sel, n - 1)
            self.tv_sels[0] = self.tv_sel

    def tv_fav_remove(self, name):
        """确认框 Enter 后真正移除 (tv_fav_toggle 的取消半程)."""
        if name not in self.tv_fav:
            return
        self.tv_fav.remove(name)
        self.tv_fav_save()
        self.osd = ("已取消收藏", time.monotonic() + 1.2)
        n = len(self.tv_list())
        if self.tv_grp == 0 and n:
            self.tv_sel = min(self.tv_sel, n - 1)
            self.tv_sels[0] = self.tv_sel

    @staticmethod
    def tv_hwdec_available():
        """bcm2835-codec-decode 设备存在且可读写 → v4l2m2m 硬解可用."""
        try:
            for p in sorted(glob.glob("/sys/class/video4linux/video*/name")):
                if open(p).read().strip() != "bcm2835-codec-decode":
                    continue
                dev = "/dev/" + os.path.basename(os.path.dirname(p))
                if os.access(dev, os.R_OK | os.W_OK):
                    return True
        except Exception:
            pass
        return False

    def tv_argv(self, url, hw, audio):
        """ffmpeg argv: 视频 rgb24 管道 + 音频同进程 pulse (panel.py 同款).
        源 host 在 tv_channels.UA_BY_HOST 表里 (aptv 转发) → 带 -user_agent."""
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
               "-protocol_whitelist", "file,pipe,crypto,http,https,tcp,tls"]
        try:
            from urllib.parse import urlparse
            import tv_channels
            ua = tv_channels.UA_BY_HOST.get(urlparse(url).netloc.split(":")[0])
            if ua:
                cmd += ["-user_agent", ua]
        except Exception:
            pass
        if hw:                               # 硬解前缀 (输出前 copy 免回读抢占)
            cmd += ["-c:v", "h264_v4l2m2m", "-num_capture_buffers", "8",
                    "-num_output_buffers", "8", "-pixel_format", "yuv420p"]
        vf = ("copy," if hw else "") + \
             "fps=%d,scale=%d:%d:flags=fast_bilinear" % (self.TV_FPS, W, H)
        cmd += ["-i", url, "-map", "0:v:0", "-vf", vf,
                "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
        if audio:
            cmd += ["-map", "0:a:0", "-ac", "2", "-ar", "44100",
                    "-f", "pulse", "default"]
        else:
            cmd.append("-an")
        return cmd

    def _tv_spawn(self, cmd):
        """spawn 点独立成方法 — 测试打桩用 (无真 ffmpeg/网络)."""
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                env=_AUDIO_ENV, **_FFPLAY_KW)

    def tv_play(self, ch, hw=None, audio=True, att=0, url_i=0):
        """起播: 杀旧会话 → spawn → 读帧线程. ch=(name, group, urls);
        url_i=线路序号 (失败自动换下一路, 全尽才报无法播放);
        hw=None 自动探测硬解; att=尝试计数 (日志/保险丝)."""
        self.tv_stop()
        if self.playing_key:                  # 音频焦点: 电台/音乐让路
            self.radio_stop()
        if self.mu_proc or self.mu_playing:
            self.mu_stop()
        urls = ch[2]
        url_i = max(0, min(url_i, len(urls) - 1))
        if hw is None:
            hw = self.tv_hwdec_available()
        cmd = self.tv_argv(urls[url_i], hw, audio)
        try:
            proc = self._tv_spawn(cmd)
        except Exception:
            self.tv_err = "无法启动播放器"
            self._tv_log("spawn-fail %s" % ch[0])
            return
        self.tv_gen += 1
        self.tv_playing = ch
        self.tv_url_i = url_i
        self.tv_hw = hw
        self.tv_audio = audio
        self.tv_att = att
        self.tv_frame = None
        self.tv_frames = 0
        self.tv_first_at = None
        self.tv_dead = None
        self.tv_err = ""
        now = time.monotonic()
        self.tv_started = self.tv_last_at = self.tv_osd_at = now
        self.tv_proc = proc
        self._tv_log("play %s url=%d/%d att=%d hw=%d audio=%d %s" % (
            ch[0], url_i + 1, len(urls), att, hw, audio, urls[url_i]))
        self.tv_reader = threading.Thread(target=self._tv_read_loop,
                                          args=(self.tv_gen, proc), daemon=True)
        self.tv_reader.start()

    def _tv_read_loop(self, gen, proc):
        """读帧线程: select 双管道, stdout 按整帧 (W*H*3) 拼, 只保留最新一帧
        (主循环 10fps 消费, 生产 12fps 丢旧不积压); stderr 截尾收集.
        会话号 gen 不匹配 (已停播/已换台) 即退出, 不再触碰状态."""
        frame_bytes = W * H * 3
        buf = bytearray()
        err = bytearray()
        try:
            out_fd = proc.stdout.fileno()
            fds = [out_fd, proc.stderr.fileno()]
        except (OSError, ValueError):
            return
        try:
            while fds:
                if gen != self.tv_gen:
                    return
                r, _, _ = select.select(fds, [], [], 0.25)
                for fd in r:
                    try:
                        data = os.read(fd, 1 << 16)
                    except (OSError, ValueError):
                        data = b""
                    if not data:              # EOF: 此管道收工
                        fds = [f for f in fds if f != fd]
                        continue
                    if fd != out_fd:
                        err += data
                        if len(err) > 8192:
                            del err[:-8192]
                        continue
                    buf += data
                    while len(buf) >= frame_bytes:
                        if gen != self.tv_gen:
                            return
                        self.tv_frame = bytes(buf[:frame_bytes])
                        del buf[:frame_bytes]
                        now = time.monotonic()
                        if self.tv_first_at is None:
                            self.tv_first_at = now
                            self._tv_log("first-frame %.1fs" %
                                         (now - self.tv_started))
                        self.tv_frames += 1
                        self.tv_last_at = now
        except OSError:
            return                            # fd 被停播关闭的竞态 → 安静退出
        if gen != self.tv_gen:
            return
        try:
            rc = proc.wait(timeout=1)
        except Exception:
            rc = proc.poll()
        self.tv_dead = {"rc": rc,
                        "err": bytes(err).decode("utf-8", "replace")}

    def tv_stop(self):
        """停播: 杀 ffmpeg (TERM→0.5s→KILL 补刀 — ffmpeg SIGTERM futex 僵死
        教训) + 收读帧线程; 播放态清空, 错误文案留给列表页."""
        if self.tv_playing and self.tv_frames:
            self._tv_log("stop %s frames=%d" % (self.tv_playing[0],
                                                self.tv_frames))
        proc = self.tv_proc
        self.tv_gen += 1                      # 读帧线程守卫 (先加号再杀)
        self.tv_proc = None
        self.tv_playing = None
        self.tv_frame = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
        if self.tv_reader is not None:
            # 先收线程再关管道: 子进程死 → 管道 EOF → 线程自然退 (0.25s 内)
            self.tv_reader.join(timeout=1.5)
            self.tv_reader = None
        for f in (getattr(proc, "stdout", None),
                  getattr(proc, "stderr", None)):
            try:
                if f:
                    f.close()
            except Exception:
                pass

    def _tv_fail(self, msg):
        self._tv_log("fail " + msg)
        self.tv_stop()
        self.tv_err = msg

    def _tv_retry(self, why, hw, audio, url_i):
        """换参数/换线路重播同频道. 保险丝: 尝试数 > 2×线路数+2 强制收."""
        ch = self.tv_playing
        if ch is None or self.tv_att > 2 * len(ch[2]) + 2:
            self._tv_fail("无法播放 · 换台试试")
            return
        self._tv_log("retry " + why)
        self.tv_play(ch, hw=hw, audio=audio, att=self.tv_att + 1,
                     url_i=url_i)

    def tv_watchdog(self):
        """poll_tool 每秒: 进程退出/起播超时/断流 → 零帧分类 (可修先修),
        修不了换下一路线路; 线路穷尽才报无法播放回列表."""
        if not self.tv_playing:
            return
        now = time.monotonic()
        exited = self.tv_proc is None or self.tv_proc.poll() is not None
        if exited or (self.tv_frames == 0 and
                      now - self.tv_started > self.TV_FIRST_TIMEOUT):
            self._tv_classify(exited)
            return
        if self.tv_frames and now - self.tv_last_at > self.TV_STALL_TIMEOUT:
            self._tv_next_url("信号中断, 换下一路",
                              "信号中断 · 换台可恢复")   # 断流也换路续播

    def _tv_next_url(self, why, last_msg):
        """换下一路线路; 已是最后一路 → 报 last_msg 回列表."""
        ch = self.tv_playing
        if ch is None or self.tv_url_i + 1 >= len(ch[2]):
            self._tv_fail(last_msg)
            return
        self._tv_retry(why, hw=None, audio=True, url_i=self.tv_url_i + 1)

    def _tv_classify(self, exited):
        """零帧失败分类: 同线路可修 (关声/软解) 优先, 修不了换下一路,
        线路穷尽才按 stderr 报文案回列表."""
        if self.tv_frames:                    # 出过画再断 = 断流换路
            self._tv_next_url("信号中断, 换下一路", "信号中断 · 换台可恢复")
            return
        dead = self.tv_dead or {}
        low = dead.get("err", "").lower()
        if self.tv_audio and "matches no streams" in low:
            self._tv_retry("流无音频, 关声重试",
                           hw=self.tv_hw, audio=False, url_i=self.tv_url_i)
            return
        if self.tv_hw and (not exited or "v4l2m2m" in low
                           or "no device" in low
                           or "cannot allocate" in low):
            self._tv_retry("硬解未出画, 切软解",
                           hw=False, audio=self.tv_audio, url_i=self.tv_url_i)
            return
        m = re.search(r"(?:server returned|http error)\s*(?:status\s+)?(\d{3})", low)
        code = m.group(1) if m else ""
        # 其余一切 (纯超时/连接错/HTTP 错/退出) = 这一路救不了 → 换下一路;
        # 线路穷尽按 stderr 报具体文案回列表
        ch = self.tv_playing
        if ch is None:
            return
        if code in ("404", "401", "403"):
            last = "源不可用 (%s) · 换台试试" % code
        elif "timed out" in low or "connection" in low:
            last = "连接超时 · 稍后再试"
        else:
            last = "无法播放 · 换台试试"
        if self.tv_url_i + 1 < len(ch[2]):
            self._tv_retry("本路失败 (%s), 换下一路" % (code or "超时/退出"),
                           hw=None, audio=True, url_i=self.tv_url_i + 1)
        else:
            self._tv_fail(last)

    def tv_key(self, k):
        """列表: ←→ 切分组 (收藏在最前) ↑↓ 选台 Enter 播 5 收藏;
        播放态: ↑↓ 换台 5 收藏当前台 (Esc 外层)."""
        lst = self.tv_list()
        if self.tv_playing:
            if k in (pygame.K_UP, pygame.K_DOWN) and lst:
                d = -1 if k == pygame.K_UP else 1
                self.tv_sel = (self.tv_sel + d) % len(lst)
                self.tv_sels[self.tv_grp] = self.tv_sel
                self.tv_play(lst[self.tv_sel])
                self.tv_save()
            elif k in (pygame.K_LEFT, pygame.K_RIGHT):
                # 卡顿自救: ←→ 手动换当前频道的线路 (循环, 单路提示)
                ch = self.tv_playing
                if len(ch[2]) < 2:
                    self.osd = ("无备用线路", time.monotonic() + 1.2)
                else:
                    ni = (self.tv_url_i + (1 if k == pygame.K_RIGHT else -1))                         % len(ch[2])
                    self.tv_play(ch, url_i=ni)
                    self.osd = ("线路 %d/%d" % (ni + 1, len(ch[2])),
                                time.monotonic() + 1.5)
            elif k == pygame.K_5:
                self.tv_fav_toggle()
            return True
        if k in (pygame.K_LEFT, pygame.K_RIGHT):
            d = -1 if k == pygame.K_LEFT else 1
            self.tv_sels[self.tv_grp] = self.tv_sel   # 存当前组光标
            self.tv_grp = (self.tv_grp + d) % len(self.tv_tabs())
            lst = self.tv_list()
            if lst:                              # 空组光标归 0
                self.tv_sel = min(self.tv_sels.get(self.tv_grp, 0),
                                  len(lst) - 1)
            else:
                self.tv_sel = 0
            self.tv_err = ""
            self.tv_save()
            return True
        if not lst:                              # 空收藏组: 只剩 ↑↓/Esc
            return True
        if k in (pygame.K_UP, pygame.K_DOWN):
            d = -1 if k == pygame.K_UP else 1
            self.tv_sel = (self.tv_sel + d) % len(lst)
            self.tv_sels[self.tv_grp] = self.tv_sel
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.tv_err = ""
            self.tv_play(lst[self.tv_sel])
            self.tv_save()
        elif k == pygame.K_5:
            self.tv_fav_toggle()
        return True

    def draw_tv(self, surf):
        if self.tv_playing:
            self._draw_tv_play(surf)
            return
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        lst = self.tv_list()
        self.tool_head(surf, "电视", "★ %d" % len(lst) if self.tv_grp == 0
                       else "%d 台 · 实测源" % len(lst))
        if self.tv_err:
            text(surf, clip_text(self.tv_err, 9, 200), 9,
                 self.th("danger"), (6, 15))
        # 分组 tabs (收藏在最前)
        gx = 6
        for i, g in enumerate(self.tv_tabs()):
            sel = i == self.tv_grp
            lab = ("★ " + g) if i == 0 else g   # 框/字/步进同宽 (星号入框)
            if sel:
                r = pygame.Rect(gx - 3, 26, textw(lab, 11) + 6, 15)
                pygame.draw.rect(surf, self.card(0), r, border_radius=3)
                pygame.draw.rect(surf, acc, r, 1, border_radius=3)
            text(surf, lab, 11, hi if sel else soft, (gx, 28))
            gx += textw(lab, 11) + 13
        if not lst:                             # 空收藏组提示
            textc(surf, "还没有收藏", 13, dim, W // 2, 72)
            textc(surf, "列表里按 5 收藏喜欢的频道", 10, dim, W // 2, 94)
            self.tool_foot(surf, ("←→ 分组", "5 收藏", "Esc 返回"))
            return
        # 频道列表 4 行 (本地曲库列表同款)
        nrow = 4
        r0 = max(0, min(self.tv_sel - (nrow - 1), max(0, len(lst) - nrow)))
        self.tv_scroll = r0
        for kk in range(nrow):
            i = r0 + kk
            if i >= len(lst):
                break
            name, grp, urls = lst[i]
            y = 44 + kk * 26
            sel = i == self.tv_sel
            rct = pygame.Rect(4, y, W - 8, 24)
            pygame.draw.rect(surf, self.card(0), rct, border_radius=4)
            pygame.draw.rect(surf, acc if sel else self.th("border"), rct,
                             2 if sel else 1, border_radius=4)
            if name in self.tv_fav:             # 收藏星标
                text(surf, "★", 11, acc, (9, y + 6))
            text(surf, clip_text(name, 13, W - 90), 13,     # 只显频道名
                 hi if sel else soft, (24, y + 4))
            text(surf, "%02d" % (i + 1), 10, acc, (W - 24, y + 6), right=True)
        self.tool_foot(surf, ("←→ 组", "↑↓ 选", "5 ★", "Enter 播",
                              "Esc 返回"))

    def _draw_tv_play(self, surf):
        surf.fill((0, 0, 0))
        if self.tv_frame:
            try:
                surf.blit(pygame.image.frombuffer(self.tv_frame,
                                                  (W, H), "RGB"), (0, 0))
            except Exception:
                pass                          # 帧长度异常: 保留黑屏不断流
            if time.monotonic() - self.tv_osd_at < 2.5:   # 开播/换台 OSD
                tag = self.tv_playing[0]
                bar = pygame.Surface((textw(tag, 11) + 12, 17),
                                     pygame.SRCALPHA)
                bar.fill((0, 0, 0, 160))
                surf.blit(bar, (4, 4))
                text(surf, tag, 11, self.th("hi"), (10, 6))
            return
        ch = self.tv_playing
        secs = int(time.monotonic() - self.tv_started)
        textc(surf, "连接中 %ds" % secs, 14, self.th("hi"), W // 2, 56)
        if len(ch[2]) > 1:                    # 多线路: 显当前路 (失败自动换)
            textc(surf, "线路 %d/%d" % (self.tv_url_i + 1, len(ch[2])),
                  10, self.th("dim"), W // 2, 76)
        textc(surf, ch[0], 12, self.th("soft"), W // 2, 94)
        textc(surf, "F/X 换台 · Z/C 换线路 · Esc 返回", 9,
              self.th("dim"), W // 2, 136)

    def _tv_log(self, line):
        """诊断轨迹 /tmp/oneapp_tv.log (E2E 证据: 首帧耗时/帧数/错误)."""
        try:
            p = "/tmp/oneapp_tv.log"
            if os.path.exists(p) and os.path.getsize(p) > 200 * 1024:
                os.truncate(p, 0)
            with open(p, "a") as f:
                f.write(time.strftime("%m-%d %H:%M:%S ") + line + "\n")
        except Exception:
            pass

    # ── 键 ──
    def _silk_input_active(self):
        """输入态 (F/Z/X/C 保持字母, 丝印方向让位): 想法/行情添加/音乐
        搜索/邮件登录/邮件写信/云注册+登录表单."""
        return (self.in_input or self.fin_input
                or self.mu_input or self.ns_input
                or (self.tool == "mail"
                    and self.mail_lv in ("login", "compose"))
                or (self.tool == "set" and (self.set_edit is not None
                                            or self.reg is not None
                                            or self.login is not None)))

    def key(self, k, ch=""):
        # 丝印方向 (非输入态): F/X/Z/C = ↑↓←→ (fn+ 同效, 输入态保持字母)
        if k in (pygame.K_f, pygame.K_x, pygame.K_z, pygame.K_c) \
                and not self._silk_input_active():
            k = {pygame.K_f: pygame.K_UP, pygame.K_x: pygame.K_DOWN,
                 pygame.K_z: pygame.K_LEFT,
                 pygame.K_c: pygame.K_RIGHT}[k]
            ch = ""
        if self.dialog:
            if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if self.dialog[0] == "exit":
                    if self.page == 0:
                        self.radio_stop()
                        return False          # 主页确认 → 退出
                    self.dialog = None        # 非主页: 只关闭对话框
                    return True
                if self.dialog[0] == "delwatch":
                    self.watch_del(self.dialog[1])   # ("delwatch", idx)
                elif self.dialog[0] == "delmail":
                    self.mail_del(self.dialog[1])    # ("delmail", email)
                elif self.dialog[0] == "unfav":
                    self.radio_fav_remove(self.dialog[1])  # ("unfav", key, 名)
                elif self.dialog[0] == "untvfav":
                    self.tv_fav_remove(self.dialog[1])     # ("untvfav", 名)
                elif self.dialog[0] == "delnfeed":
                    self.news_feed_del(self.dialog[1])    # ("delnfeed", fid)
                else:
                    self.delete_selected()   # ("del", item) 确认删除
                self.dialog = None
            elif k == pygame.K_ESCAPE:
                self.dialog = None
            else:                             # 其余任意键 = 关闭对话框
                self.dialog = None
            return True

        if self.page == 3 and self.fin_input:
            # 添加自选股输入框 (想法页同款 IME 交互)
            if k == pygame.K_SLASH:
                self.ime_mode = not self.ime_mode
                self.ime_clear()
                return True
            if self.ime_mode:
                if not self.ime_buf:
                    if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        self.ime_clear()
                        self.fin_input = False
                        self.fin_add_commit()
                        return True
                    if k == pygame.K_ESCAPE:
                        self.ime_clear()
                        self.fin_input = False
                        self.buf = ""
                        return True
                act = self.ime_key(k, ch)
                if act is None:
                    return True
                tag, val = act
                if tag == "del":
                    self.buf = self.buf[:-1]
                elif tag == "submit":
                    self.ime_clear()
                    self.fin_input = False
                    self.fin_add_commit()
                else:
                    self.buf = (self.buf + val)[:16]
                return True
            if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.fin_input = False
                self.fin_add_commit()
            elif k == pygame.K_ESCAPE:
                self.fin_input = False
                self.buf = ""
            elif k == pygame.K_BACKSPACE:
                self.buf = self.buf[:-1]
            elif ch and 0x20 <= ord(ch) < 0x7F:
                if len(self.buf) < 16:
                    self.buf += ch
            return True

        if self.page == 4 and self.ns_input == 1:
            # 加/改源·标题输入 (后标题: 默认拼音, / 切中英 — 标题不含 '/',
            # '/' 只在这里做切换键, 地址相已过不冲突; Enter=保存)
            if k == pygame.K_SLASH:
                self.ime_mode = not self.ime_mode
                self.ime_clear()
                return True
            if self.ime_mode:
                if not self.ime_buf:
                    if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        self.news_feed_commit()
                        return True
                    if k == pygame.K_ESCAPE:
                        self._ns_input_close()
                        return True
                act = self.ime_key(k, ch)
                if act is None:
                    return True
                tag, val = act
                if tag == "del":
                    self.buf = self.buf[:-1]
                elif tag == "submit":
                    self.ime_clear()
                    self.news_feed_commit()
                else:
                    self.buf = (self.buf + val)[:20]
                return True
            if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.news_feed_commit()
            elif k == pygame.K_ESCAPE:
                self._ns_input_close()
            elif k == pygame.K_BACKSPACE:
                self.buf = self.buf[:-1]
            elif ch and 0x20 <= ord(ch) < 0x7F:
                if len(self.buf) < 20:
                    self.buf += ch
            return True

        if self.page == 4 and self.ns_input == 2:
            # 加/改源·地址输入 (先地址: 纯 EN 直输, '/' 是字符绝不劫持 —
            # sym+n 实发 '/', 不碰 IME 切换; Enter → 标题相)
            if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._ns_phase_title()
                return True
            if k == pygame.K_ESCAPE:
                self._ns_input_close()
                return True
            if k == pygame.K_BACKSPACE:
                self.buf = self.buf[:-1]
            elif ch and 0x20 <= ord(ch) < 0x7F:
                if len(self.buf) < 200:
                    self.buf += ch
            return True

        if self.page == 1 and self.in_input:
            if k == pygame.K_SLASH:           # 中/英切换 (IME)
                self.ime_mode = not self.ime_mode
                self.ime_clear()
                return True
            if self.ime_mode:                 # 拼音 IME
                if not self.ime_buf:
                    if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        self.ime_clear()
                        self.submit_idea()
                        return True
                    if k == pygame.K_ESCAPE:
                        self.ime_clear()
                        self.in_input = False
                        self.buf = ""
                        self.esc_t = time.monotonic()   # 长按可继续 → 主页
                        return True
                act = self.ime_key(k, ch)
                if act is None:
                    return True
                tag, val = act
                if tag == "del":
                    self.buf = self.buf[:-1]
                elif tag == "submit":
                    self.ime_clear()
                    self.submit_idea()
                else:                         # char/word
                    self.buf = (self.buf + val)[:120]
                return True
            # 英文直输 (原行为)
            if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.submit_idea()
            elif k == pygame.K_ESCAPE:
                self.in_input = False
                self.buf = ""
                self.esc_t = time.monotonic()   # 长按可继续 → 主页
            elif k == pygame.K_BACKSPACE:
                self.buf = self.buf[:-1]
            elif ch and 0x20 <= ord(ch) < 0x7F:
                if len(self.buf) < 120:
                    self.buf += ch
            return True

        # 工具子应用: 按键全归工具处理 (数字/方向都在工具内), Esc 回入口网格;
        # 换肤键 t 仅主页 (工具内 t 是输入字符, 不劫持).
        if self.tool:
            if k == pygame.K_ESCAPE:
                if self.tool == "set" and (self.set_edit is not None
                                           or self.reg is not None
                                           or self.login is not None
                                           or self.qrlogin is not None):
                    self.set_edit = None       # 关 KEY 弹窗/注册/登录表单,
                    self.set_buf = ""          #   保持在设置页
                    self.reg = None
                    self.login = None
                    self.qrlogin = None
                    self.qr_poll_at = 0.0
                    return True
                if self.tool == "tv" and self.tv_playing:
                    self.tv_stop()             # 播放态 Esc → 回列表 (不退 app)
                elif self.tool == "music" and self.mu_input:
                    if self.ime_buf:
                        self.ime_clear()           # 先退拼音串 (IME)
                    else:
                        self.mu_input = False      # 音乐搜索输入态
                        self.mu_q = ""
                elif self.tool == "music" and self.mu_view == "play":
                    self.tool_close()          # Esc 直接退出音乐 (2026-11-15
                    return True                #   用户定版: 去掉 1s 长按限制)
                elif self.tool == "music" and self.mu_view == "qr":
                    if self.mu_qr and self.mu_qr.state != "idle":
                        self.mu_qr.stop()          # 扫码线程掐掉
                    self.mu_view = "src"           # qr Esc → 源选择
                elif self.tool == "music":
                    # src → 播放页; list/search/fav → 源选择 (逐层回播放页;
                    # 退出音乐唯一入口 = 播放页长按 Esc 1s)
                    self.mu_view = "play" if self.mu_view == "src" else "src"
                elif self.tool == "mail":
                    if not self.mail_esc():       # 逐层回退; 账号层 → 网格
                        self.tool_close()
                else:
                    self.tool_close()
                return True
            self.tool_key(k, ch)
            return True

        # 各页动作键占用: 想法(4新增/5录音/7删除/8播放语音)
        # 行情(3商品/4自选/5指数/8图表) 新闻(1-7选条, 源切换走←→)
        # 工具网格页: 数字 1-9/0 全部留给 app 直开
        # 电台页无数字占用 (列表无序号, 换台走 ↑↓+Enter, 输出走 O)
        page_keys = {1: (4, 5, 7, 8),
                     2: (5,),                      # 电台 5=收藏 (防全局跳页截走)
                     3: (4, 5, 6, 8), 4: tuple(range(1, 8)),
                     5: tuple(range(0, 10))}
        digit = k - pygame.K_0                # pygame 常量值 → 数字序号
        if self.page == 0 and k == pygame.K_9:
            self.page = 5                     # 主页 9 工具 (2026-11-16 用户定版:
            self.in_input = False             #   与邮件对调; 网格不列邮件)
        elif self.page == 0 and k == pygame.K_8:
            self.tool_open("mail")            # 主页 8 邮件直达 (工具网格不再列)
        elif (k in (pygame.K_4, pygame.K_5, pygame.K_6, pygame.K_7, pygame.K_8)
                and digit not in page_keys.get(self.page, ())):
            self.page = k - pygame.K_3        # 想法=4 电台=5 行情=6 新闻=7 工具=8
                                              # (主页仅长按 Esc)
            self.in_input = False
        elif k in (pygame.K_LEFT, pygame.K_UP):
            if self.page == 5:
                self.tool_grid_key(k)
            elif self.page == 1:
                if self.iview == "cloud":
                    self.csel = max(0, self.csel - (2 if k == pygame.K_UP else 1))
                else:
                    self.move_sel(-2 if k == pygame.K_UP else -1)
            elif self.page == 2:
                if k == pygame.K_LEFT:
                    self.radio_grp_cycle(-1)   # ← 切分组 (↑ 仍移光标)
                else:
                    self.move_rsel(-1)
            elif self.page == 3 and not self.fdetail and not self.fin_input:
                self._move_fsel(-3 if k == pygame.K_UP else -1)
            elif self.page == 4:
                if self.news_set:
                    self._ns_move(-1)              # 设置视图: 四向都移光标
                elif k == pygame.K_LEFT:
                    self._news_src_cycle(-1)       # ← 切换源
                elif self.news_detail:
                    self.news_detail_scroll = max(0,
                        self.news_detail_scroll - 1)   # 上滚一行
                else:
                    self.move_news(-1)
            else:
                self.page = (self.page - 1) % len(PAGES)
        elif k in (pygame.K_RIGHT, pygame.K_DOWN):
            if self.page == 5:
                self.tool_grid_key(k)
            elif self.page == 1:
                if self.iview == "cloud":
                    n = len(self.cloud_notes)
                    self.csel = max(0, min(n - 1, self.csel
                                           + (2 if k == pygame.K_DOWN else 1)))
                else:
                    self.move_sel(2 if k == pygame.K_DOWN else 1)
            elif self.page == 2:
                if k == pygame.K_RIGHT:
                    self.radio_grp_cycle(1)    # → 切分组 (↓ 仍移光标)
                else:
                    self.move_rsel(1)
            elif self.page == 3 and not self.fdetail and not self.fin_input:
                self._move_fsel(3 if k == pygame.K_DOWN else 1)
            elif self.page == 4:
                if self.news_set:
                    self._ns_move(1)
                elif k == pygame.K_RIGHT:
                    self._news_src_cycle(1)        # → 切换源
                elif self.news_detail:
                    self.news_detail_scroll += 1       # 下滚一行
                else:
                    self.move_news(1)
            else:
                self.page = (self.page + 1) % len(PAGES)
        elif self.page == 0 and k == pygame.K_t:
            self.cycle_theme()                # 换肤键仅主页 (工具内 t=输入)
        elif k == pygame.K_ESCAPE:
            if self.news_detail:              # 新闻详情 → 关详情
                self.news_detail = None
            elif self.page == 4 and self.news_set:   # 源设置 → 回新闻列表
                self.news_set = False
                self.buf = ""
            elif self.fdetail:                # 图表详情 → 关图表
                self.fdetail = None
            elif self.page == 1 and self.iview == "cloud":
                self.iview = "local"          # 云端列表 → 本地卡片 (逐层)
                self.csel = 0
            elif self.page != 0:
                if self.fin_input:                    # 输入态: Esc 先退输入
                    self.fin_input = False
                    self.buf = ""
                    self.ime_clear()
                    return True
                if self.rec_proc:
                    self.rec_stop()           # 离开想法页自动收音保存
                self.page = 0                 # 回主页
                self.in_input = False
                self.osd = ("返回主页", time.monotonic() + 1.0)
            else:
                self.esc_hold_at = time.monotonic()
                # 主页长按 Esc 1s 退出 app (取消确认框交互, 与音乐播放页同款)
        elif self.page == 1:
            if self.iview == "cloud":         # 云端列表: 只剩 播/删/回本地
                if k == pygame.K_9:
                    self.iview = "local"
                elif k in (pygame.K_d, pygame.K_7) and self.cloud_notes:
                    self.cloud_del_sel()
                elif (k == pygame.K_8 or k in (pygame.K_RETURN,
                                               pygame.K_KP_ENTER)) \
                        and self.cloud_notes \
                        and self.csel < len(self.cloud_notes):
                    self.cloud_voice_sel()
                return True
            items = self.idea_items()
            if k == pygame.K_9:               # 云端视图 (触发双写对账+拉列表)
                self.iview = "cloud"
                self.csel = 0
                self.cloud_sync_trigger()
                self.cloud_list_fetch()
            if k == pygame.K_n or k == pygame.K_4:      # 增加
                self.ime_clear()
                self.in_input = True
                self.buf = ""
            elif k == pygame.K_r or k == pygame.K_5:    # 录音 (再按停止)
                self.rec_toggle()
            elif (k == pygame.K_d or k == pygame.K_7) and items:   # 删除
                self.dialog = ("del", items[self.sel] if self.sel < len(items) else None)
            elif (k == pygame.K_8 or k in (pygame.K_RETURN,
                                           pygame.K_KP_ENTER)) and items \
                    and self.sel < len(items) \
                    and items[self.sel]["kind"] == "voice":
                self.play_voice(items[self.sel])          # 播放/停止
        elif self.page == 2:
            if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.radio_toggle()              # Enter = 播放/暂停
            elif k == pygame.K_5:
                self.radio_fav_toggle()          # 5 = 收藏/取消
            elif k == pygame.K_o:
                self.radio_out_cycle()           # O = 输出 本机→AirPlay 循环
        elif self.page == 5:
            # 工具入口网格: 数字 1-9/0 直开, Enter 打开
            # (方向键已在上方分支转入 tool_grid_key; Esc 全局分支 → 回主页)
            self.tool_grid_key(k)
        elif self.page == 4:
            if self.news_set:
                # 源设置视图: S/Esc 退 · Enter 显/隐 · N 加 · E 改 · D 删自定义
                if k in (pygame.K_s, pygame.K_ESCAPE):
                    self.news_set = False
                    self.buf = ""
                elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self.news_feed_toggle()
                elif k == pygame.K_n:             # 加源: 地址先 (EN '/' 直输)
                    self.ns_input = 2
                    self.ns_url = ""
                    self.ns_edit_id = None
                    self.buf = ""
                    self.ime_clear()
                elif k == pygame.K_e:             # 改源 (仅自定义): 地址 → 标题
                    f = self.news_feeds[self.ns_sel] \
                        if 0 <= self.ns_sel < len(self.news_feeds) else None
                    if f and str(f.get("id", "")).startswith("c_"):
                        self.ns_input = 2
                        self.ns_url = ""
                        self.ns_edit_id = f["id"]
                        self.buf = str(f.get("url") or "")
                        self.ime_clear()
                    elif f:
                        self.osd = ("内置源不可编辑", time.monotonic() + 2)
                elif k == pygame.K_d:
                    f = self.news_feeds[self.ns_sel] \
                        if 0 <= self.ns_sel < len(self.news_feeds) else None
                    if f and str(f.get("id", "")).startswith("c_"):
                        self.dialog = ("delnfeed", f["id"])
                return True
            if k == pygame.K_s:                   # 进源设置 (列表态)
                self.news_set = True
                self.news_detail = None
                self.ns_sel = self.ns_scroll = 0
                return True
            if k == pygame.K_p:
                # P = 列表翻页 (2026-11-21 用户定版: 换页指条目翻页不是跳页,
                # 跳工具页的旧行为已删; 大写 Shift+P = 上翻; 详情态翻正文行)
                if self.news_detail:
                    self.news_detail_scroll += -7 if ch == "P" else 7
                else:
                    self.move_news(-7 if ch == "P" else 7)
                return True
            digit = k - pygame.K_0
            if pygame.K_1 <= k <= pygame.K_7 and not self.news_detail:
                idx = self.news_scroll + digit - 1   # 窗口内 1-7, 翻页复用
                items = self.news_items()
                if idx < len(items):
                    self.news_sel = idx
                    self.news_detail_scroll = 0
                    self.news_detail = items[idx]

            elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                items = self.news_items()
                if 0 <= self.news_sel < len(items):
                    self.news_detail_scroll = 0
                    self.news_detail = items[self.news_sel]
        elif self.page == 3:
            if self.fdetail:
                if k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE):
                    self.fdetail = None
                elif k == pygame.K_m:
                    self.fdetail["view"] = "min"
                    self.fdetail["zoom"] = 1.0
                elif k == pygame.K_d:
                    self.fdetail["view"] = "day"
                    self.fdetail["zoom"] = 1.0
                elif k == pygame.K_9:             # 放大
                    self.fdetail["zoom"] = min(4.0, self.fdetail.get("zoom", 1.0) * 1.3)
                elif k == pygame.K_0:             # 缩小
                    self.fdetail["zoom"] = max(0.5, self.fdetail.get("zoom", 1.0) / 1.3)
            else:
                if k == pygame.K_n and self.fin_view == 0:   # 添加自选股
                    self.ime_clear()
                    self.fin_input = True
                    self.buf = ""
                    self.cv_armed = False
                elif (k == pygame.K_d) \
                        and self.fin_view == 0 and self.fin_items():
                    items = self.fin_items()
                    if 0 <= self.fsel < len(items):
                        self.dialog = ("delwatch",
                                       items[self.fsel].get("code"))
                elif k == pygame.K_4:             # 切换行情: 自选→指数→商品
                    self.fin_view = (self.fin_view + 1) % len(self.FIN_TABS)
                    self.fsel = self.fscroll = 0
                elif k == pygame.K_5:             # 指数 (直选, 不提示)
                    self.fin_view = 1
                    self.fsel = self.fscroll = 0
                elif k == pygame.K_6:             # 商品 (直选, 不提示)
                    self.fin_view = 2
                    self.fsel = self.fscroll = 0
                elif k == pygame.K_8 and self.fin_view == 0:   # 图表
                    items = self.fin_items()
                    if 0 <= self.fsel < len(items):
                        it = items[self.fsel]
                        self.open_detail(qt_code(it["code"]),
                                         it.get("name") or it["code"])
                elif k in (pygame.K_RETURN, pygame.K_KP_ENTER) and \
                        self.fin_view == 0:
                    items = self.fin_items()   # 在分支内取, 避免未赋值引用
                    if 0 <= self.fsel < len(items):
                        it = items[self.fsel]
                        # 直开日线 (用户偏好; m 键详情内可切回分时)
                        self.open_detail(qt_code(it["code"]),
                                         it.get("name") or it["code"],
                                         view="day")
        return True

    # ── 工具页: 入口网格 + 子应用 (参照 tab5 驾驶舱工具页) ───────────────────
    def cycle_theme(self):
        self.theme_idx = (self.theme_idx + 1) % len(THEMES)
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            json.dump({"idx": self.theme_idx}, open(THEME_FILE, "w"))
        except Exception:
            pass

    # ── 工具·设置: TAB 页签 (卡片 = API KEY + 卡片开关; 主题 = 主题选择) ──
    SET_TABS = ("卡片", "账户", "主题")

    def _card_on(self, ck):
        """卡片有效开关态 (缺省全开) — 计数/开关/绘制同源."""
        return self.cfg["cards"].get(ck, True)

    def _card_on_n(self):
        return sum(1 for ck in CARD_KEYS if self._card_on(ck))

    def _card_norm(self):
        """开机归一: 有效开 >3 张时, 排序靠后的显式关掉 (2026-11-16 用户重申)."""
        over = [ck for ck in CARD_KEYS if self._card_on(ck)][3:]
        for ck in over:
            self.cfg["cards"][ck] = False
        if over:
            self.cfg_save()

    def _set_rows(self):
        """选中页签的行: 卡片 = 卡片开关; 账户 = Oneapp 账户全套;
        主题 = 全部主题. (管理员 TOKEN/生成注册码 已下放 web 管理后台)"""
        tab = self.SET_TABS[self.set_tab]
        if tab == "主题":
            return [("theme:%d" % i, t["name"])
                    for i, t in enumerate(THEMES)]
        if tab == "账户":
            return [("login", "更换账户" if self.cfg.get("cloud_tok")
                     else "登录"),
                    ("qrlogin", "扫码登录 (免输token)"),
                    ("wxbind", "绑定微信"),
                    ("reg", "注册新账号"), ("logout", "退出登录")]
        return [("sw:hitokoto", "每日一句"), ("sw:news", "新闻"),
                ("sw:fin", "自选股"),
                ("sw:sched", "提醒/行程卡")]

    def cfg_save(self):
        netmod.save_json_file(SETTINGS_FILE, self.cfg)

    SET_CFGKEYS = {
                   "cloud": "cloud_url", "tok": "cloud_tok"}

    def set_key(self, k, ch):
        if self.kv_confirm is not None:        # kv 同步确认框 (模态)
            d = self.kv_confirm
            if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.kv_confirm = None
                self.kv_push(d["k"])           # 上传本地到云端
                self.osd = ("%s 已上传本地到云端"
                            % KV_LABEL.get(d["k"], d["k"]),
                            time.monotonic() + 2)
                self.kv_align_next()
            elif k in (pygame.K_ESCAPE, pygame.K_c):
                self.kv_confirm = None
                self.kv_apply(d["k"], d.get("v"))   # 云端覆盖本地
                self.osd = ("%s 已用云端覆盖本地"
                            % KV_LABEL.get(d["k"], d["k"]),
                            time.monotonic() + 2)
                self.kv_align_next()
            return True                        # 其余键模态吞掉
        if self.qrlogin is not None:           # 扫码登录态: Esc/q 关, 其余忽略
            if k in (pygame.K_ESCAPE, pygame.K_q):
                self.qrlogin = None
                self.qr_poll_at = 0.0
            return True
        if self.login is not None:             # 登录表单态: 用户名/密码/TOKEN
            return self._login_key(k, ch)
        if self.reg is not None:               # 注册表单态: 两字段 ASCII
            return self._reg_key(k, ch)
        if self.set_edit is not None:          # KEY 弹窗编辑态: ASCII 直输
            if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.cfg[self.SET_CFGKEYS.get(self.set_edit,
                                              "cloud_url")] = \
                    self.set_buf.strip()
                self.cfg_save()
                self.set_edit = None
                self.osd = ("已保存", time.monotonic() + 1.5)
                if self.cfg.get("cloud_url") and self.cfg.get("cloud_tok"):
                    self.cloud_ping_trigger()  # 云配置保存 → 即时验证
                return True
            if k == pygame.K_ESCAPE:
                self.set_edit = None
                self.set_buf = ""
                return True
            if k == pygame.K_BACKSPACE:
                self.set_buf = self.set_buf[:-1]
            elif ch and 0x20 <= ord(ch) < 0x7F:
                self.set_buf = (self.set_buf + ch)[:64]
            return True
        if k in (pygame.K_LEFT, pygame.K_RIGHT):
            d = 1 if k == pygame.K_RIGHT else -1
            self.set_tab = (self.set_tab + d) % len(self.SET_TABS)
            self.set_sel = 0
            return True
        if k in (pygame.K_UP, pygame.K_DOWN):
            d = 1 if k == pygame.K_DOWN else -1
            n = len(self._set_rows())
            self.set_sel = max(0, min(n - 1, self.set_sel + d))
            return True
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            rows = self._set_rows()
            rid = rows[min(self.set_sel, len(rows) - 1)][0]
            if rid.startswith("sw:"):
                ck = rid.split(":", 1)[1]
                cur = self._card_on(ck)
                if cur:                          # ON → OFF 随意
                    self.cfg["cards"][ck] = False
                    self.cfg_save()
                else:                            # OFF → ON 须 ≤3 张
                    on = self._card_on_n()
                    if on >= 3:
                        self.osd = ("最多 3 卡, 请先关一个",
                                    time.monotonic() + 2.5)
                    else:
                        self.cfg["cards"][ck] = True
                        self.cfg_save()
            elif rid.startswith("theme:"):
                self.theme_idx = int(rid.split(":", 1)[1]) % len(THEMES)
                json.dump({"idx": self.theme_idx}, open(THEME_FILE, "w"))
                self.osd = ("主题: " + THEMES[self.theme_idx]["name"],
                            time.monotonic() + 2)
            elif rid == "reg":                 # 注册表单 (用户名+邀请码)
                self.reg = {"name": "", "invite": "", "password": "",
                            "field": 0}
            elif rid == "qrlogin":             # 扫码登录: 出码+轮询
                self._cloud_qr_open()
            elif rid == "wxbind":              # 绑定微信 (当前登录账号)
                if not self.cfg.get("cloud_tok"):
                    self.osd = ("先登录 再绑定微信",
                                time.monotonic() + 2.5)
                else:
                    self.net.invalidate(("job", "cloudwxbind"))
                    self.net.job("cloudwxbind", self._cloud_wxbind_fn,
                                 ttl=10)
                    self.osd = ("出码中…", time.monotonic() + 5)
            elif rid == "login":               # 登录/更换账户 表单
                self.login = {"user": "", "pw": "", "tok": "", "field": 0}
            elif rid == "logout":              # 退出登录 (本地数据不动)
                u = self.cfg.get("cloud_user") or ""
                if self.cfg.get("cloud_tok"):
                    self.cfg["cloud_tok"] = ""
                    self.cfg["cloud_user"] = ""
                    self.cfg_save()
                    self.osd = ("已退出 %s · 云数据仅本地" % u
                                if u else "已退出",
                                time.monotonic() + 2.5)
                else:
                    self.osd = ("未登录", time.monotonic() + 1.5)
        return True

    def _login_key(self, k, ch):
        """登录表单键流: ↑↓ 换字段 (用户名/密码/TOKEN) / ASCII 直输
        (用户名强制小写) / Enter 按 填了TOKEN→ping 或 用户名+密码→
        /v1/auth/login 验证."""
        f = self.login
        if k == pygame.K_ESCAPE:
            self.login = None
            return True
        if k in (pygame.K_UP, pygame.K_DOWN):
            f["field"] = (f["field"] + 1) % 3
            return True
        if k == pygame.K_BACKSPACE:
            key = ("user", "pw", "tok")[f["field"]]
            f[key] = f[key][:-1]
            return True
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if self.login_busy:
                return True
            if not (f["tok"].strip()
                    or (f["user"].strip() and f["pw"])):
                self.osd = ("先填 TOKEN 或 用户名+密码",
                            time.monotonic() + 2)
                return True
            self.login_busy = True
            self.net.invalidate(("job", "cloudlogin"))
            self.net.job("cloudlogin", self._cloud_login_fn, ttl=10)
            self.osd = ("登录中…", time.monotonic() + 5)
            return True
        if ch and 0x20 <= ord(ch) < 0x7F:
            key = ("user", "pw", "tok")[f["field"]]
            cap = (16, 64, 48)[f["field"]]
            if key == "user":
                ch = ch.lower()                    # 用户名小写
            f[key] = (f[key] + ch)[:cap]
        return True

    def _cloud_login_fn(self):
        """worker: 填了 TOKEN → ping 验证; 否则用户名+密码 →
        POST /v1/auth/login 换 30 天会话 token. 成功统一 →
        {ok, user, tok, url, now} (now=换号 csync 重置水位)."""
        f = self.login or {}
        url = (self.cfg.get("cloud_url") or CLOUD_URL_DEFAULT).rstrip("/")
        tok = f.get("tok", "").strip()
        if tok:
            st, body = netmod.http_raw(url + "/v1/ping", timeout=8,
                                       headers={"Authorization":
                                                "Bearer " + tok})
            try:
                j = json.loads(body.decode("utf-8", "replace"))
            except ValueError:
                j = {}
            if st == 200 and j.get("ok"):
                return {"ok": 1, "user": j.get("user") or "?",
                        "tok": tok, "url": url,
                        "now": self._cloud_now(url, tok)}
            return {"ok": 0, "st": st,
                    "err": "TOKEN 无效" if st == 401 else
                           ("HTTP %s" % st if st else "网络异常")}
        body = json.dumps({"username": f.get("user", "").strip(),
                           "password": f.get("pw", ""),
                           "name": "zero"}).encode()
        st, rbody = netmod.http_raw(url + "/v1/auth/login", method="POST",
                                    body=body, timeout=8,
                                    headers={"Content-Type":
                                             "application/json"})
        try:
            j = json.loads(rbody.decode("utf-8", "replace"))
        except ValueError:
            j = {}
        if st == 200 and j.get("ok"):
            tok2 = j.get("token") or ""
            return {"ok": 1, "user": j.get("user") or "?",
                    "tok": tok2, "url": url,
                    "now": self._cloud_now(url, tok2)}
        return {"ok": 0, "st": st,
                "err": j.get("err") or
                       ("HTTP %s" % st if st else "网络异常")}

    def _cloud_now(self, url, tok):
        """tombstones 水位 (换号 → csync 重置到新号当前), 失败不拦登录."""
        st, b2 = netmod.http_raw(url + "/v1/tombstones?since=0",
                                 timeout=8,
                                 headers={"Authorization": "Bearer " + tok})
        if st == 200:
            try:
                return json.loads(b2.decode("utf-8", "replace")).get("now")
            except ValueError:
                return None
        return None

    def _reg_key(self, k, ch):
        """注册表单键流: ↑↓ 换字段 / ASCII 直输 (name/invite 强制小写) /
        Enter 出码注册 (扫码绑微信) / Esc 关表单."""
        f = self.reg
        if k == pygame.K_ESCAPE:
            self.reg = None
            return True
        if k in (pygame.K_UP, pygame.K_DOWN):
            f["field"] = (f["field"] + 1) % 3
            return True
        if k == pygame.K_BACKSPACE:
            key = ("name", "invite", "password")[f["field"]]
            f[key] = f[key][:-1]
            return True
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if self.reg_busy:
                return True
            if not f["name"] or not f["invite"]:
                self.osd = ("用户名和邀请码都要填", time.monotonic() + 2)
                return True
            if f["password"] and not (6 <= len(f["password"]) <= 64):
                self.osd = ("密码 6-64 位 (可留空)", time.monotonic() + 2)
                return True
            self.reg_busy = True
            self.net.invalidate(("job", "cloudregqr"))
            self.net.job("cloudregqr", self._cloud_regqr_fn, ttl=10)
            self.osd = ("出码中…", time.monotonic() + 5)
            return True
        if ch and 0x20 <= ord(ch) < 0x7F:
            key = ("name", "invite", "password")[f["field"]]
            cap = (16, 24, 64)[f["field"]]
            if key != "password":
                ch = ch.lower()                    # 用户名/邀请码都小写
            f[key] = (f[key] + ch)[:cap]
        return True

    def _cloud_regqr_fn(self):
        """worker: POST /v1/auth/device/start (reg 会话, 出码即预校验) →
        {ok,dc,url,mat}. 扫码后服务端建号+绑微信+授权设备, 设备 poll 领
        token. 资料取注册表单; 过期重出码时表单已关, 取 qrlogin 存档."""
        f = (self.qrlogin or {}).get("reg") or (self.reg or {})
        url = (self.cfg.get("cloud_url") or CLOUD_URL_DEFAULT).rstrip("/")
        reg = {"name": f.get("name", ""), "invite": f.get("invite", "")}
        if f.get("password"):
            reg["password"] = f["password"]
        body = json.dumps({"name": "zero", "reg": reg}).encode()
        st, rbody = netmod.http_raw(url + "/v1/auth/device/start",
                                    method="POST", body=body, timeout=10,
                                    headers={"Content-Type":
                                             "application/json"})
        try:
            j = json.loads(rbody.decode("utf-8", "replace"))
        except ValueError:
            j = {}
        if st != 200 or not j.get("device_code"):
            return {"ok": 0, "st": st,
                    "err": j.get("err") or
                    ("HTTP %s" % st if st else "网络异常")}
        try:
            import segno
            q = segno.make(j["url"], error="m")
            mat = [[1 if v else 0 for v in r] for r in q.matrix]
        except ImportError:
            return {"ok": 0, "err": "需 pip3 install segno"}
        return {"ok": 1, "dc": j["device_code"], "url": j["url"],
                "mat": mat, "reg": reg}

    # ── 扫码登录 (通用账户设备码流程, noteserver /v1/auth/device/*) ──
    def _cloud_qr_open(self):
        """进扫码登录态: 出码 worker 起跑, 2s 节流轮询在 _cloud_qr_tick."""
        self.qrlogin = {"st": "init", "dc": "", "url": "", "mat": None,
                        "err": ""}
        self.qr_poll_at = 0.0
        self.net.invalidate(("job", "cloudqrstart"))
        self.net.job("cloudqrstart", self._cloud_qrstart_fn, ttl=10)

    def _cloud_qrstart_fn(self):
        """worker: POST /v1/auth/device/start → segno 矩阵直绘数据."""
        url = (self.cfg.get("cloud_url") or CLOUD_URL_DEFAULT).rstrip("/")
        body = json.dumps({"name": "zero"}).encode()
        st, rbody = netmod.http_raw(url + "/v1/auth/device/start",
                                    method="POST", body=body, timeout=10,
                                    headers={"Content-Type":
                                             "application/json"})
        try:
            j = json.loads(rbody.decode("utf-8", "replace"))
        except ValueError:
            j = {}
        if st != 200 or not j.get("device_code"):
            return {"ok": 0, "st": st,
                    "err": "HTTP %s" % st if st else "网络异常"}
        try:
            import segno
            q = segno.make(j["url"], error="m")
            mat = [[1 if v else 0 for v in r] for r in q.matrix]
        except Exception:
            return {"ok": 0, "err": "需 pip3 install segno"}
        return {"ok": 1, "dc": j["device_code"], "url": j["url"], "mat": mat}

    def _cloud_wxbind_fn(self):
        """worker: POST /v1/wx/bindqr/start (Bearer 用户 token) →
        {ok,dc,url,mat}. 扫码后服务端把该微信绑到当前登录账号 (免密)."""
        url = (self.cfg.get("cloud_url") or CLOUD_URL_DEFAULT).rstrip("/")
        st, rbody = netmod.http_raw(
            url + "/v1/wx/bindqr/start", method="POST",
            body=b'{"name":"zero"}', timeout=10,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer "
                     + (self.cfg.get("cloud_tok") or "")})
        try:
            j = json.loads(rbody.decode("utf-8", "replace"))
        except ValueError:
            j = {}
        if st != 200 or not j.get("device_code"):
            return {"ok": 0, "st": st,
                    "err": j.get("err") or
                    ("HTTP %s" % st if st else "网络异常")}
        try:
            import segno
            q = segno.make(j["url"], error="m")
            mat = [[1 if v else 0 for v in r] for r in q.matrix]
        except ImportError:
            return {"ok": 0, "err": "需 pip3 install segno"}
        return {"ok": 1, "dc": j["device_code"], "url": j["url"],
                "mat": mat}

    def _cloud_qrpoll_fn(self):
        """worker: POST /v1/auth/device/poll → pending/approved/done/none."""
        dc = (self.qrlogin or {}).get("dc", "")
        url = (self.cfg.get("cloud_url") or CLOUD_URL_DEFAULT).rstrip("/")
        if not dc:
            return {"st": "none"}
        body = json.dumps({"device_code": dc}).encode()
        st, rbody = netmod.http_raw(url + "/v1/auth/device/poll",
                                    method="POST", body=body, timeout=8,
                                    headers={"Content-Type":
                                             "application/json"})
        try:
            j = json.loads(rbody.decode("utf-8", "replace"))
        except ValueError:
            j = {}
        out = {"st": j.get("status"), "token": j.get("token"),
               "user": j.get("user"), "err": j.get("err")}
        if out["st"] == "approved":
            st2, b2 = netmod.http_raw(url + "/v1/tombstones?since=0",
                                      timeout=8,
                                      headers={"Authorization":
                                               "Bearer " + out["token"]})
            if st2 == 200:
                try:
                    out["now"] = json.loads(
                        b2.decode("utf-8", "replace")).get("now")
                except ValueError:
                    pass
        return out

    def _cloud_qr_tick(self):
        """draw_set 每帧调: 扫码态 2s 节流轮询 (draw 驱动, 离开页面即停)."""
        if self.qrlogin is None or self.qrlogin.get("st") not in \
                ("qr", "wait"):
            return
        now = time.monotonic()
        if now - self.qr_poll_at < 2.0:
            return
        self.qr_poll_at = now
        self.net.invalidate(("job", "cloudqrpoll"))
        self.net.job("cloudqrpoll", self._cloud_qrpoll_fn, ttl=5)

    def _set_mask(self, v):
        return ("..." + v[-6:]) if len(v) > 10 else v

    def draw_set(self, surf):
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        border = self.th("border")
        self._cloud_qr_tick()                  # 扫码登录轮询 (2s 节流)
        self.tool_head(surf, "设置", "←→ 切页 · ↑↓ 选 · Enter 确认")
        # ── TAB 页签栏 ──
        tabs = self.SET_TABS
        tx = 6
        for i, tlab in enumerate(tabs):
            sel = self.set_tab == i
            tw = textw(tlab, 13) + 28
            rr = pygame.Rect(tx, 16, tw, 24)
            if sel:
                pygame.draw.rect(surf, self.card(0), rr, border_radius=4)
                pygame.draw.rect(surf, acc, rr, 2, border_radius=4)
            text(surf, tlab, 13, acc if sel else dim, (tx + 14, rr.y + 5))
            tx += tw + 4
        pygame.draw.line(surf, border, (6, 42), (W - 6, 42))
        rows = self._set_rows()
        n_rows = len(rows)
        # ── 内容区 ──
        pygame.draw.rect(surf, self.card(0),
                         pygame.Rect(6, 46, W - 12, H - 46 - 22),
                         border_radius=4)
        pygame.draw.rect(surf, border,
                         pygame.Rect(6, 46, W - 12, H - 46 - 22), 1,
                         border_radius=4)
        vis_n = min(n_rows, 6)
        start = max(0, min(self.set_sel - (vis_n - 1), max(0, n_rows - vis_n)))
        for i in range(vis_n):
            ri = start + i
            if ri >= n_rows:
                break
            rid, lab = rows[ri]
            sel = ri == self.set_sel
            y = 52 + i * 17
            if sel:
                pygame.draw.rect(surf, self.th("track"),
                                 pygame.Rect(10, y - 2, W - 20, 15),
                                 border_radius=3)
            text(surf, lab, 13, hi if sel else soft, (16, y))
            if rid.startswith("key:"):
                if rid == "key:cloud":         # 云地址不掩码 (可直接核对)
                    v = self.cfg.get("cloud_url", "")
                else:
                    v = self.cfg.get(
                        {"key:tok": "cloud_tok"}.get(rid, "cloud_url"), "")
                text(surf, clip_text((self._set_mask(v) if rid != "key:cloud"
                                      else v) or "未设置", 12, 110),
                     12, hi if v else dim, (W - 14, y), right=True)
                if sel:
                    text(surf, "输入", 10, acc, (W - 90, y), right=True)
            elif rid == "login":               # 登录行: 右侧显示当前用户
                user = self.cfg.get("cloud_user") or ""
                tok = self.cfg.get("cloud_tok") or ""
                text(surf, ("@%s" % user) if tok else "未登录", 12,
                     hi if tok else dim, (W - 14, y), right=True)
                if sel:
                    text(surf, "填 TOKEN", 10, acc, (W - 96, y), right=True)
            elif rid.startswith("sw:"):
                ck = rid.split(":", 1)[1]
                on = self._card_on(ck)
                pill = pygame.Rect(W - 52, y - 2, 36, 15)
                pygame.draw.rect(surf,
                                 acc if on else self.th("track"), pill,
                                 border_radius=8)
                text(surf, "ON" if on else "OFF", 10,
                     self.th("hi") if on else dim,
                     (pill.centerx, y), right=True)
            elif rid.startswith("theme:"):
                ti = int(rid.split(":", 1)[1])
                dot = hex2rgb(THEMES[ti]["hero"])   # 预览色 = 主题底色观感
                pygame.draw.circle(surf, dot, (W - 40, y + 6), 5)
                if self.theme_idx == ti:
                    text(surf, "✓", 12, hi, (W - 16, y), right=True)
        # ── KEY 输入弹窗 (覆盖) ──
        if self.set_edit is not None:
            lab = {"cloud": "输入云地址 URL",
                   "tok": "输入云 TOKEN"}.get(self.set_edit, "输入")
            box = pygame.Rect(30, 50, W - 60, 64)
            pygame.draw.rect(surf, self.card(0), box, border_radius=6)
            pygame.draw.rect(surf, acc, box, 2, border_radius=6)
            text(surf, lab, 13, hi, (box.x + 12, box.y + 8))
            text(surf, clip_text(self.set_buf[-28:] + "_", 13, 210), 13,
                 soft, (box.x + 12, box.y + 30))   # 尾窗 28 字符防溢出
            text(surf, "Enter 保存 · Esc 取消", 10, dim,
                 (box.x + 12, box.y + 46))
        # ── 注册表单 (覆盖): 用户名 / 邀请码 / 密码(可空) 三字段 ──
        if self.reg is not None:
            box = pygame.Rect(24, 44, W - 48, 112)
            pygame.draw.rect(surf, self.card(0), box, border_radius=6)
            pygame.draw.rect(surf, acc, box, 2, border_radius=6)
            text(surf, "注册 Oneapp 账号", 13, hi, (box.x + 12, box.y + 8))
            for i, (lab2, key2) in enumerate((("用户名", "name"),
                                              ("邀请码", "invite"),
                                              ("密码(可空)", "password"))):
                y2 = box.y + 30 + i * 18
                cur = self.reg["field"] == i
                text(surf, lab2, 12, acc if cur else dim, (box.x + 12, y2))
                val = self.reg[key2]
                shown = clip_text(val[-20:] + ("_" if cur else ""), 12, 146)
                text(surf, shown, 12, hi if val else dim,
                     (box.x + 74, y2))
            text(surf, "↑↓ 换行 · Enter 出码注册 · Esc 取消", 10, dim,
                 (box.x + 12, box.y + 86))
            if self.reg_busy:
                text(surf, "出码中…", 12, acc,
                     (box.right - 12, box.y + 86), right=True)
            elif self.osd and "失败" in self.osd[0]:
                text(surf, clip_text(self.osd[0], 10, 130), 10,
                     self.th("danger"), (box.right - 12, box.y + 87),
                     right=True)
        # ── 登录表单 (覆盖): 用户名+密码 或 TOKEN / 换账户 ──
        if self.login is not None:
            box = pygame.Rect(24, 44, W - 48, 112)
            pygame.draw.rect(surf, self.card(0), box, border_radius=6)
            pygame.draw.rect(surf, acc, box, 2, border_radius=6)
            cur = self.cfg.get("cloud_user") or ""
            title = ("更换账户 (当前 @%s)" % cur) \
                if self.cfg.get("cloud_tok") else "登录 Oneapp 账户"
            text(surf, title, 13, hi, (box.x + 12, box.y + 8))
            for i, (lab2, key2) in enumerate((("用户名", "user"),
                                              ("密码", "pw"),
                                              ("TOKEN", "tok"))):
                y2 = box.y + 30 + i * 18
                cur2 = self.login["field"] == i
                text(surf, lab2, 12, acc if cur2 else dim, (box.x + 12, y2))
                val = self.login[key2]
                shown = clip_text(val[-20:] + ("_" if cur2 else ""), 12, 146)
                text(surf, shown, 12, hi if val else dim,
                     (box.x + 74, y2))
            text(surf, "↑↓ 换行 · Enter 登录 · Esc 取消", 10, dim,
                 (box.x + 12, box.y + 86))
            if self.login_busy:
                text(surf, "验证中…", 12, acc,
                     (box.right - 12, box.y + 86), right=True)
            elif self.osd and "失败" in self.osd[0]:
                text(surf, clip_text(self.osd[0], 10, 130), 10,
                     self.th("danger"), (box.right - 12, box.y + 87),
                     right=True)
        # ── kv 云同步确认框 (覆盖): 本地 vs 云端 ──
        if self.kv_confirm is not None:
            d = self.kv_confirm
            box = pygame.Rect(28, 40, W - 56, 92)
            pygame.draw.rect(surf, self.card(0), box, border_radius=6)
            pygame.draw.rect(surf, acc, box, 2, border_radius=6)
            text(surf, "同步 %s" % KV_LABEL.get(d["k"], d["k"]), 13, hi,
                 (box.x + 12, box.y + 8))
            text(surf, "本地 %d 条 · 云端 %d 条" % (d["n"], d["cn"]), 12, hi,
                 (box.x + 12, box.y + 30))
            text(surf, "Enter 上传本地到云端", 11, acc,
                 (box.x + 12, box.y + 54))
            text(surf, "Esc 用云端覆盖本地", 11, soft,
                 (box.x + 12, box.y + 70))
        # ── 扫码登录/注册 (覆盖): QR 直绘 + 状态行, Esc/q 取消 ──
        if self.qrlogin is not None:
            stt = self.qrlogin.get("st")
            isreg = self.qrlogin.get("mode") == "reg"
            iswx = self.qrlogin.get("mode") == "wxbind"
            box = pygame.Rect(78, 20, W - 156, H - 28)
            pygame.draw.rect(surf, self.card(0), box, border_radius=6)
            pygame.draw.rect(surf, acc, box, 2, border_radius=6)
            title = ("扫码绑微信" if iswx else
                     "扫码注册·绑微信" if isreg else "扫码登录")
            text(surf, title, 13, hi, (box.x + 12, box.y + 6))
            mat = self.qrlogin.get("mat")
            if mat:
                n = len(mat)
                px = min(118 // n, 4)
                ox = box.x + (box.w - n * px) // 2
                oy = box.y + 24
                for my in range(n):
                    for mx in range(n):
                        if mat[my][mx]:
                            pygame.draw.rect(surf, hi,
                                             (ox + mx * px, oy + my * px,
                                              px, px))
            tip = {"init": "出码中…",
                   "qr": ("微信扫码 绑定到当前账号" if iswx else
                          "微信扫码 即注册并绑定本微信" if isreg else
                          "手机扫码 → 网页登录即授权"),
                   "wait": ("已扫码? 自动绑定微信" if iswx else
                            "已扫码? 自动注册并绑定微信" if isreg else
                            "已扫码? 网页确认后自动登录"),
                   "fail": "失败: %s" % self.qrlogin.get("err", "")[:20],
                   }.get(stt, "")
            textc(surf, clip_text(tip, 10, box.w - 20), 10,
                  acc if stt == "wait" else soft, box.centerx,
                  box.bottom - 22)
            text(surf, "Esc 取消", 10, dim,
                 (box.right - 12, box.bottom - 12), right=True)

    def tool_open(self, tid):
        self.tool = tid
        self.cv_armed = False
        self.osd = None
        if tid == "music":
            self.esc_hold_at = None           # 进 app 不残留长按态
            if self.mu_playing is None:
                self.mu_session_restore()     # 空态 → 恢复上次会话 (暂停在位)
            self.mu_view = "play"             # 主页=播放页 (空态显示无标题, 9 选源)
        if tid == "tv":
            self.tv_restore()                 # 恢复上次分组/光标
            self.kv_pull("tv_fav")            # 电视收藏云端拉取 (电台同款兜底)
        if tid == "sched":
            self.sched_refresh(False)         # 进卡拉取 (TTL 300s 节流)

    def tool_grid_key(self, k):
        """入口网格: 方向键移光标 / 数字 1-9,0 直开 / Enter 打开."""
        n = len(TOOL_APPS)
        if k in (pygame.K_LEFT, pygame.K_UP, pygame.K_RIGHT, pygame.K_DOWN):
            d = -1 if k in (pygame.K_LEFT, pygame.K_UP) else 1
            if k in (pygame.K_UP, pygame.K_DOWN):
                d *= TOOL_GRID
            self.tsel = max(0, min(n - 1, self.tsel + d))
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.tool_open(TOOL_APPS[self.tsel][0])
        elif k == pygame.K_0:
            if n > 9:                       # 第 10 个 app 才占 0 键
                self.tool_open(TOOL_APPS[9][0])
        elif pygame.K_1 <= k <= pygame.K_9:
            if k - pygame.K_1 < n:
                self.tool_open(TOOL_APPS[k - pygame.K_1][0])

    def tool_close(self):
        if self.tool == "music" and self.mu_view == "qr":
            self.mu_qr.stop()                 # 扫码轮询线程停 (绑定状态留存)
        if self.tool == "tv":
            self.tv_stop()                    # 播放中关 app = 停播
        self.tool = None
        self.osd = None

    def tool_head(self, surf, title, hint, dy=0, hfs=9):
        text(surf, title, 14, self.th("hi"), (6, 2 + dy))
        if hint:
            text(surf, hint, hfs, self.th("dim"), (W - 6, 5 + dy), right=True)

    def tool_foot(self, surf, labels):
        hx = 6
        for lab in labels:
            text(surf, lab, 12, self.th("soft"), (hx, 151))
            hx += textw(lab, 12) + textw("我", 12)

    def tool_key(self, k, ch):
        t = self.tool
        if t in ("fx", "unit"):
            return self.cv_key(k, ch, t == "fx")
        if t == "cal":
            return self.cal_key(k)
        if t == "lot":
            return self.lot_key(k)
        if t == "timer":
            return self.timer_key(k)
        if t == "music":
            return self.mu_key(k, ch)
        if t == "mail":
            return self.mail_key(k, ch)
        if t == "tv":
            return self.tv_key(k)
        if t == "sched":
            return self.sched_key(k)
        if t == "set":
            return self.set_key(k, ch)
        return True

    # ── 邮件 (工具第 7 卡; IMAP 授权码直连, 协议层 mail.py, net.job 后台跑) ──
    # ── 日程 (工具第 9 卡: 提醒+行程 只读展示 — 数据仅由 iPhone/Mac 提交器
    #    写入 noteserver /v1/sched-sync, 本端零管理 2026-11-17 用户定版) ──
    def _sched_items(self):
        """提醒+行程合并按时间升序 (渲染同一条排序口径)."""
        out = []
        for r in self.sched.get("reminds", []):
            out.append({"kind": "remind", "id": r.get("id") or "",
                        "text": r.get("text") or "", "t": r.get("due") or 0,
                        "tend": 0, "loc": ""})
        for r in self.sched.get("trips", []):
            out.append({"kind": "trip", "id": r.get("id") or "",
                        "text": r.get("text") or "", "t": r.get("due") or 0,
                        "tend": r.get("tend") or 0, "loc": r.get("loc") or ""})
        out.sort(key=lambda x: x["t"])
        return out

    def sched_refresh(self, force):
        url, tok = self._cloud_cfg()
        if not (url and tok):
            return
        if force:
            self.net.invalidate("schedget")    # 强刷 = 弹 TTL 缓存重跑
        self.net.job("schedget", self._sched_fetch_fn, ttl=300)

    def _sched_fetch_fn(self):
        """worker: GET /v1/sched → {"ok",now,reminds,trips} | {"ok":0}."""
        url, tok = self._cloud_cfg()
        if not (url and tok):
            return {"skip": 1}
        st, body = netmod.http_raw(
            url + "/v1/sched",
            headers={"Authorization": "Bearer " + tok})
        if st != 200 or not body:
            return {"ok": 0, "st": st}
        try:
            j = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            return {"ok": 0, "st": st}
        return {"ok": 1, "now": j.get("now") or time.time(),
                "reminds": j.get("reminds") or [],
                "trips": j.get("trips") or []}

    def sched_key(self, k):
        if k == pygame.K_4:                    # 只读卡: 唯一动作 = 手动刷新
            self.sched_refresh(True)
            self.osd = ("刷新中", time.monotonic() + 1.0)
        return True

    def draw_sched(self, surf):
        hi, soft, dim = self.th("hi"), self.th("soft"), self.th("dim")
        acc = self.th("accent")
        items = self._sched_items()
        logged = bool(self._cloud_cfg()[1])
        text(surf, "日程", 14, hi, (6, 3))
        text(surf, "\u00b7 %d \u6761" % len(items), 10, dim,
             (6 + textw("\u65e5\u7a0b", 14) + 6, 5))
        head = ("@%s" % (self.cfg.get("cloud_user") or "")) if logged \
            else "\u672a\u767b\u5f55"
        text(surf, head, 10, dim, (W - 6, 5), right=True)
        if not logged:
            textc(surf, "\u672a\u767b\u5f55 \u00b7 \u8bbe\u7f6e\u91cc\u914d\u7f6e\u60f3\u6cd5\u4e91\u540e\u53ef\u7528",
                  14, dim, W // 2, 62)
            textc(surf, "\u8bbe\u7f6e \u00b7 \u767b\u5f55 \u6216 \u6ce8\u518c\u65b0\u8d26\u53f7",
                  12, acc, W // 2, 82)
            self.tool_foot(surf, ["4 \u5237\u65b0", "Esc \u8fd4\u56de"])
            return
        now = time.time()
        if not items:
            textc(surf, "\u6682\u65e0\u63d0\u9192 / \u884c\u7a0b", 14, dim, W // 2, 60)
            textc(surf, "iPhone/Mac \u63d0\u4ea4\u5668\u540c\u6b65\u540e \u00b7 \u63094 \u5237\u65b0",
                  12, acc, W // 2, 80)
        else:
            for i in range(min(6, len(items))):
                it = items[i]
                y = 20 + i * 20
                t = it["t"]
                lt = time.localtime(t or now)
                over = t and t < now and \
                    (it["kind"] == "remind" or (it["tend"] or t) < now)
                text(surf, "%02d-%02d %02d:%02d" % (lt.tm_mon, lt.tm_mday,
                                                    lt.tm_hour, lt.tm_min),
                     16, acc if over else dim, (8, y + 1))
                badge = "\u884c" if it["kind"] == "trip" else "\u63d0"
                text(surf, badge, 16,
                     acc if it["kind"] == "trip" else soft, (116, y + 1))
                label = it["text"] + ((" \u00b7 " + it["loc"]) if it["loc"]
                                      else "")
                text(surf, clip_text(label, 16, W - 148), 16,
                     soft, (140, y + 1))
        self.tool_foot(surf, ["4 \u5237\u65b0", "Esc \u8fd4\u56de"])

    def mail_esc(self):
        """Esc 逐层回退. 返回 True=已消化, False=账号层 (调用方回工具网格)."""
        lv = self.mail_lv
        if lv == "compose":                   # 同音乐搜索口径: 先退拼音串再退层
            if self.ime_buf:
                self.ime_clear()
                return True
            self.mail_lv = "detail" if self.mail_c_reply else "list"
            self.ime_clear()
        elif lv == "detail":
            self.mail_lv = "list"
        elif lv == "list":
            self.mail_lv = "folders"
        elif lv == "folders":
            self.mail_lv = "acct"
            self.mail_cur = None               # 退出账号上下文 (poll 停拉)
            self.mail_sel = min(self.mail_sel,               # 夹回账号行
                                max(0, len(self.mail.get("accounts", [])) - 1))
        elif lv == "login":
            self.mail_lv = "acct"
            self.mail_acct_buf = self.mail_auth_buf = ""
        else:
            return False
        return True

    def mail_open(self, acct):
        """账号层 Enter: 直进 全部未读 聚合视图 (用户常用 = 163 网页版
        的 未读邮件/红旗邮件 智能视图; IMAP 不暴露 → fetch_all 逐夹聚合).
        单文件夹: Esc → 文件夹层 (虚拟夹置顶) 或直接看列表归属."""
        em = (acct or {}).get("email", "")
        self.mail_cur = dict(acct)
        self.mail_folder = None
        self.mail_msgs = []
        self.mail_total = 0
        self.mail_got = False
        self.mail_page = 0
        self.mail_has_more = False
        self.mail_lv = "folders"               # 登录直进文件夹页 (用户常用入口)
        self.mail_folders = []
        self.mail_sel = self.mail_scroll = 0
        self.osd = ("连接中…", time.monotonic() + 3)
        self.net.invalidate("mail_folders:%s" % em)
        self.net.job("mail_folders:%s" % em, self._mail_folders_fn(), 600)

    def _mail_all_fn(self, which):
        cur = dict(self.mail_cur or {})

        def f():
            return mailmod.fetch_all(cur, which, 30)
        return f

    def mail_del(self, email):
        """dialog 确认后删账号 → 落 mail.json."""
        mailmod.remove_account(email)
        self.mail = mailmod.load_accounts()
        if self.mail_cur and self.mail_cur.get("email", "").lower() == \
                (email or "").lower():
            self.mail_cur = None
            self.mail_lv = "acct"
            self.mail_folders = []
        self.mail_sel = min(self.mail_sel,
                            len(self.mail.get("accounts", [])))
        self.osd = ("已删除 " + email, time.monotonic() + 2)

    # ── 邮件: 取数闭包 (worker 线程跑, 结果 on_mail_net 落地) ──
    def _mail_unread_fn(self):
        accs = [dict(it) for it in self.mail.get("accounts", [])]

        def f():
            out = {}
            for it in accs:
                r = mailmod.inbox_unread(it)
                if r.get("ok"):
                    out[r["email"]] = r.get("unread", 0)
            return {"ok": True, "map": out}
        return f

    def _mail_folders_fn(self):
        cur = self.mail_cur

        def f():
            return mailmod.list_folders(cur)
        return f

    def _mail_list_fn(self, page=0):
        cur, folder = self.mail_cur, self.mail_folder

        def f():
            r = mailmod.fetch_msgs(cur, folder, 10, page)
            if isinstance(r, dict):
                r["page"] = page
            return r
        return f

    def _mail_body_fn(self, uid, fld=None):
        cur, folder = self.mail_cur, fld or self.mail_folder

        def f():
            r = mailmod.fetch_body(cur, folder, uid)
            if isinstance(r, dict):
                r["fld"] = folder              # 回执对号 (聚合视图逐夹)
            return r
        return f

    def _mail_login_fn(self, email, auth):
        def f():
            r = mailmod.test_login({"email": email, "auth": auth})
            r["email"] = email
            r["auth"] = auth
            return r
        return f

    def _mail_act_fn(self, uid, action, fld=None):
        cur, folder = self.mail_cur, fld or self.mail_folder

        def f():
            r = mailmod.mail_action(cur, folder, uid, action)
            if isinstance(r, dict):
                r["fld"] = folder              # 回滚对号 (聚合视图逐夹)
            return r
        return f

    def poll_mail(self):
        """邮件各层按需拉数 (poll 每秒调, TTL/去重在 net 层)."""
        lv = self.mail_lv
        if lv == "acct" and self.mail.get("accounts"):
            self.net.job("mail_unread", self._mail_unread_fn(), 60)
        elif lv == "folders" and self.mail_cur:
            em = (self.mail_cur or {}).get("email", "")
            self.net.job("mail_folders:%s" % em,
                 self._mail_folders_fn(), 600)
            self.net.job("mail_all:%s:flagged" % em,
                 self._mail_all_fn("flagged"), 300)   # 红旗数 + 预热缓存
        elif lv == "list" and self.mail_cur and self.mail_folder:
            em = (self.mail_cur or {}).get("email", "")
            if self.mail_folder.startswith("@"):
                self.net.job("mail_all:%s:%s" % (em, self.mail_folder[1:]),
                     self._mail_all_fn(self.mail_folder[1:]), 300)
            elif not self.mail_page:            # 分页浏览时暂停轮询
                self.net.job("mail_list:%s" % em,
                     self._mail_list_fn(0), 180)

    def on_mail_net(self, tag, b):
        if tag == "mail_login":
            if b.get("ok"):
                mailmod.upsert_account(b.get("email", ""), b.get("auth", ""))
                self.mail = mailmod.load_accounts()
                acc = next((it for it in self.mail.get("accounts", [])
                            if it.get("email", "").lower() ==
                            b.get("email", "").lower()), None)
                self.mail_acct_buf = self.mail_auth_buf = ""
                if acc:
                    self.mail_open(acc)
                self.osd = ("已登录 · INBOX 未读 %d" % b.get("unread", 0),
                            time.monotonic() + 2.5)
            else:
                self.osd = (b.get("err") or "登录失败", time.monotonic() + 3)
        elif tag == "mail_unread":
            for em, n in (b.get("map") or {}).items():
                self.mail_unread[em] = n
        elif tag.startswith("mail_folders"):
            if b.get("ok"):
                # 虚拟聚合夹置顶 (163 网页版的 未读/红旗 智能视图 IMAP 不暴露,
                # fetch_all 逐夹聚合); 真实文件夹随后
                fs = b.get("folders") or []
                inbox = next((f for f in fs
                              if f["name"].upper() == "INBOX"), None)
                rest = [f for f in fs if f is not inbox]
                em = (self.mail_cur or {}).get("email", "")
                self.mail_unread_n[em] = sum(max(0, f.get("unread", 0))
                                             for f in fs)
                self.mail_folders = ([{"raw": "@unread", "name": "未读"},
                                      {"raw": "@flagged", "name": "红旗"}]
                                     + ([inbox] if inbox else []) + rest)
                self.mail_sel = min(self.mail_sel,
                                    max(0, len(self.mail_folders) - 1))
                self.osd = None
            else:
                self.osd = (b.get("err") or "获取失败", time.monotonic() + 3)
        elif tag.startswith("mail_list") or tag.startswith("mail_all"):
            if b.get("ok"):
                rows = b.get("msgs") or []
                if tag.startswith("mail_list"):   # 单夹列表: 盖章归属夹
                    fldn = mailmod._decode_imap_utf7(
                        (self.mail_folder or "").strip('"'))
                    for m in rows:
                        m.setdefault("fld", self.mail_folder)
                        m.setdefault("fldn", fldn)
                em = tag.split(":")[1] if tag.startswith("mail_all") else ""
                if em:
                    if ":unread" in tag:
                        self.mail_unread_n[em] = b.get("n_unseen", 0)
                    if ":flagged" in tag or ":unread" in tag:
                        self.mail_flag_n[em] = b.get("n_flagged", 0)
                if b.get("page", 0) > 0:      # 加载更多: 追加
                    self.mail_msgs = self.mail_msgs + rows
                else:
                    self.mail_msgs = rows
                self.mail_total = b.get("total", 0)
                self.mail_has_more = b.get("has_more", False)
                self.mail_err = ""
                self.mail_got = True
                self.mail_sel = min(self.mail_sel,
                                    max(0, len(self._mail_view_msgs()) - 1))
                self.osd = None
            else:
                self.mail_err = b.get("err") or "获取失败"
        elif tag.startswith("mail_body"):     # tag 带夹+uid, 快速换信互不阻塞
            if b.get("ok") and b.get("uid") == self.mail_body_uid \
                    and b.get("fld") == self.mail_body_fld:
                self.mail_body = b.get("text") or "(空邮件)"
                self.mail_c_meta = b          # 回复预填 + In-Reply-To 元数据
                self.mail_body_scroll = 0
            elif not b.get("ok"):
                self.mail_body = "获取失败: %s" % (b.get("err") or "")
        elif tag == "mail_send":              # 发送结果 (草稿失败时保留)
            self.mail_c_busy = False
            if b.get("ok"):
                self.mail_c = {"to": "", "subject": "", "body": ""}
                self.mail_c_quote = ""
                self.mail_c_meta = None
                self.mail_lv = "detail" if self.mail_c_reply else "list"
                self.mail_c_reply = False
                self.ime_clear()
                self.osd = ("已发送 ✓", time.monotonic() + 2.5)
            else:
                self.osd = ("发送失败: %s" % (b.get("err") or ""),
                            time.monotonic() + 3)
        elif tag.startswith("mail_act"):      # tag 带夹:uid:action
            if b.get("ok"):
                self.osd = ("已标记", time.monotonic() + 1.2)
            else:                             # 乐观更新回滚
                for m in self.mail_msgs:
                    if m.get("uid") == b.get("uid") \
                            and m.get("fld", self.mail_folder) \
                            == b.get("fld"):
                        if (b.get("action") or "").startswith("mark_"):
                            m["seen"] = not m.get("seen")
                        else:
                            m["flagged"] = not m.get("flagged")
                        break
                self.osd = ("标记失败 " + (b.get("err") or ""),
                            time.monotonic() + 2.5)
            em = (self.mail_cur or {}).get("email", "")   # seen/flag 已变,
            for t in ("mail_list:%s" % em, "mail_all:%s:unread" % em,   # 缓存
                      "mail_all:%s:flagged" % em):
                self.net.invalidate(t)

    def _mail_view_msgs(self):
        # 聚合视图 (全部未读/全部红旗) 保持口径; 单文件夹视图 = 最近全量
        if self.mail_folder.startswith("@"):
            return [m for m in self.mail_msgs
                    if (not m.get("seen")) or m.get("flagged")]
        return self.mail_msgs

    def _mail_rows(self):
        """当层可选行数 (acct/folders/list 通用)."""
        if self.mail_lv == "acct":
            return len(self.mail.get("accounts", [])) + 1   # + ＋添加账号
        if self.mail_lv == "folders":
            return max(1, len(self.mail_folders))
        if self.mail_lv == "list":
            return max(1, len(self._mail_view_msgs()))
        return 1

    def mail_key(self, k, ch):
        lv = self.mail_lv
        if lv == "login":
            return self.mail_login_key(k, ch)
        if lv == "compose":
            return self.mail_compose_key(k, ch)
        if k in (pygame.K_UP, pygame.K_DOWN):
            if lv == "detail":
                n = max(0, len(self._mail_body_lines()) - MAIL_BODY_ROWS)
                d = 1 if k == pygame.K_DOWN else -1
                self.mail_body_scroll = max(
                    0, min(n, self.mail_body_scroll + d))
            elif lv == "folders":          # 三列网格: ↑↓ 跨行 ±3
                d = 3 * (1 if k == pygame.K_DOWN else -1)
                self.mail_sel = max(0, min(self._mail_rows() - 1,
                                           self.mail_sel + d))
            else:
                d = 1 if k == pygame.K_DOWN else -1
                self.mail_sel = max(0, min(self._mail_rows() - 1,
                                           self.mail_sel + d))
            return True
        if lv == "folders" and k in (pygame.K_LEFT, pygame.K_RIGHT):
            d = 1 if k == pygame.K_RIGHT else -1     # ←→ 同行 ±1
            self.mail_sel = max(0, min(self._mail_rows() - 1,
                                       self.mail_sel + d))
            return True
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if lv == "acct":
                accs = self.mail.get("accounts", [])
                if self.mail_sel >= len(accs):
                    self.mail_lv = "login"    # ＋添加账号
                    self.mail_edit = 0
                else:
                    self.mail_open(accs[min(self.mail_sel, len(accs) - 1)])
            elif lv == "folders":
                if self.mail_folders:
                    f = self.mail_folders[min(self.mail_sel,
                                              len(self.mail_folders) - 1)]
                    self.mail_folder = f["raw"]
                    self.mail_msgs = []
                    self.mail_total = 0
                    self.mail_got = False
                    self.mail_page = 0
                    self.mail_has_more = False
                    self.mail_sel = self.mail_scroll = 0
                    self.mail_lv = "list"
                    em = (self.mail_cur or {}).get("email", "")
                    if f["raw"].startswith("@"):
                        w = f["raw"][1:]
                        self.net.invalidate("mail_all:%s:%s" % (em, w))
                        self.net.job("mail_all:%s:%s" % (em, w),
                                     self._mail_all_fn(w), 300)
                    else:
                        self.net.invalidate("mail_list:%s" % em)
                        self.net.job("mail_list:%s" % em,
                                     self._mail_list_fn(0), 180)
            elif lv == "list":
                msgs = self._mail_view_msgs()
                if msgs:
                    m = msgs[min(self.mail_sel, len(msgs) - 1)]
                    fld = m.get("fld") or self.mail_folder
                    self.mail_body = None
                    self.mail_body_uid = m["uid"]
                    self.mail_body_fld = fld
                    self.mail_body_scroll = 0
                    self.mail_lv = "detail"
                    self.net.job("mail_body:%s:%s" % (fld, m["uid"]),
                                 self._mail_body_fn(m["uid"], fld), 0)
            return True
        if lv == "acct" and k == pygame.K_d:
            accs = self.mail.get("accounts", [])
            if accs and self.mail_sel < len(accs):
                self.dialog = ("delmail", accs[self.mail_sel].get("email", ""))
            return True
        if lv == "list" and k == pygame.K_4:  # 强制刷新 (聚合/单夹分流)
            self.mail_got = False
            self.mail_page = 0
            self.mail_has_more = False
            em = (self.mail_cur or {}).get("email", "")
            if self.mail_folder.startswith("@"):
                w = self.mail_folder[1:]
                self.net.invalidate("mail_all:%s:%s" % (em, w))
                self.net.job("mail_all:%s:%s" % (em, w),
                             self._mail_all_fn(w), 300)
            else:
                self.net.invalidate("mail_list:%s" % em)
                self.net.job("mail_list:%s" % em,
                             self._mail_list_fn(), 180)
            self.osd = ("刷新中…", time.monotonic() + 2.5)
            return True
        if lv == "list" and k == pygame.K_5:  # 写新邮件
            self.mail_compose_open(reply=False)
            return True
        if lv == "list" and k == pygame.K_6 \
                and self.mail_folder.startswith("@"):
            msgs = self._mail_view_msgs()      # 聚合: 直接标已读
            if msgs:
                m = msgs[min(self.mail_sel, len(msgs) - 1)]
                fld = m.get("fld") or self.mail_folder
                em = (self.mail_cur or {}).get("email", "")
                m["seen"] = True
                self.mail_unread_n[em] = max(
                    0, self.mail_unread_n.get(em, 0) - 1)
                self.net.job(
                    "mail_act:%s:%s:mark_read" % (fld, m["uid"]),
                    self._mail_act_fn(m["uid"], "mark_read", fld), 0)
                self.osd = ("已读", time.monotonic() + 1.2)
            return True
        if lv == "list" and k == pygame.K_6 and self.mail_has_more \
                and not self.mail_folder.startswith("@"):
            em = (self.mail_cur or {}).get("email", "")
            self.mail_page += 1
            self.osd = ("加载更早邮件…", time.monotonic() + 3)
            self.net.job("mail_list:%s:p%d" % (em, self.mail_page),
                         self._mail_list_fn(self.mail_page), 180)
            return True
        if lv == "detail" and k == pygame.K_4:
            self.mail_mark("read")
            return True
        if lv == "detail" and k == pygame.K_5:
            self.mail_mark("flag")
            return True
        if lv == "detail" and k == pygame.K_6:  # 回复 (需已取到正文元数据)
            if not self.mail_c_meta:
                self.osd = ("正文未加载完", time.monotonic() + 1.5)
            else:
                self.mail_compose_open(reply=True)
            return True
        return True

    def mail_login_key(self, k, ch):
        """登录表单: 邮箱/授权码纯 ASCII 直输 (无 IME), Enter 下一项/提交."""
        if k in (pygame.K_LEFT, pygame.K_RIGHT):
            self.mail_edit = 1 - self.mail_edit
            return True
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if self.mail_edit == 0:
                addr = self.mail_acct_buf.strip()
                dom = addr.rsplit("@", 1)[-1] if "@" in addr else ""
                if "@" not in addr or "." not in dom:
                    self.osd = ("邮箱格式不对 (如 xx@163.com)",
                                time.monotonic() + 2)
                else:
                    self.mail_edit = 1
            else:
                self.mail_login_commit()
            return True
        buf = self.mail_acct_buf if self.mail_edit == 0 else self.mail_auth_buf
        if k == pygame.K_BACKSPACE:
            buf = buf[:-1]
        elif ch and 0x20 <= ord(ch) < 0x7F:
            buf = (buf + ch)[:64]
        if self.mail_edit == 0:
            self.mail_acct_buf = buf
        else:
            self.mail_auth_buf = buf
        return True

    def mail_login_commit(self):
        addr = self.mail_acct_buf.strip()
        auth = self.mail_auth_buf.strip()
        if not addr or not auth:
            self.osd = ("邮箱/授权码未填", time.monotonic() + 2)
            return
        self.osd = ("连接中…", time.monotonic() + 5)
        self.net.job("mail_login", self._mail_login_fn(addr, auth), 0)

    def mail_mark(self, which):
        """详情 4/5: 已读/未读 与 红/取消旗 (乐观更新, 失败回滚).
        按 uid 定位开着的信 — 标已读会把行从视图挤掉, 按索引会错位."""
        fld = self.mail_body_fld or self.mail_folder
        m = next((x for x in self.mail_msgs
                  if x.get("uid") == self.mail_body_uid
                  and x.get("fld", self.mail_folder) == fld), None)
        if not m:
            return
        if which == "read":
            action = "mark_unread" if m.get("seen") else "mark_read"
            m["seen"] = not m.get("seen")
        else:
            action = "unflag" if m.get("flagged") else "flag"
            m["flagged"] = not m.get("flagged")
        self.net.job("mail_act:%s:%s:%s" % (fld, m["uid"], action),
                     self._mail_act_fn(m["uid"], action, fld), 0)

    # ── 邮件: 写信/回复 (compose 层; 三字段 IME 输入, Enter 下一项/发送) ──
    def mail_compose_open(self, reply=False):
        """写信层入口: 列表 5=新邮件 (空白); 详情 6=回复 (预填收件人/
        Re: 主题/顶部引用原文, 引用截 500 字). 用户文字永远在顶部.
        默认英文直输 (首字段=收件人邮箱; / 切拼音)."""
        self.mail_c = {"to": "", "subject": "", "body": ""}
        self.mail_c_quote = ""
        self.mail_cf = 0
        self.mail_c_reply = reply
        self.mail_c_from = self.mail_lv        # 回跳锚 (list / detail)
        self.ime_mode = False                  # 英文优先, 提示见底栏/字段标
        self.ime_clear()
        if reply:
            meta = self.mail_c_meta or {}
            subj = re.sub(r"^(\s*(re|回复|aw|sv)\s*[:：]\s*)+", "",
                          meta.get("subject") or "", flags=re.IGNORECASE)
            self.mail_c["to"] = meta.get("from_addr") or ""
            self.mail_c["subject"] = ("Re: " + subj)[:64]
            q = (meta.get("text") or "").strip()
            if len(q) > 500:
                q = q[:500] + " …(截断)"
            quote = "\n".join("> " + ln for ln in q.splitlines())
            self.mail_c_quote = "在 %s，%s 写道：\n%s" % (
                meta.get("date") or "?",
                meta.get("from_addr") or "对方", quote)
        self.mail_lv = "compose"

    def mail_compose_key(self, k, ch):
        """写信层按键: ←→/↑↓ 切字段, / 中英, Enter 下一项/发送, IME 同想法页."""
        if k in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN):
            self.mail_cf = (self.mail_cf + (1 if k in (pygame.K_RIGHT,
                                                       pygame.K_DOWN)
                                            else -1)) % 3
            self.ime_clear()
            return True
        if k == pygame.K_SLASH:                # 中/英切换
            self.ime_mode = not self.ime_mode
            self.ime_clear()
            return True
        caps = (64, 64, 1000)                  # 收件人/主题/正文 上限
        if self.ime_mode:                      # 拼音 IME (想法页同款流程)
            if not self.ime_buf and k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.ime_clear()
                self.mail_compose_next()
                return True
            act = self.ime_key(k, ch)
            if act is None:
                return True
            tag, val = act
            f = ("to", "subject", "body")[self.mail_cf]
            if tag == "del":
                self.mail_c[f] = self.mail_c[f][:-1]
            elif tag == "submit":
                self.ime_clear()
                self.mail_compose_next()
            else:                              # char/word
                self.mail_c[f] = (self.mail_c[f] + val)[:caps[self.mail_cf]]
            return True
        # 英文直输
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.mail_compose_next()
        elif k == pygame.K_BACKSPACE:
            f = ("to", "subject", "body")[self.mail_cf]
            self.mail_c[f] = self.mail_c[f][:-1]
        elif ch and 0x20 <= ord(ch) < 0x7F:
            f = ("to", "subject", "body")[self.mail_cf]
            if len(self.mail_c[f]) < caps[self.mail_cf]:
                self.mail_c[f] += ch
        return True

    def mail_compose_next(self):
        """Enter: 收件人→主题→正文→发送."""
        if self.mail_cf < 2:
            self.mail_cf += 1
            return
        self.mail_compose_send()

    def mail_compose_send(self):
        """发送 (worker 跑 SMTP; 失败草稿保留在写信层)."""
        if self.mail_c_busy:
            return
        if "@" not in self.mail_c.get("to", ""):
            self.mail_cf = 0
            self.osd = ("收件人格式不对", time.monotonic() + 2)
            return
        self.mail_c_busy = True
        self.mail_c_busy_at = time.monotonic()
        self.osd = ("发送中…", time.monotonic() + 10)
        self.net.job("mail_send", self._mail_send_fn(), 0)

    def _mail_send_fn(self):
        cur = dict(self.mail_cur or {})
        fields = dict(self.mail_c)
        body = fields.get("body", "").rstrip()
        if self.mail_c_quote:                  # 引用块拼在用户文字之后
            body = (body + "\n\n" + self.mail_c_quote).strip()
        reply = self.mail_c_reply
        meta = self.mail_c_meta or {}

        def f():
            return mailmod.send_mail(cur, fields.get("to", ""),
                                     fields.get("subject", ""), body,
                                     in_reply_to=meta.get("msg_id", "")
                                     if reply else "",
                                     references=meta.get("refs", "")
                                     if reply else "")
        return f

    def _mail_c_lines(self):
        """写信正文折行 → (lines, 用户行数). 用户文字亮色 + 引用块暗色,
        缓存同详情口径 (正文/引用/屏宽不变则复用)."""
        key = (self.mail_c.get("body", ""), self.mail_c_quote, W)
        if self._mail_c_cache[0] != key:
            lines = []
            for para in (self.mail_c.get("body", "") + "\n_").splitlines()[:-1]:
                w = self._wrap_lines(para, 12, W - 30, 80)
                lines.extend(w if w else [""])
            n_user = len(lines)
            if self.mail_c_quote:
                for para in self.mail_c_quote.splitlines():
                    lines.extend(self._wrap_lines(para, 12, W - 30, 80))
            self._mail_c_cache = (key, lines[:300], n_user)
        return self._mail_c_cache[1], min(self._mail_c_cache[2],
                                          len(self._mail_c_cache[1]))

    def _mail_body_lines(self):
        """详情正文折行 (缓存: 正文或屏宽不变则复用)."""
        key = (self.mail_body or "", W)
        if self._mail_body_cache[0] != key:
            lines = []
            for para in (self.mail_body or "获取中…").splitlines():
                w = self._wrap_lines(para, 13, W - 14, 80)
                lines.extend(w if w else [""])
            self._mail_body_cache = (key, lines[:300])
        return self._mail_body_cache[1]

    # ── 邮件: 绘制 (六层) ──
    def draw_mail(self, surf):
        {"acct": self.draw_mail_acct, "login": self.draw_mail_login,
         "folders": self.draw_mail_folders, "list": self.draw_mail_list,
         "detail": self.draw_mail_detail,
         "compose": self.draw_mail_compose}[self.mail_lv](surf)

    def draw_mail_acct(self, surf):
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        accs = self.mail.get("accounts", [])
        self.tool_head(surf, "邮件 · 账号", "%d 个" % len(accs))
        rows = [it.get("email", "") for it in accs]
        rows.append("＋ 添加账号")
        if not accs:
            textc(surf, "163 邮箱 + 授权码登录", 13, dim, W // 2, 56)
            textc(surf, "授权码: 网页版 设置 → POP3/SMTP/IMAP 生成", 9, dim,
                  W // 2, 76)
        start = max(0, min(self.mail_sel - 2, len(rows) - 4))
        for i, a in enumerate(rows[start:start + 4], start):
            y = 18 + (i - start) * 31
            r = pygame.Rect(6, y, W - 12, 27)
            sel = i == self.mail_sel
            add = i == len(rows) - 1
            pygame.draw.rect(surf, self.card(0), r, border_radius=5)
            pygame.draw.rect(surf, acc if sel else self.th("border"), r,
                             2 if sel else 1, border_radius=5)
            text(surf, clip_text(a, 13, W - 110), 13,
                 acc if add else (hi if sel else soft), (14, y + 5))
            un = self.mail_unread.get(a)
            if un:
                text(surf, "未读 %d" % un, 11, acc, (W - 14, y + 7),
                     right=True)
        self.tool_foot(surf, ["Enter 打开", "d 删除", "Esc 退出"])

    def draw_mail_login(self, surf):
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        self.tool_head(surf, "邮件 · 添加账号", "英文直输")
        fields = [("邮箱", self.mail_acct_buf, "you@163.com", False),
                  ("授权码", self.mail_auth_buf, "网页版开启 IMAP 后生成", True)]
        y = 20
        for i, (lab, val, ph, mask) in enumerate(fields):
            sel = i == self.mail_edit
            r = pygame.Rect(6, y, W - 12, 32)
            pygame.draw.rect(surf, self.card(0), r, border_radius=5)
            pygame.draw.rect(surf, acc if sel else self.th("border"), r,
                             2 if sel else 1, border_radius=5)
            text(surf, lab, 12, acc if sel else dim, (14, y + 8))
            show = "●" * len(val) if mask else val
            cur = show + ("_" if sel and int(time.monotonic() * 2) % 2 == 0
                          else "")
            text(surf, cur or ph, 13, hi if val else dim, (62, y + 8))
            y += 38
        dom = self.mail_acct_buf.rsplit("@", 1)[-1].lower() \
            if "@" in self.mail_acct_buf else ""
        if dom.endswith("qq.com") or dom.endswith("foxmail.com"):
            hint = "QQ: 网页版 设置 → 账号 → POP3/IMAP/SMTP 服务 → 生成授权码"
        else:
            hint = "163: 网页版 设置 → POP3/SMTP/IMAP → 开启并生成授权码"
        textc(surf, hint, 9, dim, W // 2, 102)
        textc(surf, "Enter 下一项 / 登录 · ←→ 切字段 · Esc 返回", 10, soft,
              W // 2, 118)
        self.tool_foot(surf, ["Enter 确认", "Esc 返回"])

    def draw_mail_folders(self, surf):
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        em = (self.mail_cur or {}).get("email", "")
        self.tool_head(surf, "邮件 · " + clip_text(em, 11, 150),
                       "%d 夹" % len(self.mail_folders))
        nfol = len(self.mail_folders)
        cols = 3                              # 三列网格 (丝印←→↑↓/真方向)
        rows_n = (nfol + cols - 1) // cols
        vis = 4                               # 每屏 4 行
        if not nfol:
            textc(surf, "连接中…", 12, dim, W // 2, 70)
        start_row = max(0, min(self.mail_sel // cols - (vis - 1),
                               rows_n - vis))
        cw = (W - 12) // cols
        for r in range(vis):
            rowi = start_row + r
            base = rowi * cols
            if base >= nfol:
                break
            for c in range(cols):
                idx = base + c
                if idx >= nfol:
                    break
                f = self.mail_folders[idx]
                nm = f["name"]
                if f["raw"] == "@unread":
                    cnt = self.mail_unread_n.get(em, 0)
                    nm = ("未读 %d" % cnt) if cnt else "未读"
                elif f["raw"] == "@flagged":
                    cnt = self.mail_flag_n.get(em, 0)
                    nm = ("红旗 %d" % cnt) if cnt else "红旗"
                x = 6 + c * cw
                y = 18 + r * 30
                cell = pygame.Rect(x + 1, y, cw - 2, 26)
                sel = idx == self.mail_sel
                pygame.draw.rect(surf, self.card(0), cell, border_radius=4)
                pygame.draw.rect(surf, acc if sel else self.th("border"),
                                 cell, 2 if sel else 1, border_radius=4)
                text(surf, clip_text(nm, 13, cw - 12), 13,
                     hi if sel else soft, (x + 6, y + 5))
                if f.get("unread", 0) > 0:
                    text(surf, str(f["unread"]), 13, acc,
                         (x + cw - 6, y + 5), right=True)
        self.tool_foot(surf, ["←→↑↓ 选夹", "Enter 打开", "Esc 返回"])

    def draw_mail_list(self, surf):
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        if (self.mail_folder or "").startswith("@"):
            disp = {"unread": "全部未读",
                    "flagged": "全部红旗"}.get(self.mail_folder[1:],
                                               self.mail_folder[1:])
        else:
            disp = mailmod._decode_imap_utf7(
                (self.mail_folder or "").strip('"'))
        self.tool_head(surf, "邮件 · %s" % clip_text(disp, 11, 130),
                       "%d/%d 封" % (len(self._mail_view_msgs()),
                                     self.mail_total))
        msgs = self._mail_view_msgs()
        if not msgs:
            if (self.mail_folder or "").startswith("@"):
                empty = "无未读/旗标邮件"
            else:
                empty = "无邮件"
            msg = self.mail_err or (empty if self.mail_got
                                    else ("搜索中…" if self.net.mode != "offline"
                                          else "离线 · 联网后同步"))
            textc(surf, msg, 12, dim, W // 2, 70)
        start = max(0, min(self.mail_sel - 1, len(msgs) - 3))
        for i, m in enumerate(msgs[start:start + 3], start):
            y = 16 + (i - start) * 45
            r = pygame.Rect(6, y, W - 12, 43)
            sel = i == self.mail_sel
            unread = not m.get("seen")
            pygame.draw.rect(surf, self.card(0), r, border_radius=5)
            pygame.draw.rect(surf, acc if sel else self.th("border"), r,
                             2 if sel else 1, border_radius=5)
            text(surf, clip_text(m.get("from", ""), 13, W - 100), 13,
                 hi if sel else soft, (14, y + 3))
            text(surf, m.get("date", ""), 10, dim, (W - 16, y + 5), right=True)
            text(surf, clip_text(m.get("subject", ""), 13, W - 60), 13,
                 hi if unread else dim, (14, y + 17))
            if m.get("flagged"):
                pygame.draw.polygon(surf, acc, [(W - 22, y + 18),
                                                (W - 14, y + 21),
                                                (W - 22, y + 24)])
            if unread:
                pygame.draw.circle(surf, acc, (W - 14, y + 22), 3)
            pv = m.get("preview", "")
            if m.get("fldn") and m.get("fld") != self.mail_folder:
                pv = "[%s] %s" % (m["fldn"], pv)   # 聚合视图标注来源夹
            text(surf, clip_text(pv, 13, W - 28), 13,
                 dim, (14, y + 30))
        foot = ["4 刷新", "5 写邮件"]
        if (self.mail_folder or "").startswith("@"):
            foot.append("6 已读")
        elif self.mail_has_more:
            foot.append("6 更多")
        foot += ["Enter 读", "Esc 返回"]
        self.tool_foot(surf, foot)

    def draw_mail_detail(self, surf):
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        # 按 uid 定位 (与 mail_mark 同口径): 标已读挤掉行后详情不错位
        m = next((x for x in self.mail_msgs
                  if x.get("uid") == self.mail_body_uid), None)
        if not m:                              # 刷新后列表已无此信 → 收起
            self.mail_lv = "list"
            return
        self.tool_head(surf, clip_text(m.get("from", ""), 11, 200),
                       m.get("date", ""))
        y = 16
        for ln in self._wrap_lines(m.get("subject", ""), 13, W - 12, 2):
            text(surf, ln, 13, hi, (6, y))
            y += 16
        pygame.draw.line(surf, self.th("border"), (6, y + 1), (W - 6, y + 1))
        by = y + 6
        lines = self._mail_body_lines()
        mx = max(0, len(lines) - MAIL_BODY_ROWS)
        self.mail_body_scroll = min(self.mail_body_scroll, mx)
        surf.set_clip((0, by, W, 148 - by))
        for i, ln in enumerate(lines[self.mail_body_scroll:
                                     self.mail_body_scroll + MAIL_BODY_ROWS]):
            text(surf, ln, 13, soft, (6, by + i * 16))
        surf.set_clip(None)
        if self.mail_body_scroll > 0:          # 右上/右下滚动指示 (新闻详情同款)
            pygame.draw.polygon(surf, dim, [(W - 11, by + 4), (W - 4, by + 4),
                                            (W - 8, by + 1)])
        if self.mail_body_scroll < mx:
            pygame.draw.polygon(surf, dim, [(W - 11, 146), (W - 4, 146),
                                            (W - 8, 149)])
        self.tool_foot(surf, ["4 %s" % ("标未读" if m.get("seen") else "标已读"),
                              "5 %s" % ("取旗" if m.get("flagged") else "红旗"),
                              "6 回复", "Esc 返回"])

    def draw_mail_compose(self, surf):
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        self.tool_head(surf, "邮件 · %s" % ("回复" if self.mail_c_reply
                                            else "写邮件"),
                       clip_text((self.mail_cur or {}).get("email", ""),
                                 9, 90))
        fields = [("收件人", self.mail_c.get("to", ""), "对方邮箱 addr@xx.com"),
                  ("主题", self.mail_c.get("subject", ""), "(可空)")]
        y = 18
        for i, (lab, val, ph) in enumerate(fields):
            sel = self.mail_cf == i
            r = pygame.Rect(6, y, W - 12, 26)
            pygame.draw.rect(surf, self.card(0), r, border_radius=5)
            pygame.draw.rect(surf, acc if sel else self.th("border"), r,
                             2 if sel else 1, border_radius=5)
            text(surf, lab, 11, acc if sel else dim, (14, y + 7))
            cur = val + ("_" if sel and int(time.monotonic() * 2) % 2 == 0
                         else "")
            text(surf, clip_text(cur, 12, W - 100), 12,
                 hi if val else dim, (62, y + 7))
            if sel:                           # 输入法模式标 (拼/EN)
                text(surf, "拼" if self.ime_mode else "EN", 10, acc,
                     (W - 14, y + 8), right=True)
            y += 32
        # 正文框: 用户文字亮色 (输入端永远在缓冲区尾), 引用块暗色只读
        sel = self.mail_cf == 2
        r = pygame.Rect(6, y, W - 12, 64)
        pygame.draw.rect(surf, self.card(0), r, border_radius=5)
        pygame.draw.rect(surf, acc if sel else self.th("border"), r,
                         2 if sel else 1, border_radius=5)
        lines, n_user = self._mail_c_lines()
        if not lines:
            text(surf, "正文 (可空) · Enter 发送", 11, dim, (14, y + 7))
        start = max(0, min(n_user - 4, len(lines) - 4))   # 输入端可见
        surf.set_clip((8, y + 2, W - 16, 60))
        for i, ln in enumerate(lines[start:start + 4]):
            is_q = (start + i) >= n_user
            text(surf, ln, 12, dim if is_q else soft, (14, y + 5 + i * 15))
        surf.set_clip(None)
        if not (self.ime_bar(surf, 146)):      # 候选条在时让位底栏
            self.tool_foot(surf, ["Enter 下一项/发送", "/ 中英", "Esc 返回"])

    # ── 汇率 / 单位换算 (共用字段模型: 0 金额 1 源 2 目标 3 类别) ──
    def cv_key(self, k, ch, is_fx):
        n = len(FX_CODES) if is_fx else len(UCATS)
        if k in (pygame.K_LEFT, pygame.K_RIGHT):
            d = -1 if k == pygame.K_LEFT else 1
            mx = 2 if is_fx else 3              # fx 无类别字段
            self.cv_field = (self.cv_field + d) % (mx + 1)
        elif k in (pygame.K_UP, pygame.K_DOWN):
            d = 1 if k == pygame.K_UP else -1    # UP 增加 / DOWN 减少
            f = self.cv_field
            if f == 0:
                self._cv_amt_bump(d, is_fx)
            elif f == 1:
                self._cv_set(is_fx, "from", (self._cv_get(is_fx, "from") + d) % n)
            elif f == 2:
                self._cv_set(is_fx, "to", (self._cv_get(is_fx, "to") + d) % n)
            elif not is_fx:
                self.u_cat = (self.u_cat + d) % len(UCATS)
                nu = len(UCATS[self.u_cat][1])
                self.u_from = min(self.u_from, nu - 1)
                self.u_to = min(self.u_to, nu - 1)
        elif k == pygame.K_e:
            a = self._cv_get(is_fx, "from")
            self._cv_set(is_fx, "from", self._cv_get(is_fx, "to"))
            self._cv_set(is_fx, "to", a)
        elif not is_fx and k == pygame.K_r:
            self.u_cat = (self.u_cat + 1) % len(UCATS)
            nu = len(UCATS[self.u_cat][1])
            self.u_from = min(self.u_from, nu - 1)
            self.u_to = min(self.u_to, nu - 1)
        elif ch and (ch.isdigit() or ch == "."):
            self._cv_amt_char(ch, is_fx)
        elif k == pygame.K_BACKSPACE:
            self._cv_amt_char(None, is_fx)
        return True

    def _cv_get(self, is_fx, which):
        return (self.fx_from if which == "from" else self.fx_to) if is_fx \
            else (self.u_from if which == "from" else self.u_to)

    def _cv_set(self, is_fx, which, v):
        if is_fx:
            if which == "from":
                self.fx_from = v
            else:
                self.fx_to = v
        elif which == "from":
            self.u_from = v
        else:
            self.u_to = v

    def _cv_amt_attr(self, is_fx):
        return "fx_amt" if is_fx else "u_amt"

    def _cv_amt_char(self, ch, is_fx):
        attr = self._cv_amt_attr(is_fx)
        v = getattr(self, attr)
        if ch is None:                        # backspace
            v = v[:-1]
            if not v:
                self.cv_armed = False
        elif not self.cv_armed:               # 首键: 覆盖默认值
            v = "0." + ch if ch == "." else ch
            self.cv_armed = True
        elif ch == ".":
            if "." not in v:
                v += "."
                self.cv_armed = True
        elif len(v.replace(".", "")) < 10:
            v += ch
        setattr(self, attr, v if v not in ("", ".") else "0")

    def _cv_amt_bump(self, d, is_fx):
        attr = self._cv_amt_attr(is_fx)
        try:
            v = float(getattr(self, attr))
        except ValueError:
            v = 0.0
        v = max(0.0, round(v + d, 2))
        setattr(self, attr, fmt_num(v).replace(",", ""))

    # ── 健康 ──
    def cal_key(self, k):
        if k in (pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT):
            y, m = self._cal_ym()
            if k in (pygame.K_UP, pygame.K_DOWN):   # ↑↓ 换年
                y += -1 if k == pygame.K_UP else 1
            else:                                    # ←→ 换月 (跨年进退)
                m += -1 if k == pygame.K_LEFT else 1
                if m == 0:
                    y, m = y - 1, 12
                if m == 13:
                    y, m = y + 1, 1
            self.cal_ym = (y, m)
        elif k == pygame.K_n:                        # n 回今天
            t = time.localtime()
            self.cal_ym = (t.tm_year, t.tm_mon)
        return True

    # ── 彩票 (双色球/大乐透, ←→ 换彩种) ──
    LOT_RED, LOT_BLUE = (224, 32, 32), (42, 107, 224)
    LOT_TYPES = (("ssq", "双色球"), ("dlt", "大乐透"))

    def lot_key(self, k):
        if k in (pygame.K_LEFT, pygame.K_RIGHT):
            d = 1 if k == pygame.K_RIGHT else -1
            self.lot_type = (self.lot_type + d) % len(self.LOT_TYPES)
            self.lot_scroll = self.lot_view = 0     # 换彩种光标归零 (回最新期)
        elif k in (pygame.K_UP, pygame.K_DOWN):
            key = self.LOT_TYPES[self.lot_type][0]
            n = len((self.data.get(key) or {}).get("issues") or [])
            d = -1 if k == pygame.K_UP else 1
            self.lot_scroll = max(0, min(n - 1, self.lot_scroll + d))
        return True

    def timer_key(self, k):
        if self.tm_run:
            if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.tm_run = False          # 暂停
                self.tm_left = max(0.0, self.tm_end - time.monotonic())
            elif k == pygame.K_r:
                self.tm_run = self.tm_done = False
                self.tm_left = float(self.tm_dur)
            return True
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if self.tm_left <= 0 or self.tm_done:
                self.tm_left = float(self.tm_dur)
            self.tm_done = False
            self.tm_run = True
            self.tm_end = time.monotonic() + self.tm_left
        elif k == pygame.K_r:
            self.tm_run = self.tm_done = False
            self.tm_left = float(self.tm_dur)
        elif pygame.K_1 <= k <= pygame.K_6:
            self.tm_dur = TM_PRESETS[k - pygame.K_1] * 60
            self.tm_left = float(self.tm_dur)
            self.tm_done = False
        elif k in (pygame.K_LEFT, pygame.K_RIGHT):
            self.tm_dur = max(10, self.tm_dur + (60 if k == pygame.K_RIGHT else -60))
            self.tm_left = float(self.tm_dur)
            self.tm_done = False
        elif k in (pygame.K_UP, pygame.K_DOWN):
            self.tm_dur = max(10, self.tm_dur + (10 if k == pygame.K_DOWN else -10))
            self.tm_left = float(self.tm_dur)
            self.tm_done = False
        return True

    def timer_tick(self):
        """倒计时推进 + 结束蜂鸣 (poll 每秒调用)."""
        if self.tm_run:
            self.tm_left = max(0.0, self.tm_end - time.monotonic())
            if self.tm_left <= 0:
                self.tm_run = False
                self.tm_done = True
                self.tm_beep()
                self.osd = ("计时结束", time.monotonic() + 3.0)

    def tm_beep(self):
        if self.beep_proc and self.beep_proc.poll() is None:
            return
        try:
            gen_beep()
            self.beep_proc = subprocess.Popen(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                 "/tmp/dz_beep.wav"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=_AUDIO_ENV, **_FFPLAY_KW)
        except Exception:
            pass

    # ── 播放器 (TAB5 app_player 移植: HA media_player 遥控, 本机不出声) ──
    def _cal_ym(self):
        if self.cal_ym is None:
            t = time.localtime()
            self.cal_ym = (t.tm_year, t.tm_mon)
        return self.cal_ym

    def _move_fsel(self, d):
        n = len(self.fin_items()) if self.fin_view == 0 else 8
        if not n:
            return
        if abs(d) == 3:                       # 上/下越界 → 底/顶同列循环
            new = self.fsel + d
            if new < 0:
                new = min(n - 1, ((n - 1) // 3) * 3 + self.fsel % 3)
            elif new >= n:
                new = min(n - 1, self.fsel % 3)
            self.fsel = new
        else:                                 # 左/右夹紧
            self.fsel = max(0, min(n - 1, self.fsel + d))
        if self.fsel < self.fscroll:
            self.fscroll = self.fsel
        if self.fsel >= self.fscroll + 6:
            self.fscroll = self.fsel - 5

    def keyup(self, k):
        """Esc 松开: 长按退出 (音乐播放页/主页) 未满 1s → 取消."""
        if k == pygame.K_ESCAPE:
            self.esc_hold_at = None

    def move_rsel(self, d):
        n = len(self.radio_list())
        if not n:
            return
        self.rsel = (self.rsel + d) % n          # 循环: 顶部↑到底部, 反之亦然
        rows = 8
        if self.rsel < self.rscroll:
            self.rscroll = self.rsel
        if self.rsel >= self.rscroll + rows:
            self.rscroll = self.rsel - rows + 1

    def move_sel(self, d):
        n = len(self.idea_items())
        if not n:
            return
        if abs(d) == 2:                        # ↑↓: 跨行, 视口窗口跟随 (2 行一窗)
            self.sel = max(0, min(n - 1, self.sel + d))
        else:                                  # ←→: 行内移动 (不出行)
            row = self.sel // 2
            self.sel = max(row * 2, min(row * 2 + 1, self.sel + d))
        self.sel = min(self.sel, n - 1)
        row = self.sel // 2                    # 视口 2 行一窗, 整行滚动 (偶对齐)
        top = self.scroll // 2
        if row < top:
            top = row
        elif row > top + 1:
            top = row - 1
        self.scroll = min(top * 2, max(0, ((n - 1) // 2) * 2))

    @staticmethod
    def _pl_lrc_parse(s):
        """LRC → [(秒, 文本)]; 多时间戳行展开, 稳定排序 (tab5 同款)."""
        out = []
        for ln in (s or "").splitlines():
            stamps = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", ln)
            if not stamps:
                continue
            txt = re.sub(r"\[(\d+):(\d+(?:\.\d+)?)\]", "", ln).strip()
            for mm, ss in stamps:
                out.append((int(mm) * 60 + float(ss), txt))
        out.sort(key=lambda x: x[0])
        return out

    def adjust_volume(self, code):
        if self.playing_key and self.raop_cons is not None \
                and self.raop_cons.poll() is None:
            self._raop_volume(code)           # 电台 AirPlay 会话内: 音量直达音箱
            return
        if self.tool == "music" and self.mu_ff is not None \
                and self.mu_ff.poll() is None:
            self._raop_volume(code, self.mu_out_name())
            return                            # 音乐 AirPlay 会话内: 同款直达
        try:
            env = dict(os.environ, XDG_RUNTIME_DIR="/run/user/1000")
            if code == 113:                   # Fn+A 静音
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
                               timeout=2, check=False, env=env)
            else:                             # Fn+D 音量- / Fn+S 音量+
                vol = subprocess.run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
                                     capture_output=True, text=True, timeout=2,
                                     env=env).stdout
                m = re.search(r"(\d+)%", vol)
                # pactl 相对增减可超 100% (软件增益, 会破音), 钳到系统上限 100
                cur = max(0, min(100, (int(m.group(1)) if m else 100)
                                 + (-5 if code == 114 else 5)))
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@",
                                "%d%%" % cur],
                               timeout=2, check=False, env=env)
            vol = subprocess.run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
                                 capture_output=True, text=True, timeout=2,
                                 env=env).stdout
            mute = subprocess.run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"],
                                  capture_output=True, text=True, timeout=2,
                                  env=env).stdout
            m = re.search(r"(\d+)%", vol)
            label = "音量 " + m.group(1) + "%" if m else "音量?"
            if "yes" in mute:
                label = "静音 " + label
            if self.radio_out >= 1 and not (self.playing_key
                                            and self.raop_cons is not None):
                label = "本机 " + label       # 输出选了 AirPlay 但无会话: 讲明调的是本机
            self.osd = (label, time.monotonic() + 1.2)
        except Exception:
            self.osd = ("音量调节不可用", time.monotonic() + 1.0)

    # ── 绘制 ──
    def esc_hold_poll(self):
        """长按 Esc ≥1s: 音乐播放页=退出音乐, 主页=退出 dash
        (draw 每帧调; 松开 keyup 取消)."""
        if self.esc_hold_at is None:
            return
        in_music = self.tool == "music" and self.mu_view == "play"
        in_home = not self.tool and self.page == 0
        if not (in_music or in_home):
            self.esc_hold_at = None
            return
        if time.monotonic() - self.esc_hold_at >= 1.0:
            if in_music:
                self.esc_hold_at = None       # 音乐退出: 条随播放页消失
                self.tool_close()
            else:
                # 主页退 dash: 保留满条 + "退出中" (2026-11-14 用户定版:
                # 此前同帧清零 → 清理期屏幕上是退回 0 的空条)
                self.esc_exit = True

    def draw_exit_bar(self, surf, frac):
        """退出进度条 (主页退 dash / 音乐播放页退音乐共用): 22px 高盖住底部
        菜单栏, 条一出现即常显 '退出中' (无底色垫片, hi 色在 track 深底上
        清晰; 2026-11-14 用户定版: 背景片突兀, 去掉)."""
        pygame.draw.rect(surf, self.th("track"), (0, H - 22, W, 22))
        pygame.draw.rect(surf, self.th("accent"),
                         (0, H - 22, max(8, int(W * frac)), 22))
        textc(surf, "退出中", 11, self.th("hi"), W // 2, H - 19)

    def draw(self, surf):
        self.esc_hold_poll()
        t = time.localtime()
        surf.fill(self.th("page"))
        if self.tool:
            self.draw_tool(surf)
        elif self.page == 0:
            self.draw_home(surf, t)
        elif self.page == 1:
            self.draw_ideas(surf)
        elif self.page == 2:
            self.draw_radio(surf)
        elif self.page == 3:
            self.draw_fin(surf)
        elif self.page == 4:
            self.draw_news(surf)
        else:
            self.draw_tools(surf)
        _ex = 1.0 if self.esc_exit else None
        if _ex is None and not self.tool and self.page == 0 \
                and self.esc_hold_at is not None:
            _ex = min(1.0, (time.monotonic() - self.esc_hold_at) / 1.0)
        if _ex is None:
            self.draw_tabs(surf)
        else:
            self.draw_exit_bar(surf, _ex)   # 退出条遮底栏 (主页退 dash)
        self.draw_osd(surf)
        self.draw_dialog(surf)

    def draw_radio(self, surf):
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        border, track = self.th("border"), self.th("track")
        BODY = 13                                # 与想法页同字号
        sts = self.radio_list()
        playing = bool(self.playing_key)
        LX, LW = 2, 158                          # 列表区 (左, 半屏)
        RX, RW = 164, 152                        # VU 区 (右, 半屏)
        tabs = self.radio_tabs()

        # 头部: 电台 · N 台 + 归属地徽标 (IP 匹配激活时右对齐)
        import radio_stations as _rs
        text(surf, "电台", 14, hi, (6, 2))
        text(surf, "· %d 台" % len(_rs.STATIONS), 11, dim,
             (6 + textw("电台", 14) + 6, 4))
        prov, city = self.radio_local()
        if prov or city:
            loc = "·".join(x for x in (prov, city) if x)
            text(surf, clip_text("本:%s" % loc, 10, LW - textw("电台", 14) - 56),
                 10, acc, (LX + LW - 6, 5), right=True)

        # 分组 tabs (收藏在最前, 电视页同款框选; ★ 组标签即星标本身)
        gx = 6
        for i, g in enumerate(tabs):
            sel = i == self.radio_grp
            lab = g
            if sel:
                r = pygame.Rect(gx - 3, 24, textw(lab, 11) + 6, 15)
                pygame.draw.rect(surf, self.card(0), r, border_radius=3)
                pygame.draw.rect(surf, acc, r, 1, border_radius=3)
            text(surf, lab, 11, hi if sel else soft, (gx, 26))
            gx += textw(lab, 11) + 9

        # 电台列表 (左, 无序号 — 换台走 ↑↓+Enter, ←→ 切组)
        prov, city = self.radio_local()          # 本地徽标判据 (循环外一次)
        rows = 7
        y0, rh = 42, 15
        for i in range(rows):
            idx = self.rscroll + i
            if idx >= len(sts):
                break
            st = sts[idx]
            y = y0 + i * rh
            on = st["key"] == self.playing_key
            fav = st["key"] in self.radio_fav
            local = _rs.local_rank(st, prov, city) < 2
            if idx == self.rsel:
                pygame.draw.rect(surf, track, (LX, y, LW, rh - 1), border_radius=3)
                pygame.draw.rect(surf, acc, (LX, y, 2, rh - 1))
            if on:
                pygame.draw.polygon(surf, acc, [(LX + 8, y + 4), (LX + 8, y + 12),
                                                (LX + 15, y + 8)])
            elif fav:
                text(surf, "★", 11, acc, (LX + 8, y + 1))
            name_col = hi if (on or idx == self.rsel) else soft
            tx = LX + (20 if on else (18 if fav else 8))
            # 右列动态预留: 本徽标窄 (26), FM 频率宽 (46)
            rsv = 26 if local else (46 if st.get("freq") else 12)
            text(surf, clip_text(st.get("name", ""), BODY, LW - rsv - tx),
                 BODY, name_col, (tx, y + 1))
            if local:
                text(surf, "本", 10, acc, (LX + LW - 4, y + 3), right=True)
            else:
                fq = st.get("freq") or 0
                if fq > 0:
                    text(surf, "FM%.1f" % (fq / 10.0), 10, dim,
                         (LX + LW - 4, y + 3), right=True)

        if not sts:                             # 空收藏组提示
            textc(surf, "还没有收藏", BODY, dim, LX + LW // 2, 70)
            textc(surf, "列表里按 5 收藏喜欢的电台", 10, dim, LX + LW // 2, 92)
        text(surf, "←→ 组 · Enter 播/停 · 5 ★ · O 输出", 13, soft, (6, 151))

        # 右侧 VU 面板 (常驻): 播放中跳动, 未播放静止低位
        if True:
            if playing:
                lvb, lvm, lvt = self.lvl.lvl
                self.vu_tick(time.monotonic(), lvb, lvm, lvt)
            cur = (self.radio_st_by_key(self.playing_key) or {}).get("name")
            r = pygame.Rect(RX, 16, RW, 130)
            pygame.draw.rect(surf, self.card(0), r, border_radius=5)
            pygame.draw.rect(surf, border, r, 1, border_radius=5)
            text(surf, "播放中" if playing else "未播放", 10,
                 acc if playing else dim, (r.x + 8, r.y + 4))
            text(surf, clip_text("输出:%s" % self.radio_out_name(), 10, RW - 60),
                 10, dim, (r.x + RW - 8, r.y + 4), right=True)
            text(surf, clip_text(cur if cur else "—", 13, RW - 16), 13,
                 hi if cur else dim, (r.x + 8, r.y + 17))
            nb, bw2, bgap = 12, 9, 2.2
            bh = r.height - 46
            bx0 = r.x + (RW - (nb * bw2 + (nb - 1) * bgap)) // 2
            by = r.y + 40
            for i in range(nb):
                bxx = bx0 + i * (bw2 + bgap)
                pygame.draw.rect(surf, track, (bxx, by, bw2, bh), border_radius=2)
                fh = max(3, min(bh, int(self.vu[i] / 28.0 * bh)))
                pygame.draw.rect(surf, acc, (bxx, by + bh - fh, bw2, fh),
                                 border_radius=2)
                ph = min(bh, int(self.vu_pk[i] / 28.0 * bh))
                if ph > fh:
                    pygame.draw.rect(surf, hi,
                                     (bxx, by + bh - ph - 2, bw2, 2))
    def draw_ideas(self, surf):
        hi, soft, dim = self.th("hi"), self.th("soft"), self.th("dim")
        acc = self.th("accent")
        border = self.th("border")
        items = self.idea_items()
        BODY = 13                                # 与农历/主页正文同字号
        self.cloud_sync_trigger()                # 在页期间 15s 节流双写对账
        if self.iview == "cloud":
            return self._draw_ideas_cloud(surf)
        if self.scroll >= len(items):            # 删除/换号后窗口越界防呆
            self.scroll = max(0, (len(items) - 1) // 2 * 2 if items else 0)

        # 头部 (右侧: 登录用户名 / 未登录仅本地; "按9看云端"=键位提示)
        text(surf, "想法", 12, hi, (6, 3))
        cuser = self.cfg.get("cloud_user") or ""
        chead = ("@%s · 按9看云端" % cuser) if self.cfg.get("cloud_tok") \
            else "仅本地 · 按9看云端"
        pages = " · %d-%d" % (self.scroll + 1,
                              min(self.scroll + 4, len(items))) \
            if len(items) > 4 else ""
        text(surf, "· %d 条%s" % (len(items), pages), 10, dim,
             (6 + textw("想法", 12) + 6, 5))
        text(surf, chead, 10, dim, (W - 6, 5), right=True)

        # 卡片网格 2×2: 4 张卡常驻 (时间行 + 内容 ≤2 行), 工具条固定在下方
        M, GW, GH, gap = 4, 154, 62, 4
        y0 = 16
        pos = [(M, y0), (M + GW + gap, y0),
               (M, y0 + GH + gap), (M + GW + gap, y0 + GH + gap)]
        for i in range(4):
            idx = self.scroll + i
            r = pygame.Rect(pos[i][0], pos[i][1], GW, GH)
            sel = idx == self.sel
            have = idx < len(items)
            pygame.draw.rect(surf, self.card(0) if have else self.th("track"),
                             r, border_radius=5)
            pygame.draw.rect(surf, acc if sel else border, r,
                             2 if sel else 1, border_radius=5)
            if not have:
                continue
            it = items[idx]
            playing = (self.playing_voice
                       and it.get("id") == self.playing_voice.get("id"))
            ts = time.localtime(it["createdAt"] / 1000.0)
            if it["kind"] == "voice":
                # 语音卡: ▶ 图标 + 时长; 播放中 = 底部进度条
                ty = r.y + 10
                text(surf, "%02d-%02d %02d:%02d" % (ts.tm_mon, ts.tm_mday,
                                                    ts.tm_hour, ts.tm_min),
                     10, dim, (r.x + 8, ty))
                pygame.draw.polygon(surf, acc if (playing or sel) else soft, [
                    (r.x + 10, ty + 16), (r.x + 10, ty + 27),
                    (r.x + 19, ty + 21)])
                text(surf, "语音 %ds" % (it.get("dur") or 0), BODY,
                     hi if sel else soft, (r.x + 26, ty + 15))
                if playing:
                    total = it.get("dur") or 0
                    elapsed = time.monotonic() - self._voice_at
                    bx, bw2 = r.x + 10, GW - 20
                    by = r.y + GH - 12
                    now = time.monotonic()
                    for bi in range(3):           # 均衡图标
                        hgt = 4 + int(3 * (1 + math.sin(now * 7 + bi * 2.1)) / 2)
                        pygame.draw.rect(surf, acc,
                                         (bx + bi * 5, by - hgt + 6, 3, hgt))
                    pygame.draw.rect(surf, self.th("track"),
                                     (bx + 20, by, bw2 - 20, 6), border_radius=3)
                    frac = min(1.0, elapsed / total) if total > 0 else 0.0
                    if frac > 0:
                        pygame.draw.rect(surf, acc,
                                         (bx + 20, by, max(3, int((bw2 - 20) * frac)), 6),
                                         border_radius=3)
                continue
            lines = self._wrap_lines(it.get("text") or "", BODY, GW - 16, 3)
            n_lines = len(lines)
            blk = 12 + 2 + n_lines * 14 - 2        # 时间行 + 间距 + 内容块
            ty = r.y + max(4, (GH - blk) // 2 - 3)  # 垂直居中偏上 3px (顶部保底 4px)
            text(surf, "%02d-%02d %02d:%02d" % (ts.tm_mon, ts.tm_mday,
                                                ts.tm_hour, ts.tm_min),
                 10, dim, (r.x + 8, ty))
            ly = ty + 15
            for ln in lines[:n_lines]:
                text(surf, ln, BODY, hi if sel else soft, (r.x + 8, ly))
                ly += 14

        if not items:
            textc(surf, "暂无想法 · n 文字 / 5 语音", BODY, dim, W // 2, 70)

        # 录音卡 (第 4 卡槽): 红点闪烁 + 计时
        if self.rec_proc and not self.in_input:
            r = pygame.Rect(M, 116, W - 2 * M, 27)
            pygame.draw.rect(surf, self.card(0), r, border_radius=5)
            pygame.draw.rect(surf, self.th("danger"), r, 1, border_radius=5)
            if int(time.monotonic() * 2) % 2 == 0:      # 红点闪烁
                pygame.draw.circle(surf, self.th("danger"),
                                   (r.x + 16, r.y + 27 // 2), 5)
            el = int(time.monotonic() - self.rec_started)
            text(surf, "录音中", BODY, hi, (r.x + 28, r.y + 8))
            text(surf, "%d:%02d" % (el // 60, el % 60), BODY,
                 self.th("danger"), (r.x + 86, r.y + 8))
            text(surf, "5 停止并保存", 10, dim, (r.right - 8, r.y + 10),
                 right=True)

        # 输入行 (第 4 张卡的位置, 底 141 不越菜单, 框内元素全部收在框里)
        if self.in_input:
            r = pygame.Rect(M, 116, W - 2 * M, 27)
            pygame.draw.rect(surf, self.card(0), r, border_radius=5)
            pygame.draw.rect(surf, acc, r, 1, border_radius=5)
            ty = r.y + (27 - 13) // 2          # 框内垂直居中
            text(surf, ">", BODY, acc, (r.x + 8, ty))
            tag = "拼" if self.ime_mode else "EN"
            text(surf, tag, 10, acc, (r.x + 22, ty + 1))
            shown = clip_text(self.buf + self.ime_buf, BODY, 150)
            text(surf, shown, BODY, hi, (r.x + 40, ty))
            if int(time.monotonic() * 2) % 2 == 0:
                cx = r.x + 40 + textw(shown, BODY) + 1
                pygame.draw.rect(surf, acc, (cx, ty + 2, 7, 13))
            text(surf, "/ 英 · Enter 存", 9, dim, (r.right - 8, ty + 3),
                 right=True)

        # 底部快捷键提示 (拼音候选条激活时让位; 左侧留 2 字符宽)
        if not (self.in_input and self.ime_bar(surf, 146)):
            hx = 6 + textw("我我", 13)
            for lab in ("4 增加", "5 录音", "7 删除", "8 播放"):
                text(surf, lab, 13, soft, (hx, 151))
                hx += textw(lab, 13) + textw("我我我", 13)

    def _draw_ideas_cloud(self, surf):
        """云端列表 (9 键进入): 时间 + 文字/语音, Enter 下载播放, 7 删."""
        hi, soft, dim = self.th("hi"), self.th("soft"), self.th("dim")
        acc = self.th("accent")
        notes = self.cloud_notes
        cuser = self.cfg.get("cloud_user") or ""
        logged = bool(self.cfg.get("cloud_tok"))
        text(surf, "想法·云端", 12, hi, (6, 3))
        text(surf, ("@%s" % cuser) if logged else "未登录", 10, dim,
             (W - 6, 5), right=True)
        if not logged:
            textc(surf, "未登录 · 想法仅本地保存", 13, dim, W // 2, 62)
            textc(surf, "设置里 登录 或 注册新账号", 12, acc, W // 2, 82)
            text(surf, "9 本地", 13, soft, (6, 151))
            return
        text(surf, "· %d 条" % len(notes), 10, dim,
             (6 + textw("想法·云端", 12) + 6, 5))
        if not notes:
            textc(surf, "云端暂无条目", 13, dim, W // 2, 70)
        else:
            start = max(0, min(self.csel - 7, len(notes) - 8))
            for i in range(8):
                idx = start + i
                if idx >= len(notes):
                    break
                it = notes[idx]
                sel = idx == self.csel
                y = 18 + i * 15
                if sel:
                    pygame.draw.rect(surf, self.th("track"),
                                     (4, y - 1, W - 8, 14), border_radius=3)
                ts = time.localtime(it.get("ts") or 0)
                text(surf, "%02d-%02d %02d:%02d" % (ts.tm_mon, ts.tm_mday,
                                                    ts.tm_hour, ts.tm_min),
                     10, dim, (8, y + 1))
                if it.get("kind") == "voice":
                    text(surf, "▶ 语音 %ds" % (it.get("dur") or 0), 13,
                         hi if sel else soft, (64, y))
                else:
                    text(surf, clip_text(it.get("text") or "", 13, W - 78),
                         13, hi if sel else soft, (64, y))
                if self.playing_voice \
                        and self.playing_voice.get("id") == "cloud-" + it.get("id", ""):
                    pygame.draw.polygon(surf, acc, [
                        (W - 16, y + 1), (W - 16, y + 11), (W - 9, y + 6)])
        text(surf, "9 本地 · Enter 播 · 7 删云", 13, soft, (6, 151))

    def _news_items(self):
        """主页新闻卡: 第一个有条目的可见源 (源表可配置后不再钉死 ai)."""
        j = self.data.get("news") or {}
        feeds = j.get("feeds") or {}
        for fid, _n in self.news_srcs():
            its = [it for it in ((feeds.get(fid) or {}).get("items") or [])
                   if it.get("t")]
            if its:
                return its[:12]
        return []

    def _wrap_lines(self, s_, size, maxw, n):
        """按像素宽折行, 最多 n 行 (超出并到末行加省略号截断).
        逐字符折行对邮件 800 字符全文是 O(n²) 渲染 (详情页每帧重算 → 冻结),
        改逐行二分找断点, 结果按参数缓存 (详情打开期间文本不变)."""
        key = (str(s_), size, maxw, n)
        hit = _wrap_cache.get(key)
        if hit is not None:
            return list(hit)
        out = _wrap_lines_impl(s_, size, maxw, n)
        if len(_wrap_cache) > 600:
            _wrap_cache.clear()
        _wrap_cache[key] = out
        return list(out)

    def draw_home(self, surf, t):
        hi, soft, dim = self.th("hi"), self.th("soft"), self.th("dim")
        acc = self.th("accent")
        border = self.th("border")
        M = 4                                    # 屏边距 = 卡距
        BODY = 13                                # 正文字号 (除时钟外统一)

        # ── 左主卡: 时钟/日期+农历/天气/新股 ──
        LW = 112
        r = pygame.Rect(M, M, LW, 137)
        pygame.draw.rect(surf, self.card(0), r, border_radius=6)
        pygame.draw.rect(surf, border, r, 1, border_radius=6)
        ccx = r.x + LW // 2

        hm = "%02d:%02d" % (t.tm_hour, t.tm_min)
        ss = "%02d" % t.tm_sec
        grp = textw(hm, 30) + 4 + textw(ss, 12)
        x0 = ccx - grp // 2
        text(surf, hm, 30, hi, (x0, 12))
        # 秒与分底部对齐 (30px/12px 字形底差 ≈13, 实测校正)
        text(surf, ss, 12, dim, (x0 + textw(hm, 30) + 4, 25))

        # 时钟下方三行 (日期/农历/天气) 等距; 电池图标贴底
        # (新股/自选股信息挪行情页顶部跑马灯, 主页卡不再显示)
        rows_y = []
        top_y, bot_y = 42, r.bottom - 22
        n_rows = 3
        slot = (bot_y - top_y) / n_rows
        for i in range(n_rows):
            rows_y.append(int(top_y + i * slot + (slot - 9) // 2))

        wd = WEEK_ZH[t.tm_wday]
        textc(surf, "%02d月%02d日 %s" % (t.tm_mon, t.tm_mday, wd), BODY, soft,
              ccx, rows_y[0])
        textc(surf, "农历" + lunar.lunar_text(t.tm_year, t.tm_mon, t.tm_mday),
              BODY, soft, ccx, rows_y[1])
        wx = self.data.get("wx") or {}
        cw = wx.get("current_weather") or {}
        if cw.get("temperature") is not None:
            temp = "%d°" % round(cw["temperature"])
            cond = wx_code_zh(cw.get("weathercode", -1)) or "—"
            textc(surf, clip_text(temp + " " + cond + " · "
                                  + (self.wx_loc.get("city") or WX_HOME["city"]),
                                  BODY, 104), BODY, hi, ccx, rows_y[2])
        else:
            textc(surf, "天气获取中" if self.net.mode != "offline" else "离线",
                  BODY, dim, ccx, rows_y[2])
        # 电池图标 (贴底右下, 距底边 = 时钟距顶边 8px): 外壳+电量填充+正极头
        bp, bs = battery_state()
        if bp is not None:
            charging = bs.startswith("Charg")
            col = (acc if charging else
                   (self.th("warn") if bp < 20 else soft))
            iw, ih = 16, 8                        # 图标尺寸
            ix = r.right - 8 - iw - 2             # 留出极头
            iy = r.bottom - 8 - ih
            pygame.draw.rect(surf, col, (ix, iy, iw, ih), 1, border_radius=2)
            pygame.draw.rect(surf, col, (ix + iw, iy + 3, 2, ih - 6),
                             border_radius=1)      # 正极头
            fw = int((iw - 4) * bp / 100.0)
            if fw > 0:
                pygame.draw.rect(surf, col, (ix + 2, iy + 2, fw, ih - 4))
            if charging:                          # 充电: 闪电标
                pygame.draw.polygon(surf, hi, [
                    (ix + iw // 2 + 1, iy + 1), (ix + iw // 2 - 3, iy + ih // 2),
                    (ix + iw // 2, iy + ih // 2), (ix + iw // 2 - 1, iy + ih - 1),
                    (ix + iw // 2 + 3, iy + ih // 2 + 1),
                    (ix + iw // 2, iy + ih // 2 + 1)])

        # ── 右列卡片: 按设置显隐 (提醒行程/每日一句/新闻/自选股) 动态堆叠 ──
        cx = M + LW + M                            # 124
        cw2 = W - cx - M                           # 192
        ck = self.cfg.get("cards", {})
        vis = [(lab, key) for lab, key in
               (("提醒/行程", "sched"), ("每日一句", "hitokoto"),
                ("新闻", "news"), ("自选股", "fin"))
               if self._card_on(key)][:3]      # 右列最多 3 卡 (用户定版)
        nc = max(1, len(vis))
        gap = M
        chh = (137 - M * (nc - 1)) // nc           # 动态均分右列高
        tops = {lab: M + i * (chh + gap)
                for i, (lab, key) in enumerate(vis)}
        now = time.monotonic()
        for i, (lab, key) in enumerate(vis):
            rr = pygame.Rect(cx, tops[lab], cw2, chh)
            pygame.draw.rect(surf, self.card(i + 1), rr, border_radius=6)
            pygame.draw.rect(surf, border, rr, 1, border_radius=6)
            text(surf, lab, 12, dim, (rr.x + 8, rr.y + 2))   # 标题 12 < 正文 13 (2026-11-20 用户: 标题/正文字号互换)
        # 每日一句: 单行跑马灯 (溢出卡宽即滚动, 同新闻卡)
        top0 = tops.get("每日一句")
        if tops.get("每日一句") is not None:
            hk = self.data.get("hitokoto") or {}
            if hk.get("text"):
                txt = hk["text"]
                if hk.get("from"):
                    txt += " — " + hk["from"]
                vw = cw2 - 16
                vy = top0 + 13 + (chh - 13 - BODY) // 2
                segs = _split_width_segs(txt, BODY, vw)
                si = int(now // 3) % len(segs)   # 3s 一段轮播
                text(surf, segs[si], BODY, soft, (cx + 8, vy))
            else:
                text(surf, "获取中" if self.net.mode == "online" else "--",
                     BODY, dim, (cx + 8, top0 + 13 + (chh - 13 - BODY) // 2))
            # 新闻: 单行 BODY px 垂直居中, 超宽横向跑马灯, 滚完一条再换
        top1 = tops.get("新闻")
        if tops.get("新闻") is not None:
            news = self._news_items()
            now = time.monotonic()
            if news:
                if now - self._news_at > 10:           # 10s 轮换头条 (无跑马灯)
                    self._news_at = now
                    self.news_i = (self.news_i + 1) % len(news)
                headline = news[self.news_i]["t"]
                vw = cw2 - 16
                vy2 = top1 + 13 + (chh - 13 - BODY) // 2
                text(surf, clip_text(headline, BODY, vw), BODY, soft,
                     (cx + 8, vy2))
            else:
                text(surf, "获取中" if self.net.mode != "offline" else "离线",
                     BODY, dim, (cx + 8, top1 + 16))
            # 自选股行情卡 (新闻下方): 单行跑马灯
        top2 = tops.get("自选股")
        if tops.get("自选股") is not None:
            its = self.fin_items()
            segs = []                              # 分段着色: 涨红跌绿
            for it in its:
                if not it.get("price"):
                    continue
                segs.append(("%s %.2f %+.2f%%" % (
                    (it.get("name") or it["code"])[:6],
                    it.get("price") or 0, it.get("pchg") or 0),
                    self._chg_col(it.get("pchg") or 0)))
            if segs:
                vw = cw2 - 16
                SEP = " · "
                sep_w = textw(SEP, BODY)
                widths = [textw(t, BODY) for t, _ in segs]
                tw = sum(widths) + sep_w * (len(segs) - 1)
                vy = top2 + 13 + (chh - 13 - BODY) // 2
                if tw <= vw:                       # 不溢出: 静态分行内逐段画
                    x = cx + 8
                    for (t, c), w in zip(segs, widths):
                        text(surf, t, BODY, c, (x, vy))
                        x += w + sep_w
                else:                              # 跑马灯: 逐段平移, 分隔符 dim
                    SPEED = 28.0
                    span = tw + vw + 24
                    if now - self._wk_at > span / SPEED + 1.0:
                        self._wk_at = now
                    off = (now - self._wk_at) * SPEED
                    surf.set_clip((cx + 8, top2, vw, chh))
                    x = cx + 8 + vw - off
                    for i, ((t, c), w) in enumerate(zip(segs, widths)):
                        if x + w > cx + 8 and x < cx + 8 + vw:
                            text(surf, t, BODY, c, (x, vy))
                        if i < len(segs) - 1:
                            text(surf, SEP, BODY, dim, (x + w, vy))
                        x += w + sep_w
                    surf.set_clip(None)
            else:
                text(surf, "获取中" if self.net.mode == "online" else "--",
                     BODY, dim, (cx + 8, top2 + 13 + (chh - 13 - BODY) // 2))
    
    # ── 行情页: 自选/指数/商品 三子页 ──
        # 提醒/行程卡: 最近条目单行居中, 3s 轮播 (300s 节流后台拉新)
        tops_s = tops.get("提醒/行程")
        if tops_s is not None:
            if time.monotonic() - self._sched_home_at > 300:
                self._sched_home_at = time.monotonic()
                self.sched_refresh(False)
            its = self._sched_items()
            if its:
                si = int(time.monotonic() // 3) % len(its)   # 3s 轮播一条
                it2 = its[si]
                lt2 = time.localtime(it2["t"] or time.time())
                vy = tops_s + 12 + (chh - 12 - BODY) // 2    # 标签(12px)下垂直居中
                text(surf, "%02d-%02d %02d:%02d"
                     % (lt2.tm_mon, lt2.tm_mday, lt2.tm_hour, lt2.tm_min),
                     BODY, soft, (cx + 8, vy))                # 13px 下宽 82
                text(surf, "行" if it2["kind"] == "trip" else "提",
                     BODY, soft, (cx + 96, vy))
                text(surf, clip_text(it2["text"], BODY, cw2 - 123), BODY,
                     soft, (cx + 115, vy))
            else:
                text(surf, "暂无 · iPhone 同步后按4刷新", BODY, dim,
                     (cx + 8, tops_s + 13 + (chh - 13 - BODY) // 2))

    FIN_TABS = ["自选股", "指数", "商品"]

    @staticmethod
    def _tint(base, col, t):
        """向目标色混合 t (0-1), 用于涨红/跌绿卡片底."""
        return tuple(int(base[i] + (col[i] - base[i]) * t) for i in range(3))

    def _chg_col(self, v):
        """红涨绿跌 (car-dash 同款: up=danger, down=ok)."""
        if v > 0.005:
            return self.th("danger")
        if v < -0.005:
            return self.th("ok")
        return self.th("dim")

    def _fmt_pct(self, v):
        return ("+" if v > 0 else "") + "%.2f%%" % v

    @staticmethod
    def _flag_icon(surf, x, y, col):
        """红色旗子图形: 旗杆 + 三角旗 (字体无 emoji 的替代)."""
        pygame.draw.line(surf, col, (x, y), (x, y + 11), 2)
        pygame.draw.polygon(surf, col, [(x + 1, y), (x + 11, y + 3), (x + 1, y + 7)])

    def _fmt_price(self, v):
        if v >= 10000:
            return "%.0f" % v
        if v >= 1000:
            return "%.1f" % v
        return "%.2f" % v

    def fin_items(self):
        """自选股: 本地 watchlist + 实时 quotes 覆盖, 按涨跌幅高→低.

        (旧版本定义曾重复存两份 — 2026-11-20 去重只留此份)"""
        items = [dict(it) for it in self.watch.get("items", [])
                 if it.get("code")][:16]
        qt = parse_qt_text(self.data.get("quotes") or "")
        for it in items:
            f = qt.get(qt_code(it["code"]))
            if qt_ok(f):
                it["price"] = qt_num(f, 3)
                it["chg"] = qt_num(f, 31)
                it["pchg"] = qt_num(f, 32)
                if not it.get("name") and len(f) > 1:
                    it["name"] = f[1]
        # 涨跌幅高→低 (用户定版 2026-11-20); 无行情 (-inf) 排末尾;
        # 稳定排序 → 同涨幅/无行情保持 watch 原顺序
        items.sort(key=lambda it: it.get("pchg")
                   if isinstance(it.get("pchg"), (int, float))
                   else float("-inf"), reverse=True)
        return items

    def idx_cards(self):
        qt = parse_qt_text(self.data.get("idx") or "")
        out = []
        for name, code in IDX_LIST:
            f = qt.get(code)
            if qt_ok(f):
                out.append((name, code, qt_num(f, 3), qt_num(f, 31),
                            qt_num(f, 32)))
            else:
                out.append((name, code, 0, 0, 0))
        return out

    def cmd_cards(self):
        hf = parse_hf_text(self.data.get("cmd") or "")
        btc = None
        g = self.data.get("gate")
        if isinstance(g, list) and g:
            try:
                if float(g[0].get("last") or 0) > 0:
                    btc = g[0]
            except (TypeError, ValueError, KeyError, IndexError):
                btc = None
        out = []
        for name, code in CMD_LIST:
            if code is None:
                if btc:
                    price = float(btc.get("last") or 0)
                    pct = float(btc.get("change_percentage") or 0)
                    out.append((name, "USDT", price, price * pct / 100.0, pct))
                else:
                    out.append((name, "USDT", 0, 0, 0))
            else:
                f = hf.get(code)
                if f and qt_num(f, 0) > 0:
                    price = qt_num(f, 0)
                    pct = qt_num(f, 1)
                    out.append((name, code.replace("hf_", ""), price,
                                price * pct / 100.0, pct))
                else:
                    out.append((name, "", 0, 0, 0))
        return out

    def draw_fin(self, surf):
        hi, soft, dim = self.th("hi"), self.th("soft"), self.th("dim")
        acc = self.th("accent")
        border = self.th("border")
        BODY = 13
        M = 4
        view = self.fin_view

        # 头部 (图表详情显示股票名, 不显示总标题)
        if not self.fdetail:
            text(surf, "行情", 14, hi, (6, 2))
            text(surf, "· " + self.FIN_TABS[view], 11, dim,
                 (6 + textw("行情", 14) + 6, 4))
            stag = self._fin_sync_tag()
            text(surf, stag, 10, acc if "云✓" in stag else dim,
                 (W - 6, 4), right=True)   # 同步状态: 云✓ / 本地
            self._draw_fin_ticker(surf)   # 顶部标题: 自选股行情+新股 跑马灯

        if self.fdetail:
            self.draw_fin_detail(surf)

        else:
            # 2 行 × 3 列卡片 (car-dash 股票卡: 涨红跌绿底)
            if view == 0:
                cards = [(it.get("name") or it["code"], it["code"],
                          it.get("price") or 0, it.get("chg") or 0,
                          it.get("pchg") or 0) for it in self.fin_items()]
            elif view == 1:
                cards = self.idx_cards()
            else:
                cards = self.cmd_cards()
            base = self.card(0)
            cols, cwid, chh, tgap = 3, 102, 61, 3
            for i in range(6):
                idx = self.fscroll + i
                if idx >= len(cards):
                    break
                name, code, price, chg, pct = cards[idx]
                colr = self._chg_col(pct)
                col = self.th("danger") if pct > 0.005 else (
                    self.th("ok") if pct < -0.005 else acc)
                bg = self._tint(base, col, 0.22)   # 涨红跌绿底
                r = pygame.Rect(M + (i % cols) * (cwid + tgap),
                                16 + (i // cols) * (chh + tgap), cwid, chh)
                pygame.draw.rect(surf, bg, r, border_radius=5)
                pygame.draw.rect(surf, border, r, 1, border_radius=5)
                if view == 0 and idx == self.fsel:
                    pygame.draw.rect(surf, acc, r, 1, border_radius=5)
                # 三行数据水平居中, 行距均分 (名称/价格/涨跌)
                cx = r.x + cwid // 2
                textc(surf, clip_text(name, 13, cwid - 8), 13, soft,
                      cx, r.y + 7)
                textc(surf, self._fmt_price(price) if price else "--", 16,
                      colr if price else dim, cx, r.y + 25)
                chgl = ("%s%.2f  %s" % ("+" if chg > 0 else "", chg,
                                        self._fmt_pct(pct))) if pct else "--"
                textc(surf, chgl, 11, colr if pct else dim, cx, r.y + 45)

        # 底部子页快捷键
        y = 151
        text(surf, "4 切换行情", 13, acc if view else soft, (6, y))
        text(surf, "ENTER 日线", 13, soft, (6 + textw("4 切换行情", 13)
                                            + textw("我我", 13), y))
        if view == 0:
            text(surf, "n 添加 · d 删", 13, soft, (W - 6, y),
                 right=True)

        # 添加自选股输入框 (第 2 排卡片之上浮层)
        if self.fin_input:
            r = pygame.Rect(M, 116, W - 2 * M, 27)
            pygame.draw.rect(surf, self.th("track"), r, border_radius=5)
            pygame.draw.rect(surf, acc, r, 2, border_radius=5)
            ty = r.y + (27 - 13) // 2
            text(surf, "+", BODY, acc, (r.x + 8, ty))
            tag = "拼" if self.ime_mode else "EN"
            text(surf, tag, 10, acc, (r.x + 22, ty + 1))
            shown = clip_text(self.buf + self.ime_buf, BODY, 150)
            text(surf, shown, BODY, hi, (r.x + 40, ty))
            if int(time.monotonic() * 2) % 2 == 0:
                cx2 = r.x + 40 + textw(shown, BODY) + 1
                pygame.draw.rect(surf, acc, (cx2, ty + 2, 7, 13))
            text(surf, "Enter 存 · / 英 · Esc 退", 9, dim,
                 (r.right - 8, ty + 3), right=True)
            if not self.ime_bar(surf, 146):
                pass

    def _fin_sync_tag(self):
        """行情页右上同步状态: Oneapp 云 (登录即有); 未登录显示 本地."""
        return "云✓" if self.cfg.get("cloud_tok") else "本地"

    def _draw_fin_ticker(self, surf):
        """顶部标题跑马灯: 仅新股申购信息 (个股行情在卡片列表里, 不重复)."""
        parts = []
        ipo = self.data.get("ipo") or {}
        for it in (ipo.get("items") or []):
            parts.append("新股申购 %s %s%s" % (
                (it.get("name") or "")[:8], it.get("date") or "",
                "(今)" if it.get("today") else ""))
        if not parts:
            return
        txt = " · ".join(parts)
        tab = "· " + self.FIN_TABS[self.fin_view]
        tx = 6 + textw("行情", 14) + 6 + textw(tab, 11) + 10
        vw = W - 6 - tx - textw(self._fin_sync_tag(), 10) - 8  # 右侧让位状态
        if vw < 60:
            return
        dim = self.th("dim")
        tw = textw(txt, 13)
        vy = 3
        now = time.monotonic()
        if tw <= vw:
            text(surf, txt, 13, dim, (tx, vy))
            return
        SPEED = 28.0
        span = tw + vw + 24
        if now - self._tk_at > span / SPEED + 1.0:
            self._tk_at = now
        off = (now - self._tk_at) * SPEED
        surf.set_clip((tx, 0, vw, 18))
        text(surf, txt, 13, dim, (tx + vw - off, vy))
        surf.set_clip(None)

    def draw_fin_detail(self, surf):
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        d = self.fdetail
        code, name = d["code"], d["name"]

        # 头部
        text(surf, clip_text(name, 13, 120), 13, hi, (6, 3))
        text(surf, code + (" · 分时" if d["view"] == "min" else " · 日K"), 9,
             dim, (10 + textw(clip_text(name, 13, 120), 13), 7))
        text(surf, "9 放大 · 0 缩小 · Enter 返回", 11, dim, (W - 6, 4), right=True)

        # 价格行: 详情价取 quotes, 兜底 portfolio
        price = pchg = chg = 0
        qt = parse_qt_text(self.data.get("quotes") or "")
        f = qt.get(code)
        if qt_ok(f):
            price, chg, pchg = qt_num(f, 3), qt_num(f, 31), qt_num(f, 32)
            prev_close, high, low = qt_num(f, 4), qt_num(f, 33), qt_num(f, 34)
            turnover = qt_num(f, 37)
        col = self._chg_col(pchg)
        text(surf, "%.2f" % price if price else "--", 22, col, (6, 20))
        px = 12 + textw("%.2f" % price if price else "--", 22)
        text(surf, "%s%.2f  %s" % ("+" if chg > 0 else "", chg,
                                   self._fmt_pct(pchg)), 11, col, (px, 30))
        if f and qt_ok(f):
            text(surf, "高 %.2f  低 %.2f  昨 %.2f" % (high, low, prev_close),
                 8, dim, (W - 6, 24), right=True)
            if turnover:
                text(surf, ("成交额 %.1f亿" % (turnover / 10000.0)
                            if turnover >= 10000 else "成交额 %d万" % turnover),
                     8, dim, (W - 6, 34), right=True)

        # 图表区
        r = pygame.Rect(2, 44, W - 4, 106)
        pygame.draw.rect(surf, self.card(0), r, border_radius=4)
        pygame.draw.rect(surf, self.th("border"), r, 1, border_radius=4)
        if d["view"] == "min":
            prices = parse_minute(self.data.get("minute") or {}, code)
            self._draw_line_chart(surf, r, prices, baseline=prev_close if f else None,
                                  labels=("9:30", "11:30/13:00", "15:00"),
                                  zoom=d.get("zoom", 1.0))
        else:
            days = parse_day(self.data.get("day") or {}, code)
            closes = [x[2] for x in days]
            self._draw_line_chart(surf, r, closes, baseline=None,
                                  labels=(days[0][0][2:] if days else "",
                                          "", days[-1][0][2:] if days else ""),
                                  color=col, zoom=d.get("zoom", 1.0))

    def _draw_line_chart(self, surf, r, ys, baseline=None, labels=("", "", ""),
                         color=None, zoom=1.0):
        """折线图: r 区域内画 ys 序列 (+可选昨收基线 + 底部标签)."""
        dim = self.th("dim")
        if not ys:
            textc(surf, "数据获取中…", 9, dim, r.centerx, r.y + 44)
            return
        lo, hi_ = min(ys), max(ys)
        if baseline is not None:
            lo, hi_ = min(lo, baseline), max(hi_, baseline)
        center = (lo + hi_) / 2.0
        half = max((hi_ - lo) / 2.0, 1e-6) / max(0.2, zoom)
        lo, hi_ = center - half, center + half
        span = (hi_ - lo) or 1.0
        pad_t, pad_b = 12, 14
        ch = r.height - pad_t - pad_b
        cy0 = r.y + pad_t

        def pt(i, v):
            x = r.x + 4 + int(i / max(1, len(ys) - 1) * (r.width - 8))
            y = cy0 + int((hi_ - v) / span * ch)
            return (x, y)

        surf.set_clip(r)                       # 缩放后线条只画在图框内
        if baseline is not None:               # 昨收虚线
            by = pt(0, baseline)[1]
            for x in range(r.x + 4, r.x + r.width - 4, 8):
                pygame.draw.line(surf, self.th("border"), (x, by), (x + 4, by), 1)
        col = color or (self._chg_col(ys[-1] - baseline) if baseline is not None
                        else self.th("accent"))
        pts = [pt(i, v) for i, v in enumerate(ys)]
        if len(pts) > 1:
            pygame.draw.lines(surf, col, False, pts, 2)
        # 最新点亮点
        pygame.draw.circle(surf, col, pts[-1], 2)
        surf.set_clip(None)
        # 高低标签 (缩放时原极值已出框, 不显示)
        if zoom <= 1.01:
            text(surf, "%.2f" % hi_, 7, dim, (r.x + 6, r.y + 2))
            text(surf, "%.2f" % lo, 7, dim, (r.x + 6, r.bottom - 12))
        for lab, xx in zip(labels, (r.x + 6, r.centerx - 12, r.right - 44)):
            if lab:
                text(surf, lab, 7, dim, (xx, r.bottom - 11))

    # ── 工具页: 万年历 / 汇率 ──
    # ── 新闻页 (car-dash 同源 /news/feeds, 13px) ──
    @staticmethod
    def _news_feeds_merge(feeds):
        """内置源补齐: 表里缺的内置 id → 追加种子 (版本升级新增内置源时,
        老本地/云端列表自动拿到; 用户显隐态/自定义源不动)."""
        have = {f.get("id") for f in feeds}
        added = False
        for d in news_rss.defaults():
            if d["id"] not in have:
                feeds.append(d)
                added = True
        return added

    @classmethod
    def _news_feeds_load(cls):
        """源表 (news_feeds.json); 坏数据/空 → 内置种子; 缺内置 → 补齐落盘."""
        v = netmod.load_json_file(NEWS_FEEDS_FILE, None)
        if isinstance(v, list) and v and all(
                isinstance(f, dict) and f.get("id") and f.get("url")
                for f in v):
            for f in v:
                f["on"] = bool(f.get("on", True))
            if cls._news_feeds_merge(v):
                try:
                    netmod.save_json_file(NEWS_FEEDS_FILE, v)
                except Exception:
                    pass
            return v
        seed = news_rss.defaults()
        try:
            netmod.save_json_file(NEWS_FEEDS_FILE, seed)
        except Exception:
            pass
        return seed

    def news_srcs(self):
        """生效源表 [(id, 显示名)] — 设置里 on=True 的 (列表页 ←→ 只在
        可见源间循环, 隐藏源不出现)."""
        return [(f["id"], str(f.get("name") or f["id"]))
                for f in self.news_feeds if f.get("on", True)]

    def news_items(self):
        j = self.data.get("news") or {}
        srcs = self.news_srcs()
        if not srcs:
            return []
        self.news_src_i %= len(srcs)      # 源被隐藏/云同步缩短后钳回
        feed = (j.get("feeds") or {}).get(srcs[self.news_src_i][0]) or {}
        return [it for it in (feed.get("items") or []) if it.get("t")][:30]

    def _news_src_cycle(self, d):
        """←/→ 切换新闻源 (原 9 键); 换源清光标/滚动/详情."""
        srcs = self.news_srcs()
        if not srcs:
            return
        self.news_src_i = (self.news_src_i + d) % len(srcs)
        self.news_sel = 0
        self.news_scroll = 0
        self.news_detail = None

    def _ns_move(self, d):
        """设置视图光标 (7 行窗口滚动)."""
        n = len(self.news_feeds)
        if not n:
            return
        self.ns_sel = max(0, min(n - 1, self.ns_sel + d))
        if self.ns_sel < self.ns_scroll:
            self.ns_scroll = self.ns_sel
        elif self.ns_sel >= self.ns_scroll + 7:
            self.ns_scroll = self.ns_sel - 6

    def _news_feeds_osd(self, msg):
        """源表变更反馈: 登录态标 已上云 (未登录本地落盘, 开机补推)."""
        self.osd = (msg + (" · 已上云" if self.cfg.get("cloud_tok") else ""),
                    time.monotonic() + 2)

    def news_feed_toggle(self):
        """设置视图 Enter: 显/隐切换 (内置+自定义同权; 云同步跟随源表)."""
        if not (0 <= self.ns_sel < len(self.news_feeds)):
            return
        f = self.news_feeds[self.ns_sel]
        f["on"] = not f.get("on", True)
        self.news_src_i = self.news_sel = self.news_scroll = 0
        self.news_detail = None
        self.news_feeds_save()
        self._news_feeds_osd("%s %s" % (f.get("name") or "",
                                        "已显示" if f["on"] else "已隐藏"))

    def _ns_phase_title(self):
        """地址相 → 标题相 (地址暂存 ns_url; 编辑态标题预填旧名, 拼音默认)."""
        self.ns_url = (self.buf or "").strip()
        self.buf = ""
        if self.ns_edit_id:
            old = next((x for x in self.news_feeds
                        if x.get("id") == self.ns_edit_id), None)
            self.buf = str((old or {}).get("name") or "")
        self.ns_input = 1
        self.ime_mode = True                  # 标题默认中文
        self.ime_clear()

    def _ns_input_close(self):
        """收掉加/改源输入 (整链作废)."""
        self.ns_input = 0
        self.ns_url = ""
        self.ns_edit_id = None
        self.buf = ""
        self.ime_clear()

    def news_feed_commit(self):
        """标题相 Enter (地址已在 ns_url): 新增 (标题+地址; id=c_+md5[:8]
        同址幂等, 重加=恢复显示) 或编辑落位 (id/on 不变, 可改标题+地址).
        标题空 → 域名兜底; 地址无效/与他源重址 → OSD 拒绝."""
        url = self.ns_url
        title = (self.buf or "").strip()
        edit_id = self.ns_edit_id
        self._ns_input_close()
        if url and "://" not in url:
            url = "https://" + url
        host = ""
        try:
            host = urllib.parse.urlparse(url).hostname or ""
        except ValueError:
            host = ""
        if not url.startswith(("http://", "https://")) or "." not in host:
            self.osd = ("地址无效 需 http(s)://", time.monotonic() + 2.5)
            return
        url = url[:200]
        old = next((x for x in self.news_feeds
                    if x.get("id") == edit_id), None) if edit_id else None
        if edit_id and not old:
            return                          # 编辑目标已消失 (云端覆盖竞态)
        fid = old["id"] if old else \
            "c_" + hashlib.md5(url.encode()).hexdigest()[:8]
        dup = next((x for x in self.news_feeds if x is not old and
                    (x.get("id") == fid or x.get("url") == url)), None)
        if old:
            if dup:
                self.osd = ("该地址已有源", time.monotonic() + 2.5)
                return
            old["url"] = url
            old["name"] = (title or old.get("name") or host)[:20]
            self.news_feeds_save()
            self._news_feeds_osd("已改 %s" % old["name"])
            return
        if dup:
            dup["on"] = True                # 重加同址 = 恢复显示
            self.news_feeds_save()
            self._news_feeds_osd("%s 已存在 已显示"
                                 % (dup.get("name") or host))
            return
        if len(self.news_feeds) >= NEWS_FEEDS_MAX:
            self.osd = ("最多 %d 个源" % NEWS_FEEDS_MAX,
                        time.monotonic() + 2.5)
            return
        name = title or (host[4:] if host.startswith("www.") else host)
        self.news_feeds.append({"id": fid, "name": name[:20],
                                "url": url, "on": True})
        self.ns_sel = len(self.news_feeds) - 1     # 光标落新源
        self.ns_scroll = max(0, self.ns_sel - 6)
        self.news_feeds_save()
        self._news_feeds_osd("已加 %s" % name)

    def news_feed_del(self, fid):
        """确认框 Enter 后删自定义源 (内置源只可隐藏不可删)."""
        f = next((x for x in self.news_feeds if x.get("id") == fid), None)
        if not f or not str(f.get("id", "")).startswith("c_"):
            return
        self.news_feeds.remove(f)
        self.ns_sel = min(self.ns_sel, max(0, len(self.news_feeds) - 1))
        self.news_feeds_save()
        self._news_feeds_osd("已删 %s" % (f.get("name") or fid))

    def move_news(self, d):
        n = len(self.news_items())
        if not n:
            return
        new = self.news_sel + d
        if new < self.news_scroll:                # 越过页顶 → 上一页
            self.news_scroll = max(0, self.news_scroll - 7)
            self.news_sel = self.news_scroll      # 选中落新页首
        elif new >= self.news_scroll + 7:         # 越过页底 → 下一页
            self.news_scroll = min(self.news_scroll + 7, max(0, n - 7))
            self.news_sel = self.news_scroll
        else:
            self.news_sel = max(0, min(n - 1, new))

    def draw_news(self, surf):
        hi, soft, dim = self.th("hi"), self.th("soft"), self.th("dim")
        acc = self.th("accent")
        track = self.th("track")
        BODY = 15
        srcs = self.news_srcs()
        src_name = srcs[self.news_src_i % len(srcs)][1] if srcs else ""

        items = self.news_items()

        if self.news_detail:
            # 详情视图: 头 (源 · 时间) + 全文滚动
            it = self.news_detail
            text(surf, "%s · %s" % (src_name, it.get("ts", "")), 11, dim,
                 (6, 2))
            t_lines = self._wrap_lines(it.get("t", ""), BODY, W - 12, 999)
            d_lines = self._wrap_lines(it.get("d", "") or "（无摘要）",
                                       BODY, W - 12, 999)
            lines = [(ln, hi) for ln in t_lines] + [(ln, soft) for ln in d_lines]
            max_scroll = max(0, len(lines) - 7)
            self.news_detail_scroll = max(0,
                min(self.news_detail_scroll, max_scroll))
            lh = 18
            surf.set_clip((0, 14, W, 134))
            for k in range(7):
                li = self.news_detail_scroll + k
                if li >= len(lines):
                    break
                ln, col = lines[li]
                text(surf, ln, BODY, col, (6, 16 + k * lh))
            surf.set_clip(None)
            # 滚动指示三角 (右上=有上文, 右下=有下文)
            if self.news_detail_scroll > 0:
                pygame.draw.polygon(surf, dim,
                                    [(W - 14, 8), (W - 6, 8), (W - 10, 3)])
            if self.news_detail_scroll < max_scroll:
                pygame.draw.polygon(surf, dim,
                                    [(W - 14, 145), (W - 6, 145), (W - 10, 150)])
            return

        if self.news_set:
            # ── 源设置视图 (S 进): 全源列表 显隐/增删, 云同步状态右上 ──
            text(surf, "新闻源设置", 14, hi, (6, 2))
            text(surf, "· %d 源" % len(self.news_feeds), 11, dim,
                 (6 + textw("新闻源设置", 14) + 6, 4))
            tok = self.cfg.get("cloud_tok")
            text(surf, "云✓" if tok else "本地", 10, acc if tok else dim,
                 (W - 6, 4), right=True)
            y0, rh = 17, 18
            for i in range(7):
                idx = self.ns_scroll + i
                if idx >= len(self.news_feeds):
                    break
                f = self.news_feeds[idx]
                y = y0 + i * rh
                sel = idx == self.ns_sel
                on = f.get("on", True)
                if sel:
                    pygame.draw.rect(surf, track, (2, y, W - 4, rh - 1),
                                     border_radius=3)
                    pygame.draw.rect(surf, acc, (2, y, 2, rh - 1))
                text(surf, str(idx - self.ns_scroll + 1), BODY,
                     acc if sel else dim, (8, y + 1))
                text(surf, clip_text(str(f.get("name") or f.get("id") or ""),
                                     BODY, W - 96),
                     BODY, hi if sel else (soft if on else dim), (26, y + 1))
                if str(f.get("id", "")).startswith("c_"):
                    text(surf, "自", 10, dim, (W - 22, y + 3), right=True)
                text(surf, "显" if on else "隐", 10, acc if on else dim,
                     (W - 6, y + 3), right=True)
            text(surf, "↑↓ 选 · Enter 显/隐", 13, soft, (6, 151))
            text(surf, "N加 · E改 · D删 · Esc 退", 13, soft, (W - 6, 151),
                 right=True)
            if self.ns_input:
                # 加/改源输入条 (两相: 标题[拼音]→地址[EN]; 长文本保尾段
                # 前缀 … — 正在敲的是尾部, 头截尾留才看得出对错)
                r = pygame.Rect(4, 116, W - 8, 27)
                pygame.draw.rect(surf, self.th("track"), r, border_radius=5)
                pygame.draw.rect(surf, acc, r, 2, border_radius=5)
                ty = r.y + (27 - 13) // 2
                lab = ("改" if self.ns_edit_id else "") + \
                      ("标题" if self.ns_input == 1 else "地址")
                text(surf, lab, 11, acc, (r.x + 8, ty + 1))
                lx = r.x + 8 + textw(lab, 11) + 8
                if self.ns_input == 1:
                    tag = "拼" if self.ime_mode else "EN"
                    text(surf, tag, 10, acc, (lx, ty + 1))
                    lx += textw(tag, 10) + 6
                    raw = self.buf + self.ime_buf
                    hint = "Enter 存 · / 中英 · Esc 退"
                else:
                    raw = self.buf
                    hint = "Enter 下步 · Esc 退"
                vw = max(50, r.right - 8 - textw(hint, 9) - 8 - lx)
                shown = tail_text(raw, BODY, vw)
                text(surf, shown, BODY, hi, (lx, ty))
                if int(time.monotonic() * 2) % 2 == 0:
                    cx2 = lx + textw(shown, BODY) + 1
                    pygame.draw.rect(surf, acc, (cx2, ty + 2, 7, 13))
                text(surf, hint, 9, dim, (r.right - 8, ty + 3), right=True)
                if self.ns_input == 1:
                    self.ime_bar(surf, 146)
            return

        # 列表头
        text(surf, "新闻", 14, hi, (6, 2))
        text(surf, ("· %s %d 条" % (src_name, len(items))) if srcs
             else "· 源已全部隐藏", 11, dim, (6 + textw("新闻", 14) + 6, 4))

        # 列表行: 均匀分布
        rows = 7
        y0, rh = 17, 18
        for i in range(rows):
            idx = self.news_scroll + i
            if idx >= len(items):
                break
            it = items[idx]
            y = y0 + i * rh
            sel = idx == self.news_sel
            if sel:
                pygame.draw.rect(surf, track, (2, y, W - 4, rh - 1),
                                 border_radius=3)
                pygame.draw.rect(surf, acc, (2, y, 2, rh - 1))
            text(surf, str(idx - self.news_scroll + 1), BODY,
                 acc if sel else dim, (8, y + 1))
            text(surf, clip_text(it.get("t", ""), BODY, 226),
                 BODY, hi if sel else soft, (26, y + 1))
            if it.get("ts"):
                text(surf, it["ts"], 10, dim, (W - 6, y + 2), right=True)

        if not srcs:
            textc(surf, "S 进源设置 开启或添加源", BODY, dim, W // 2, 70)
        elif not items:
            textc(surf, "新闻获取中" if self.net.mode != "offline"
                  else "离线 · 联网后同步", BODY, dim, W // 2, 70)
        text(surf, "P 翻页 · 1-7 打开 · ←→ 源", BODY, soft, (6, 151))
        text(surf, "S 设置", BODY, acc, (W - 6, 151), right=True)

    def draw_tool(self, surf):
        {"fx": self.draw_fx, "unit": self.draw_unit,
         "cal": self.draw_cal_app, "lot": self.draw_lot,
         "timer": self.draw_timer, "music": self.draw_mu,
         "mail": self.draw_mail, "tv": self.draw_tv,
         "set": self.draw_set,
         "sched": self.draw_sched}[self.tool](surf)

    def draw_tools(self, surf):
        acc, hi, soft = self.th("accent"), self.th("hi"), self.th("soft")
        border = self.th("border")
        # 无信息栏, 卡片满屏: 四边距离 = 行列间距 (4px)
        cols, gap, edge = TOOL_GRID, 4, 4
        n = len(TOOL_APPS)
        rows = (n + cols - 1) // cols
        cw = (W - edge * 2 - gap * (cols - 1)) // cols
        ch = (H - edge * 2 - gap * (rows - 1)) // rows
        for i, (tid, name) in enumerate(TOOL_APPS):
            r, c = divmod(i, cols)
            x = edge + c * (cw + gap)
            y = edge + r * (ch + gap)
            sel = i == self.tsel
            rect = pygame.Rect(x, y, cw, ch)
            pygame.draw.rect(surf, self.card(0), rect, border_radius=5)
            pygame.draw.rect(surf, acc if sel else border, rect,
                             2 if sel else 1, border_radius=5)
            text(surf, str((i + 1) % 10), 10, acc, (x + 5, y + 3))
            textc(surf, name, 15, hi if sel else soft, x + cw // 2,
                  y + (ch - 15) // 2)

    # ── 工具·万年历 (原工具页月视图收编) ──
    def draw_cal_app(self, surf):
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        y, m = self._cal_ym()
        # 无标题栏: 年月农历即标题, 右上只留键提示
        text(surf, "←→ 月 · ↑↓ 年 · n 今天", 9, dim, (W - 6, 5), right=True)
        text(surf, "%d年%d月 · 农历%s月" % (
            y, m, lunar.LM_CHARS[lunar.solar_to_lunar(y, m, 1)[1] - 1]
            if lunar.solar_to_lunar(y, m, 1) else "?"), 14, hi, (6, 3))
        for i, wd in enumerate(("日", "一", "二", "三", "四", "五", "六")):
            col = self.th("danger") if i == 0 else (acc if i == 6 else dim)
            text(surf, wd, 11, col, (6 + i * 45 + 13, 31))
        import calendar as _cal
        first_wd, days = _cal.monthrange(y, m)
        ly, lm = (y, m - 1) if m > 1 else (y - 1, 12)
        prev_days = _cal.monthrange(ly, lm)[1]
        t = time.localtime()
        cy0 = 44
        for row in range(6):
            for col in range(7):
                idx = row * 7 + col - first_wd          # 0 = 1 号
                if idx < 0:
                    d, dim_day = prev_days + idx + 1, True
                elif idx >= days:
                    d, dim_day = idx - days + 1, True
                else:
                    d, dim_day = idx + 1, False
                cx = 6 + col * 45
                cyy = cy0 + row * 20
                today = (not dim_day and d == t.tm_mday and
                         (y, m) == (t.tm_year, t.tm_mon))
                if today:
                    pygame.draw.rect(surf, self.th("hero"), (cx, cyy, 34, 18),
                                     border_radius=4)
                dcol = dim if dim_day else (hi if today else soft)
                text(surf, str(d), 12, dcol, (cx + 3, cyy + 2))
                ld = lunar.solar_to_lunar(y, m, d) if not dim_day else None
                if ld:
                    lab = (lunar.LM_CHARS[ld[1] - 1] + "月") if d == 1 \
                        else lunar.LD_CHARS[ld[2] - 1]
                    text(surf, lab, 8, acc if d == 1 else dim, (cx + 18, cyy + 5))

    # ── 工具·汇率 ──
    def _cv_box(self, surf, x, y, w, s, focused, h=20, fs=12):
        soft, acc = self.th("soft"), self.th("accent")
        border = self.th("border")
        r = pygame.Rect(x, y, w, h)
        pygame.draw.rect(surf, self.card(0), r, border_radius=4)
        pygame.draw.rect(surf, acc if focused else border, r,
                         2 if focused else 1, border_radius=4)
        text(surf, s, fs, self.th("hi") if focused else soft,
             (x + 6, y + (h - fs) // 2))

    def draw_fx(self, surf):
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        self.tool_head(surf, "汇率", "e 兑换 · ←→ 字段", hfs=11)
        j = self.data.get("rate") or {}
        rates = (j.get("rates") or {}) if j.get("ok") else FX_FALLBACK
        try:
            amt = float(self.fx_amt)
        except ValueError:
            amt = 0.0
        fcode, tcode = FX_CODES[self.fx_from], FX_CODES[self.fx_to]
        f = self.cv_field
        # 主行: 金额/源/→/目标 加高放大, 水平均匀分布
        # (8 | 124 g12 60 g12 16(→) g12 68 | 8, 全宽 320)
        y0, bh = 30, 36
        self._cv_box(surf, 8, y0, 124, self.fx_amt, f == 0, h=bh, fs=17)
        self._cv_box(surf, 144, y0, 60, fcode, f == 1, h=bh, fs=16)
        textc(surf, "→", 18, dim, 224, y0 + bh // 2 - 9)
        self._cv_box(surf, 244, y0, 68, tcode, f == 2, h=bh, fs=16)
        textc(surf, FX_NAMES[fcode], 10, dim, 174, y0 + bh + 2)
        textc(surf, FX_NAMES[tcode], 10, dim, 278, y0 + bh + 2)
        v = amt / (rates.get(fcode) or 1.0) * (rates.get(tcode) or 1.0)
        textc(surf, "%s %s" % (fmt_num(round(v, 4)), FX_NAMES[tcode]),
              26, acc, W // 2, 92)
        u = (rates.get(tcode) or 1.0) / (rates.get(fcode) or 1.0)
        textc(surf, "1 %s = %s %s" % (fcode, fmt_num(round(u, 4)), tcode),
              12, soft, W // 2, 122)
        if j.get("updated"):
            label = "更新 " + time.strftime("%m-%d %H:%M",
                                            time.localtime(j["updated"]))
        else:
            label = "离线静态表"
        textc(surf, label, 10, dim, W // 2, 138)
        self.tool_foot(surf, ("数字 金额", "↑↓ 调整", "e 交换", "Esc 返回"))

    # ── 工具·单位换算 (与汇率同版式: 主行均匀分布 + 大结果) ──
    def draw_unit(self, surf):
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        cat, units = UCATS[self.u_cat]
        self.tool_head(surf, "单位换算", "r 换类 · e 交换", hfs=11)
        fd = dict(units)
        try:
            a = float(self.u_amt)
        except ValueError:
            a = 0.0
        fcode, tcode = units[self.u_from][0], units[self.u_to][0]
        f = self.cv_field
        # 主行: 金额/源/→/目标 与汇率同几何 (8|124 g12 60 g12 16(→) g12 68|8)
        y0, bh = 30, 36
        self._cv_box(surf, 8, y0, 124, self.u_amt, f == 0, h=bh, fs=17)
        self._cv_box(surf, 144, y0, 60, fcode, f == 1, h=bh, fs=16)
        textc(surf, "→", 18, dim, 224, y0 + bh // 2 - 9)
        self._cv_box(surf, 244, y0, 68, tcode, f == 2, h=bh, fs=16)
        # 类别 (字段3): 居中小胶囊
        lab = "◁ %s ▷" % cat
        cw_ = textw(lab, 12) + 16
        self._cv_box(surf, W // 2 - cw_ // 2, y0 + bh + 2, cw_, lab, f == 3,
                     h=20, fs=12)
        if cat == "温度":
            v = temp_convert(a, fcode, tcode)
            u = temp_convert(1, fcode, tcode)
        else:
            v = a * fd[fcode] / fd[tcode]
            u = fd[fcode] / fd[tcode]
        textc(surf, "%s %s" % (self._u_fmt(v), tcode), 26, acc, W // 2, 92)
        textc(surf, "1 %s = %s %s" % (fcode, self._u_fmt(u), tcode),
              12, soft, W // 2, 122)
        textc(surf, "%s · %d 单位" % (cat, len(units)), 10, dim, W // 2, 138)
        self.tool_foot(surf, ("数字 金额", "↑↓ 调整", "←→ 字段", "Esc 返回"))

    @staticmethod
    def _u_fmt(v):
        if abs(v) >= 1e12 or (0 < abs(v) < 1e-6):
            return "%.4g" % v
        if v == int(v) and abs(v) < 1e10:
            return "{:,d}".format(int(v))
        return "{:,}".format(float("%.6g" % v))

    # ── 工具·彩票 (双色球/大乐透) ──
    def draw_lot(self, surf):
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        key, name = self.LOT_TYPES[self.lot_type]
        self.tool_head(surf, name, "↑↓ 选期 · ←→ 彩种")
        j = self.data.get(key) or {}
        issues = (j.get("issues") or []) if j.get("ok") else []
        if not issues:
            textc(surf, "加载中…" if self.net.mode != "offline" else "离线 · 联网后同步",
                  12, dim, W // 2, 70)
            return
        n = len(issues)
        self.lot_scroll = max(0, min(self.lot_scroll, n - 1))
        it = issues[self.lot_scroll]
        # 顶部卡片 = 选中期, 期号/大球/奖金 全部水平居中
        r = pygame.Rect(4, 17, W - 8, 60)
        pygame.draw.rect(surf, self.card(0), r, border_radius=5)
        pygame.draw.rect(surf, self.th("border"), r, 1, border_radius=5)
        textc(surf, "第 %s 期 · %s 周%s" % (
            it.get("code", ""), str(it.get("date", ""))[5:],
            it.get("week", "")), 12, hi, W // 2, 21)
        reds = it.get("reds") or []
        blues = self._lot_blues(it)
        rad = 12
        bw = (len(reds) + len(blues) - 1) * (rad * 2 + 5) + 3 + rad * 2
        self._lot_balls(surf, reds, blues, (W - bw) // 2, 38, rad)
        g1 = self._lot_grade(it, 1)
        g2 = self._lot_grade(it, 2)
        textc(surf, "奖池 %s · 一等 %s 注 %s · 二等 %s 注 %s" % (
            self._lot_yi(it.get("pool")), g1[0], g1[1], g2[0], g2[1]),
            8, dim, W // 2, 65)
        # 历史窗口 4 行, 选中行高亮, 窗口跟随光标
        rows = 4
        view = self.lot_view
        if self.lot_scroll < view:
            view = self.lot_scroll
        if self.lot_scroll >= view + rows:
            view = self.lot_scroll - rows + 1
        self.lot_view = max(0, view)
        for k in range(rows):
            idx = self.lot_view + k
            if idx >= n:
                break
            h = issues[idx]
            y = 83 + k * 16
            selr = idx == self.lot_scroll
            if selr:
                hl = pygame.Rect(4, y - 2, W - 8, 15)
                pygame.draw.rect(surf, self.card(0), hl, border_radius=4)
                pygame.draw.rect(surf, acc, hl, 1, border_radius=4)
            text(surf, h.get("code", ""), 10, acc if selr else soft, (8, y))
            text(surf, str(h.get("date", ""))[5:], 9, dim, (66, y + 1))
            self._lot_balls(surf, h.get("reds") or [], self._lot_blues(h), 106, y, 6)
        text(surf, "%d/%d" % (self.lot_scroll + 1, n),
             8, dim, (W - 10, 147), right=True)

    @staticmethod
    def _lot_blues(it):
        """后区球列表: 大乐透 blues×2; 双色球 blue 单球 → 统一列表."""
        b = it.get("blues")
        if b is None:
            b = [it.get("blue") or 0]
        return b

    def _lot_balls(self, surf, reds, blues, x, y, rad):
        fs = 7 if rad <= 6 else (10 if rad <= 9 else 13)
        # 数字在球内垂直居中: 用渲染表面字形包围盒中心对圆心 (设备 freetype
        # get_metrics 段错误不可用; 行框含留白, fs//2 经验值会偏)
        yo = y + rad - _ball_glyph_cy(fs)
        step = rad * 2 + 5
        gap = 3 if blues else 0               # 前/后区之间留 3px
        items = ([(self.LOT_RED, v) for v in reds] +
                 [(self.LOT_BLUE, v) for v in blues])
        for i, (col, v) in enumerate(items):
            cx = x + i * step + (gap if i >= len(reds) else 0)
            pygame.draw.circle(surf, col, (cx + rad, y + rad), rad)
            s = "%02d" % v
            text(surf, s, fs, (255, 255, 255),
                 (cx + rad - textw(s, fs) // 2, yo))

    @staticmethod
    def _lot_grade(it, level):
        for g in (it.get("grades") or []):
            if g.get("level") == level:
                p = g.get("money") or 0
                return (g.get("num") or 0,
                        "浮动" if not p else
                        ("%.2f 万" % (p / 1e4)) if p >= 1e4 else ("%d 元" % p))
        return (0, "浮动")

    @staticmethod
    def _lot_yi(v):
        return "%.2f 亿" % ((v or 0) / 1e8)

    # ── 家居页: HA 设备控制 ──
    # ── 工具·照片 ──
    @staticmethod
    def _cover_blit(surf, img, rect):
        w, h = img.get_size()
        scale = max(rect.w / w, rect.h / h)
        nw, nh = max(rect.w, int(w * scale + 0.5)), max(rect.h, int(h * scale + 0.5))
        img2 = pygame.transform.smoothscale(img, (nw, nh))
        clip = surf.get_clip()
        surf.set_clip(rect)
        surf.blit(img2, (rect.x + (rect.w - nw) // 2, rect.y + (rect.h - nh) // 2))
        surf.set_clip(clip)

    # ── 工具·计时器 ──
    def draw_timer(self, surf):
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        self.tool_head(surf, "计时器", "")
        cx, cy, rad = W // 2, 76, 40          # 底缘 116, 让开 y132 预设行
        frac = min(1.0, (self.tm_left / self.tm_dur) if self.tm_dur else 0.0)
        col = acc if self.tm_run else self.th("dim")
        # 平滑圆环: 4x 超采样离屏面绘制 → smoothscale 缩回 (缩放插值即抗锯齿;
        # 设备无 AA 原语, draw.arc/circle 直画锯齿明显)。背景纯页面色, 不透明
        # 面即可, 避开设备会段错误的 SRCALPHA/freetype 路径。按 (进度, 颜色,
        # 主题) 缓存, 进度按秒变 → 每秒才重画超采样面, 常规帧只 blit。
        key = (round(frac, 4), col, self.th("track"), self.th("page"),
               self.theme_idx)
        if getattr(self, "_ring_key", None) != key:
            SS = 4
            size = (rad + 2) * 2 * SS
            tmp = pygame.Surface((size, size))
            tmp.fill(self.th("page"))
            c = size // 2
            rr = rad * SS
            pygame.draw.circle(tmp, self.th("track"), (c, c), rr, 3 * SS)
            if frac > 0:
                nseg = max(2, int(180 * frac))
                pts = []
                for i in range(nseg + 1):
                    a = -math.pi / 2 + 2 * math.pi * frac * i / nseg
                    pts.append((c + rr * math.cos(a), c + rr * math.sin(a)))
                pygame.draw.lines(tmp, col, False, pts, 3 * SS)
                for p in (pts[0], pts[-1]):   # 圆头端点
                    pygame.draw.circle(tmp, col, (int(p[0]), int(p[1])), SS)
            self._ring = pygame.transform.smoothscale(
                tmp, ((rad + 2) * 2, (rad + 2) * 2))
            self._ring_key = key
        surf.blit(self._ring, (cx - rad - 2, cy - rad - 2))
        s = max(0, math.ceil(self.tm_left))
        disp = ("%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60)
                if s >= 3600 else "%02d:%02d" % (s // 60, s % 60))
        textc(surf, disp, 22 if s < 3600 else 18,
              hi if (self.tm_run or self.tm_left) else dim, cx, cy - 13)
        if self.tm_done:
            st = "计时结束!"
        elif self.tm_run:
            st = "运行至 %s" % time.strftime(
                "%H:%M", time.localtime(time.time() + self.tm_left))
        elif self.tm_left < self.tm_dur:
            st = "已暂停"
        else:
            st = "数字 1-6 选预设"
        textc(surf, st, 9, soft, cx, cy + 10)
        # 预设按钮 6×30 + 5×4 = 200px, 水平居中
        x0 = (W - (len(TM_PRESETS) * 30 + (len(TM_PRESETS) - 1) * 4)) // 2
        for i, mn in enumerate(TM_PRESETS):
            r = pygame.Rect(x0 + i * 34, 132, 30, 15)
            on = self.tm_dur == mn * 60 and not self.tm_run
            pygame.draw.rect(surf, self.card(0), r, border_radius=4)
            pygame.draw.rect(surf, acc if on else self.th("border"), r,
                             2 if on else 1, border_radius=4)
            text(surf, str(i + 1), 7, acc, (r.x + 2, r.y + 1))
            textc(surf, "%d分" % mn, 9, acc if on else soft, r.centerx, r.y + 2)
        self.tool_foot(surf, ("Enter 播/停", "r 重置", "←→ ±1分", "↑↓ ±10s"))

    # ── 工具·音乐 (左 文本/进度/歌词, 右 封面+VU) ──
    def draw_mu(self, surf):
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        border = self.th("border")
        self.mu_tick()                        # 播放页每帧检测 (连播/出错即停)
        if self.mu_view == "play":
            self._draw_mu_play(surf)
            return
        if self.mu_view == "qr":
            self._draw_mu_qr(surf)
            return
        if self.mu_view in ("search", "fav"):
            self._draw_mu_rows(surf)
            return
        if self.mu_view == "list":
            self.tool_head(surf, "本地曲库", "%d 首" % len(self.mu_files))
            if not self.mu_files:
                textc(surf, "~/music 为空", 12, dim, W // 2, 70)
                textc(surf, "scp 拷入 mp3 / flac / wav 即自动出现", 10, dim,
                      W // 2, 92)
                self.tool_foot(surf, ("r 重扫", "9 选源", "Esc 返回"))
                return
            nrow = 4
            r0 = max(0, min(self.mu_sel - (nrow - 1), max(0,
                            len(self.mu_files) - nrow)))
            for kk in range(nrow):
                i = r0 + kk
                if i >= len(self.mu_files):
                    break
                f = self.mu_files[i]
                y = 24 + kk * 30
                sel = i == self.mu_sel
                cur = self.mu_playing and i == self.mu_play_idx
                rct = pygame.Rect(4, y, W - 8, 27)
                pygame.draw.rect(surf, self.card(0), rct, border_radius=4)
                pygame.draw.rect(surf, acc if sel else border, rct,
                                 2 if sel else 1, border_radius=4)
                if cur:
                    text(surf, "▶" if self.mu_proc else "⏸", 12, acc,
                         (10, y + 4))
                label = f.get("title") or "(未命名)"
                meta = (f.get("artist", "") + " · " if f.get("artist") else "") \
                    + (f.get("ext") or "本地").lstrip(".")
                text(surf, clip_text(label, 12, W - 110), 12,
                     hi if sel else soft, (28 if cur else 12, y + 2))
                text(surf, clip_text(meta, 9, W - 110), 9, dim,
                     (28 if cur else 12, y + 15))
            self.tool_foot(surf, ("↑↓ 选", "Enter 播", "r 重扫",
                                  "9 选源", "Esc 返回"))
            return
        # src 源选择 (想法页卡片风; 卡底不压 y151 底栏)
        self.tool_head(surf, "音乐", "♪ 播放中" if self.mu_playing else "")
        for i, (sid, name, note) in enumerate(MUSIC_SRCS):
            y = 24 + i * 42
            sel = i == self.mu_sel
            locked = sid == "ne"              # M3 网易云锁定
            rct = pygame.Rect(8, y, W - 16, 38)
            pygame.draw.rect(surf, self.card(0 if not locked else 2), rct,
                             border_radius=5)
            pygame.draw.rect(surf, acc if sel else border, rct,
                             2 if sel else 1, border_radius=5)
            text(surf, name, 14, hi if sel else soft, (20, y + 3))
            if sid == "qq":
                note = ("已绑定 · Enter 搜索" if musrc.qq_bound()
                        else "扫码绑定 · Enter")
            elif sid == "ne":
                note = ("已绑定 · Enter 搜索" if musrc.ne_bound()
                        else "扫码绑定 · Enter")
            text(surf, note or "%d 首已就绪" % len(self.mu_files), 9,
                 dim, (20, y + 21))
        self.tool_foot(surf, ("↑↓ 选源", "Enter 进", "Esc 回播放"))
        # (输出键提示只在播放页 — 2026-11-14 用户定版)

    def _draw_mu_qr(self, surf):
        """QQ 扫码页: 矩阵 rect 直绘 + 状态行 (q 取消)."""
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        st = self.mu_qr.state
        self.tool_head(surf, "%s · 扫码绑定"
                       % ("QQ 音乐" if self.mu_qr_kind == "qq" else "网易云"),
                       "")
        mat = self.mu_qr.matrix
        if mat:
            n = len(mat)
            px = min(130 // n, 4)
            ox = (W - n * px) // 2
            oy = 16                    # 底 16+116=132, 让位状态行 y138
            for my in range(n):
                for mx in range(n):
                    if mat[my][mx]:
                        pygame.draw.rect(surf, hi,
                                         (ox + mx * px, oy + my * px,
                                          px, px))
        if st == "qr":
            appname = "网易云 APP" if self.mu_qr_kind == "ne" else "手机 QQ"
            tip = "%s 扫一扫" % appname if self.mu_qr.matrix else "生成二维码…"
        elif st == "scanned":
            tip = "已扫描 · 请在手机上确认"
        elif st == "ok":
            tip = "绑定成功"
        elif st == "fail":
            tip = "失败: %s" % self.mu_qr.err[:24]
        else:
            tip = ""
        textc(surf, tip, 11, acc if st in ("scanned", "ok") else soft,
              W // 2, 138)
        self.tool_foot(surf, ("过期自动刷新 · s 免登录", "q 取消")
                       if self.mu_qr_kind == "ne"
                       else ("过期自动刷新", "q 取消"))

    def _draw_mu_rows(self, surf):
        """搜索/收藏列表 (共用行渲染; 输入态画搜索行)."""
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        border = self.th("border")
        is_fav = self.mu_view == "fav"
        srcname = "网易云" if self.mu_src == "ne" else "QQ"
        title = srcname + (" 我喜欢" if is_fav else " 搜索")
        bound = (musrc.ne_bound() if self.mu_src == "ne"
                 else musrc.qq_bound())
        hint = ("6 搜索" if is_fav else
                "n 关键词 · %s" % ("已绑定" if bound else "免登录"))
        self.tool_head(surf, title, hint)
        if self.mu_input:
            r = pygame.Rect(24, 68, W - 30, 26)   # 屏幕中间 (原 y75 h19 太局促, 底 94 不动)
            pygame.draw.rect(surf, self.card(0), r, border_radius=4)
            pygame.draw.rect(surf, acc, r, 2, border_radius=4)
            _it, _ib = glyph_vbox("拼", 11)        # CJK 参考墨迹范围
            cy = r.y + (r.height - (_ib - _it + 1)) // 2 - _it   # 墨迹居中
            tag = "拼" if self.ime_mode else "EN"
            text(surf, tag, 11, acc, (r.x + 6, cy))
            cur = "_" if time.monotonic() % 1 < 0.5 else ""
            q = self.mu_q + self.ime_buf + cur
            text(surf, clip_text(q or "键入关键词…", 11, W - 50), 11,
                 hi if self.mu_q or self.ime_buf else dim,
                 (r.x + 6 + textw(tag, 11) + 6, cy))
            if not self.ime_bar(surf, 97):
                textc(surf, "拼音 · / 英 · Enter 搜", 10, dim, W // 2, 104)
            self.tool_foot(surf, ("Enter 搜", "Esc 退"))
            return
        if self.mu_busy:
            textc(surf, "取数中… (4 播放需等此步)", 11, dim, W // 2, 80)
            return
        if not self.mu_files:
            textc(surf, "无结果" if self.mu_q or is_fav
                  else "n 搜索", 11, dim, W // 2, 80)
            self._mu_rows_foot(surf, is_fav)
            return
        nrow = 4
        r0 = max(0, min(self.mu_sel - (nrow - 1),
                        max(0, len(self.mu_files) - nrow)))
        for kk in range(nrow):
            i = r0 + kk
            if i >= len(self.mu_files):
                break
            f = self.mu_files[i]
            y = 24 + kk * 30
            sel = i == self.mu_sel
            cur = self.mu_playing and i == self.mu_play_idx
            rct = pygame.Rect(4, y, W - 8, 27)
            pygame.draw.rect(surf, self.card(0), rct, border_radius=4)
            pygame.draw.rect(surf, acc if sel else border, rct,
                             2 if sel else 1, border_radius=4)
            if cur:
                text(surf, "▶" if self.mu_proc else "⏸", 12, acc, (10, y + 4))
            label = f["title"] if f.get("artist") else \
                f.get("title") or "(未命名)"
            meta = f.get("artist", "")
            if f.get("preview"):
                meta = (meta + " · " if meta else "") + "试听"
            elif f.get("dur"):
                meta = (meta + " · " if meta else "") + "%d:%02d" \
                    % (f["dur"] // 60, f["dur"] % 60)
            text(surf, clip_text(label, 12, W - 110), 12,
                 hi if sel else soft, (28 if cur else 12, y + 2))
            text(surf, clip_text(meta, 9, W - 110), 9, dim,
                 (28 if cur else 12, y + 15))
        self._mu_rows_foot(surf, is_fav)

    def _mu_rows_foot(self, surf, is_fav):
        """搜索/收藏底栏 (自绘, 用户指定键位与间距): 4 播放 · 2 格 · 5 收藏
        (fav=r 刷新) · 2 格 · 6 返回; 右侧 8 停止 — 输出键提示只在播放页
        (2026-11-14 用户定版: 搜索页两处"输出"都删)."""
        soft, dim = self.th("soft"), self.th("dim")
        acc = self.th("accent")
        text(surf, "　　4 播放　   5 收藏　  6 返回", 12,
             soft, (6, 151))
        text(surf, "4 播放", 12, acc, (6 + textw("　　", 12), 151))
        tail = "8 停止"
        text(surf, tail, 12, dim, (W - 6 - textw(tail, 12), 151))

    def _draw_mu_play(self, surf):
        acc, hi, soft, dim = (self.th("accent"), self.th("hi"),
                              self.th("soft"), self.th("dim"))
        track = self.th("track")
        warn = self.th("warn")
        p = self.mu_playing or {}
        self.tool_head(surf, "音乐", "")      # 右上角提示不要 (2026-11-16
                                              #   用户定版; 输出名只在 OSD)
        playing = bool(self.mu_proc) and not self.mu_paused
        # VU 电平 (播放=pulse monitor / ffmpeg fd3; 暂停=回落静默衰减)
        lvb, lvm, lvt = self.lvl.lvl if playing else (0.0, 0.0, 0.0)
        self.vu_tick(time.monotonic(), lvb, lvm, lvt)
        # 本机出声锚点: VU 首响定位 (2026-11-14) — HTTP 直连预缓冲 1-3s,
        # 旧写法"起进程=出声"使歌词抢跑; pulse monitor 首个过阈帧=真出声,
        # -0.15s 补监视器链路; 5s 静音曲头/监视器失效兜底回旧值.
        # AirPlay 路径不用 VU (fifo 是产端时序) — _mu_raop_watch 用
        # RAOP-UP+1.2s 定锚
        if self.mu_aud_up is None and playing and self.mu_ff is None:
            if lvb > 2.0 or lvm > 2.0:
                self.mu_aud_up = time.monotonic() - 0.15
            elif (self.mu_proc and self.mu_proc_at
                  and time.monotonic() - self.mu_proc_at > 5.0):
                self.mu_aud_up = self.mu_proc_at
        # 左栏: 标题 (折2) / 歌手 / 歌词当前行 (折2) / 进度条
        ty = 24
        for ln in self._wrap_lines(p.get("title") or "(无标题)", 15, 204, 2):
            text(surf, ln, 15, hi, (8, ty))
            ty += 20
        if p.get("artist"):
            text(surf, clip_text(p["artist"], 10, 204), 10, soft, (8, ty + 1))
        pos = self.mu_wall_pos()
        cur = None
        for sec, ln in self.mu_lyr:           # 当前行: 时间戳 ≤ pos 的最后行
            if sec > pos + 0.2:
                break
            cur = ln or cur
        if cur:
            ly = 80
            for ln in self._wrap_lines(cur, 13, 204, 2):
                text(surf, ln, 13, acc, (8, ly))
                ly += 18
        if self.mu_dur:
            bw = 196
            by = 122
            pygame.draw.rect(surf, track, (8, by, bw, 6), border_radius=3)
            frac = max(0.0, min(1.0, pos / self.mu_dur))
            pygame.draw.rect(surf, acc, (8, by, max(4, int(bw * frac)), 6),
                             border_radius=3)
            text(surf, mmss(pos), 9, soft, (8, by + 10))
            text(surf, mmss(self.mu_dur), 9, dim, (8 + bw, by + 10), right=True)
            if self.mu_paused:
                text(surf, "⏸ 已暂停", 10, warn, (8 + bw // 2 - 24, by + 10))
        # 右栏: 封面 84×84 (player 同位) + VU 12 条 (封面下方, radio 迷你版)
        cr = pygame.Rect(222, 22, 84, 84)
        if self.mu_pic is None and self.mu_pic_raw:
            self.mu_pic, self.mu_pic_raw = jpg_surf(self.mu_pic_raw, 84, 84), \
                None                          # 主线程转 (一次; 失败即占位)
        if self.mu_pic:
            surf.blit(self.mu_pic, (222, 22))
        else:
            pygame.draw.rect(surf, self.card(0), cr, border_radius=5)
            textc(surf, "无封面", 10, dim, cr.centerx, cr.centery - 6)
        nb, bw2, bgap, vh = 12, 6, 1.4, 34
        vx0 = cr.x + (cr.width - (nb * bw2 + (nb - 1) * bgap)) // 2
        vy = 112
        for i in range(nb):
            bxx = vx0 + i * (bw2 + bgap)
            pygame.draw.rect(surf, track, (bxx, vy, bw2, vh), border_radius=2)
            fh = max(3, min(vh, int(self.vu[i] / 28.0 * vh)))
            pygame.draw.rect(surf, acc,
                             (bxx, vy + vh - fh, bw2, fh), border_radius=2)
            pk = max(3, min(vh, int(self.vu_pk[i] / 28.0 * vh)))
            pygame.draw.rect(surf, hi, (bxx, vy + vh - pk, bw2, 2))
        if self.esc_hold_at is not None:      # 长按中: 退出条遮底栏 + 常显退出中
            frac = min(1.0, (time.monotonic() - self.esc_hold_at) / 1.0)
            self.draw_exit_bar(surf, frac)
        else:
            foot = ("4 播/停", "5 输出", "↑↓ 切曲", "7 停止", "8 选源")
            slot = W // len(foot)         # 5 标签槽内居中 = 水平均分
            for i, lab in enumerate(foot):   #   (2026-11-16 用户定版)
                text(surf, lab, 12, self.th("soft"),
                     (i * slot + (slot - textw(lab, 12)) // 2, 151))

    def draw_tabs(self, surf):
        """快捷入口条: 仅主页显示 (其他页长按 Esc 回主页), 不含主页自身;
        8 邮件 9 工具 (2026-11-16 用户定版, 邮件与工具对调).
        工具开着时一律不画 (主页 8 直开邮件 page 保持 0, 页签会压工具底栏)."""
        if self.page != 0 or self.tool:
            return
        y = H - 22
        entries = [(n, str(4 + j)) for j, (n, _) in enumerate(PAGES[1:5])]
        entries += [("邮件", "8"), ("工具", "9")]
        cw = W // len(entries)
        for j, (name, num) in enumerate(entries):
            x = j * cw
            text(surf, num, 12, self.th("accent"), (x + 6, y + 6))
            text(surf, name, 15, self.th("soft"), (x + 20, y + 3))

    def draw_osd(self, surf):
        if not self.osd:
            return
        if time.monotonic() >= self.osd[1]:
            self.osd = None
            return
        w = textw(self.osd[0], 11) + 16
        r = pygame.Rect(W - w - 4, 20, w, 18)
        pygame.draw.rect(surf, self.card(0), r, border_radius=4)
        pygame.draw.rect(surf, self.th("border"), r, 1, border_radius=4)
        text(surf, self.osd[0], 11, self.th("hi"), (W - 12, 23), right=True)

    def draw_dialog(self, surf):
        if not self.dialog:
            return
        dim = pygame.Surface((W, H), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 150))
        surf.blit(dim, (0, 0))
        r = pygame.Rect(W // 2 - 90, H // 2 - 28, 180, 56)
        pygame.draw.rect(surf, self.card(1), r, border_radius=6)
        pygame.draw.rect(surf, self.th("border"), r, 1, border_radius=6)
        if self.dialog[0] == "exit":
            textc(surf, "退出 ONEAPP?", 12, self.th("accent"), W // 2, H // 2 - 18)
        elif self.dialog[0] == "unfav":
            textc(surf, "取消收藏 %s?" % (self.dialog[2] or self.dialog[1]),
                  12, self.th("accent"), W // 2, H // 2 - 18)
        elif self.dialog[0] == "untvfav":
            textc(surf, "取消收藏 %s?" % (self.dialog[1] or ""),
                  12, self.th("accent"), W // 2, H // 2 - 18)
        elif self.dialog[0] == "delwatch":
            textc(surf, "删除自选股 %s?" % (self.dialog[1] or ""),
                  12, self.th("accent"), W // 2, H // 2 - 18)
        elif self.dialog[0] == "delnfeed":
            f = next((x for x in self.news_feeds
                      if x.get("id") == self.dialog[1]), None)
            textc(surf, "删除新闻源 %s?" % ((f or {}).get("name")
                                           or self.dialog[1] or ""),
                  12, self.th("accent"), W // 2, H // 2 - 18)
        else:
            it = self.dialog[1] or {}
            textc(surf, "删除这条想法?", 12, self.th("accent"), W // 2, H // 2 - 20)
            textc(surf, clip_text((it.get("text") or "(语音)")[:24], 9, 164),
                  9, self.th("soft"), W // 2, H // 2 - 6)
        textc(surf, "Enter 确认 · Esc 取消", 9, self.th("soft"), W // 2, H // 2 + 2)


# ── 设备帧缓冲: 与内置 APP 同路径 (/dev/fb_lcd 直写) ────────────────────────
class Framebuffer:
    def __init__(self):
        self.dev = open("/dev/fb_lcd", "r+b", buffering=0)
        bpp = 16
        try:
            bpp = int(open("/sys/class/graphics/fb_lcd/bits_per_pixel").read().strip())
        except Exception:
            pass
        if bpp == 32:
            self.conv = pygame.Surface((W, H), depth=32,
                                       masks=(0xFF0000, 0xFF00, 0xFF, 0xFF000000))
        else:                                   # RGB565 (little-endian)
            self.conv = pygame.Surface((W, H), depth=16,
                                       masks=(0xF800, 0x07E0, 0x001F, 0))
        self.mm = mmap.mmap(self.dev.fileno(), W * H * bpp // 8)

    def blit(self, canvas):
        self.conv.blit(canvas, (0, 0))
        self.mm.seek(0)
        self.mm.write(self.conv.get_buffer().raw)

    def close(self):
        try:
            self.mm.close()
        finally:
            self.dev.close()


# ── 设备键盘: 裸 evdev (TCA8418) → pygame 事件 ──────────────────────────────
EV_KEY = 0x01

def find_keyboard():
    for name_f in glob.glob("/sys/class/input/input*/name"):
        try:
            if open(name_f).read().strip() != "tca8418c":
                continue
            base = os.path.dirname(name_f)
            for dev_dir in glob.glob(base + "/event*"):
                n = os.path.basename(dev_dir)
                if os.path.exists("/dev/input/" + n):
                    return "/dev/input/" + n
        except Exception:
            continue
    return None


# 方向键 = fn 层 (fn+F/X/Z/C → 内核发 103/108/105/106, 官方 fn 表同款);
# 裸 F/Z/X/C 是字母 (输入态打拼音/英文必需)
EVDEV_MAP = {
    1: pygame.K_ESCAPE,
    2: pygame.K_1, 3: pygame.K_2, 4: pygame.K_3, 5: pygame.K_4, 6: pygame.K_5,
    7: pygame.K_6, 8: pygame.K_7, 9: pygame.K_8, 10: pygame.K_9, 11: pygame.K_0,
    12: pygame.K_MINUS, 13: pygame.K_EQUALS,
    26: pygame.K_LEFTBRACKET, 27: pygame.K_RIGHTBRACKET,
    39: pygame.K_SEMICOLON, 40: pygame.K_QUOTE, 41: pygame.K_BACKQUOTE,
    43: pygame.K_BACKSLASH, 51: pygame.K_COMMA, 52: pygame.K_PERIOD,
    14: pygame.K_BACKSPACE, 15: pygame.K_TAB, 28: pygame.K_RETURN,
    96: pygame.K_KP_ENTER, 111: pygame.K_DELETE, 57: pygame.K_SPACE,
    33: pygame.K_f, 44: pygame.K_z, 45: pygame.K_x, 46: pygame.K_c,
    102: pygame.K_HOME, 104: pygame.K_PAGEUP,              # fn+K/L
    107: pygame.K_END, 109: pygame.K_PAGEDOWN, 110: pygame.K_INSERT,
    103: pygame.K_UP, 108: pygame.K_DOWN, 105: pygame.K_LEFT, 106: pygame.K_RIGHT,
    16: pygame.K_q, 17: pygame.K_w, 18: pygame.K_e, 19: pygame.K_r, 20: pygame.K_t,
    21: pygame.K_y, 22: pygame.K_u, 23: pygame.K_i, 24: pygame.K_o, 25: pygame.K_p,
    30: pygame.K_a, 31: pygame.K_s, 32: pygame.K_d, 34: pygame.K_g, 35: pygame.K_h,
    36: pygame.K_j, 37: pygame.K_k, 38: pygame.K_l,
    47: pygame.K_v, 48: pygame.K_b, 49: pygame.K_n, 50: pygame.K_m,
    53: pygame.K_SLASH, 92: pygame.K_SLASH,    # IME 中/英切换 (设备 Fn+N 实发 92 — unmapped log 实证, 53=标准布局码)
}


# evdev code → 可打印字符 (输入模式用; 与 EVDEV_MAP 共存: 事件同时带 key 与 unicode)
EVDEV_CHARS = {
    2: "1", 3: "2", 4: "3", 5: "4", 6: "5", 7: "6", 8: "7", 9: "8", 10: "9", 11: "0",
    12: "-", 13: "=", 26: "[", 27: "]", 39: ";", 40: "'", 41: "`", 43: "\\",
    51: ",", 52: ".", 53: "/", 92: "/", 57: " ",
    16: "q", 17: "w", 18: "e", 19: "r", 20: "t", 21: "y", 22: "u", 23: "i",
    24: "o", 25: "p", 30: "a", 31: "s", 32: "d", 33: "f", 34: "g", 35: "h",
    36: "j", 37: "k", 38: "l", 44: "z", 45: "x", 46: "c", 47: "v", 48: "b",
    49: "n", 50: "m",
}

# ── ZERO 三修饰键 + sym 第二层解码 (2026-11-15 真机抓键 + 设备树 tca8418@34
#    三层键码表实解: sym 键=383, fn 键=389, shift/Aa=42; 内核在 sym 层换用
#    keymap1 码表且与丝印上档错位 — 故按物理格反查丝印符号, 不信内核码) ──
_SYM_CHARS = {                           # sym 层官方码表 (32 符号, sym 键按住时
    26: "!", 27: "@", 39: "#", 40: "$", 41: "%", 43: "^",   # 内核发这些码):
    51: "&", 52: "*", 53: "(", 94: ")",  # 与 CardputerZero/launcher
    55: "~", 69: "`", 70: "_", 71: "-", 72: "+", 73: "=",   # cp0_keyboard_keymap.c
    74: "[", 75: "]", 76: "{", 77: "}",  # default_keymap + 设备
    79: ";", 80: ":", 81: "'", 82: '"',  # /usr/share/keymaps/tca8418_keypad_m5stack_keymap.map
    83: "<", 85: ">", 86: "\\", 89: "|", # 完全同源: q..p=~/`_-+=[]{}; a..l=;:'"<>\|,
    90: ",", 91: ".", 92: "/", 93: "?",  # v..m=,./? (sym+b=. 用户实证)
}
_MOD_SYMS = (383, 389, 42)               # sym / fn / shift 键码 (不投事件)


def keychar(code, uni, mods):
    """修饰键态下的字符解码 (纯函数, 可测).
    sym 按住: 查官方 sym 层码表 (sym+2=@ sym+b=. sym+q=~ ...),
    未表项按基层字符. shift 按住: 字母→大写 (内核 shift 不换码表).
    fn 不产字符."""
    if mods.get(383):
        c = _SYM_CHARS.get(code)
        if c:
            return c
        if code not in (14, 28, 57) and uni.isalpha():
            return uni.upper()
    if (mods.get(383) or mods.get(42)) and uni.isalpha():
        return uni.upper()
    return uni


class EvKeyboard:
    app = None                                 # 主循环注入, 音量键用

    def __init__(self):
        self.path = find_keyboard()
        self.fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK) if self.path else None
        self.mods = {383: False, 389: False, 42: False}   # sym/fn/shift 按住态

    def poll(self):
        if self.fd is None:
            return
        while True:
            r, _, _ = select.select([self.fd], [], [], 0)
            if not r:
                return
            try:
                buf = os.read(self.fd, 24 * 16)
            except BlockingIOError:
                return
            for off in range(0, len(buf) - 23, 24):
                _, _, typ, code, val = struct.unpack_from("qqHHi", buf, off)
                if typ != EV_KEY or val not in (0, 1):
                    continue                  # 0=松开 1=按下 (2=自动重复丢弃)
                if code in (113, 114, 115):     # Fn+A 静音 / Fn+S+ / Fn+D-
                    if EvKeyboard.app:
                        EvKeyboard.app.adjust_volume(code)
                elif code in self.mods:         # sym/fn/shift: 只更新态不投事件
                    self.mods[code] = val == 1
                else:
                    kt = pygame.KEYDOWN if val == 1 else pygame.KEYUP
                    key = EVDEV_MAP.get(code, pygame.K_UNKNOWN)
                    uni = EVDEV_CHARS.get(code, "") if val == 1 else ""
                    if val == 1:
                        uni = keychar(code, uni, self.mods)
                    if uni or key != pygame.K_UNKNOWN:
                        pygame.event.post(pygame.event.Event(
                            kt, key=key, mod=0, unicode=uni))
                    elif val == 1:                # 只记按下 (松开不记, 减半噪音)
                        try:
                            with open("/tmp/dash_unmapped_keys.log",
                                      "a") as f:
                                f.write("code=%d\n" % code)
                        except Exception:
                            pass

    def close(self):
        if self.fd is not None:
            os.close(self.fd)


# ── 演示数据 (截图/展示, DASH_DEMO=1) ───────────────────────────────────────
def _demo_qt_text(rows):
    """按真实 qt 字段位(f30 时间戳/f31 涨跌/f32 涨跌%/f33 高/f34 低)造演示文本."""
    parts = []
    for code, name, num, price, prev, chg, pchg, high, low in rows:
        f = ["1", name, num, "%.2f" % price, "%.2f" % prev, "%.2f" % price] + \
            ["x"] * 24 + ["20261108150000", "%.2f" % chg, "%.2f" % pchg,
                          "%.2f" % high, "%.2f" % low]
        parts.append('v_%s="%s"' % (code, "~".join(f)))
    return "\n".join(parts)


def demo_data():
    now = int(time.time() * 1000)
    return {
        "wx": {"current_weather": {"temperature": 24.6, "weathercode": 0}},
        "hitokoto": {"ok": True, "text": "凡是过往,皆为序章。",
                     "from": "莎士比亚"},
        "idx": _demo_qt_text([
            ("sh000001", "上证指数", "000001", 3316.20, 3305.75, 10.45, 0.32, 3320.11, 3298.55),
            ("sz399001", "深证成指", "399001", 10572.50, 10500.10, 72.40, 0.69, 10580.0, 10490.2),
            ("sz399006", "创业板指", "399006", 2191.30, 2175.80, 15.50, 0.71, 2196.0, 2170.4),
            ("sh000688", "科创综指", "000688", 1045.60, 1040.20, 5.40, 0.52, 1048.0, 1038.1),
            ("sh000300", "沪深300", "000300", 3910.80, 3895.10, 15.70, 0.40, 3916.0, 3890.5),
            ("r_hkHSI", "恒生指数", "hkHSI", 20421.0, 20310.5, 110.5, 0.54, 20440.0, 20300.0),
            ("usDJI", "道琼斯", "DJI", 44300.0, 44100.0, 200.0, 0.45, 44350.0, 44080.0),
            ("usIXIC", "纳斯达克", "IXIC", 19100.0, 19050.0, 50.0, 0.26, 19120.0, 19040.0),
        ]),
        "quotes": _demo_qt_text([
            ("sh600519", "贵州茅台", "600519", 1513.00, 1521.00, -8.00, -0.53, 1522.00, 1508.00),
            ("sz000001", "平安银行", "000001", 11.62, 11.55, 0.07, 0.61, 11.70, 11.50),
        ]),
        "minute": {"code": 0, "data": {"sh600519": {"data": {"data": [
            "%03d %.2f" % (930 + i // 60, 1500.0 + 20.0 * ((i % 120) / 120.0) +
             (5.0 if i >= 120 else 0)) for i in range(0, 241)
        ]}}}},
        "day": {"code": 0, "data": {"sh600519": {"qfqday": [
            ["2026-10-%02d" % (i + 1), 1480.0 + i * 2.0, 1490.0 + i * 2.0,
             1500.0 + i * 2.0, 1475.0 + i * 2.0] for i in range(20)
        ]}}},
        "rate": {"ok": True, "base": "USD",
                 "rates": {"USD": 1.0, "CNY": 6.6908, "EUR": 0.8958,
                           "JPY": 158.32, "GBP": 0.7475, "HKD": 7.8396,
                           "KRW": 1355.38, "AUD": 1.4541},
                 "updated": 1794096151},
        # 工具子应用演示数据 (截图/展示)
        "ssq": {"ok": True, "issues": [
            {"code": "2026128", "date": "2026-11-05", "week": "四",
             "reds": [3, 9, 17, 22, 28, 32], "blue": 12,
             "pool": 2212886688, "sales": 388451122,
             "grades": [{"level": 1, "num": 4, "money": 9123456},
                        {"level": 2, "num": 88, "money": 218866}]},
            {"code": "2026127", "date": "2026-11-03", "week": "二",
             "reds": [1, 7, 11, 19, 25, 33], "blue": 5,
             "pool": 2188001122, "sales": 377112233,
             "grades": [{"level": 1, "num": 2, "money": 10223344},
                        {"level": 2, "num": 61, "money": 295511}]},
            {"code": "2026126", "date": "2026-11-01", "week": "日",
             "reds": [2, 8, 15, 21, 27, 31], "blue": 16,
             "pool": 2150011223, "sales": 361002112,
             "grades": [{"level": 1, "num": 0, "money": 0},
                        {"level": 2, "num": 45, "money": 401122}]},
            {"code": "2026125", "date": "2026-10-29", "week": "四",
             "reds": [4, 10, 13, 20, 24, 30], "blue": 8,
             "pool": 2102233441, "sales": 355901122,
             "grades": [{"level": 1, "num": 1, "money": 11002233},
                        {"level": 2, "num": 33, "money": 528833}]},
        ]},
        "dlt": {"ok": True, "issues": [
            {"code": "26132", "date": "2026-11-18", "week": "三",
             "reds": [2, 9, 16, 26, 35], "blues": [6, 11],
             "pool": 321917081, "sales": 754624606,
             "grades": [{"level": 1, "num": 3, "money": 8814866},
                        {"level": 2, "num": 128, "money": 101697}]},
            {"code": "26131", "date": "2026-11-16", "week": "一",
             "reds": [2, 11, 18, 19, 29], "blues": [4, 7],
             "pool": 323417845, "sales": 731584779,
             "grades": [{"level": 1, "num": 8, "money": 6352665},
                        {"level": 2, "num": 139, "money": 100754}]},
            {"code": "26130", "date": "2026-11-14", "week": "六",
             "reds": [1, 7, 11, 27, 29], "blues": [2, 6],
             "pool": 343462222, "sales": 743650197,
             "grades": [{"level": 1, "num": 11, "money": 5926591},
                        {"level": 2, "num": 123, "money": 105954}]},
        ]},
        "news": {"ok": True, "feeds": {"ai": {"ok": True, "items": [
            {"t": "终端侧AI:从单点应用走向智能体体验",
             "d": "端侧方案展出", "ts": "11-08 19:05"},
            {"t": "开源社区发布新版本",
             "d": "没有永远的夏天", "ts": "11-08 12:09"},
        ]}}},
    }


# ── 主循环 ──────────────────────────────────────────────────────────────────
def main():
    try:
        signal.signal(signal.SIGTERM,
                      lambda *a: pygame.event.post(pygame.event.Event(pygame.QUIT)))
    except Exception:
        pass
    windowed = "--window" in sys.argv

    on_device = not windowed and os.path.exists("/dev/fb_lcd")
    if on_device:
        # 必须在 pygame.init() 之前: 否则 SDL 抢面板 DRM master, fb 写入被丢弃
        os.environ["SDL_VIDEODRIVER"] = "dummy"
    pygame.init()

    fb = kbd = None
    if on_device:
        pygame.display.set_mode((1, 1))
        fb = Framebuffer()
        kbd = EvKeyboard()
        canvas = pygame.Surface((W, H))
    elif windowed:
        surf = pygame.display.set_mode((W * 2, H * 2))
        pygame.display.set_caption("ONEAPP · CardputerZero")
        canvas = pygame.Surface((W, H))
    else:                                       # 无 fb 无头 (测试)
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        pygame.display.set_mode((W, H))
        canvas = pygame.Surface((W, H))

    netins = netmod.Net()
    app = App(netins)
    if kbd:
        EvKeyboard.app = app
    if os.environ.get("DASH_DEMO") or "--demo" in sys.argv:
        app.data = demo_data()
    if os.environ.get("DASH_PAGE"):
        app.page = max(0, min(len(PAGES) - 1, int(os.environ["DASH_PAGE"])))
    if os.environ.get("DASH_PLAY"):
        app.autoplay_key = os.environ["DASH_PLAY"]
    if os.environ.get("DASH_TOOL") in [t[0] for t in TOOL_APPS]:
        app.tool = os.environ["DASH_TOOL"]   # 截图/调试: 直落指定子应用
        app.page = 5                         # 子应用只能从工具页进 (poll_tool 挂 page==5)
    if os.environ.get("DASH_DETAIL"):
        app.fdetail = {"code": os.environ["DASH_DETAIL"],
                       "name": os.environ["DASH_DETAIL"], "view": "min"}

    clock = pygame.time.Clock()
    last_poll = 0
    running = True
    while running:
        if kbd:
            kbd.poll()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if not app.key(ev.key, getattr(ev, "unicode", "") or ""):
                    running = False
            elif ev.type == pygame.KEYUP:
                app.keyup(ev.key)
        # 网络结果队列 → app
        try:
            while True:
                app.on_net(*netins.results.get_nowait())
        except Exception:
            pass
        now = time.monotonic()
        if now - last_poll >= 1.0:
            last_poll = now
            app.poll()
        app.draw(canvas)
        if app.esc_exit:
            running = False
        if fb:
            fb.blit(canvas)
        elif windowed:
            pygame.transform.scale(canvas, (W * 2, H * 2), surf)
            pygame.display.flip()
        clock.tick(10)

    app.mu_session_save()                      # 退出前保住播放位 (先于 mu_stop)
    app.tv_stop()                              # 杀 TV ffmpeg + 收读帧线程
    app.radio_stop()                           # 杀 ffplay + 停电平流
    app.stop_player()
    app.mu_stop()                              # 音乐 ffplay
    if app.rec_proc:
        app.rec_stop()                         # 录音收尾保存
    netins.close()
    if fb:
        fb.close()
    if kbd:
        kbd.close()
    pygame.quit()


if __name__ == "__main__":
    main()
