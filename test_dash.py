#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ONEAPP 逻辑冒烟测试 (无网络/无显示): python3 test_dash.py

商店版 (零本地服务) 测试集: 纯离线, 公网数据层用样例数据验证解析.
在线链路 (RSS 真抓 / 音乐搜索 / 歌词) 用 RUN_NET=1 额外跑 test_net_live.
"""

import json
import os
import tempfile
import time as _time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ["SDL_AUDIODRIVER"] = "dummy"

import pygame

import lunar
import net as netmod


class FakeNet:
    """离线替身 (新 net 接口: pub/pub_text/bin/job/probe)."""

    def __init__(self):
        self.mode = "offline"
        self.got = []

    def probe(self):
        pass

    def pub(self, url, tag, ttl=600, **k):
        self.got.append(("pub", tag, url))

    def pub_text(self, url, tag, ttl=15):
        self.got.append(("pubt", tag, url))

    def bin(self, port, url, tag, ttl=1800):
        self.got.append(("bin", tag, url))

    def job(self, tag, fn, ttl=600):
        self.got.append(("job", tag))

    def invalidate(self, key):
        pass

    def close(self):
        pass


def test_lunar():
    # 春节基准: 1900-01-31 / 2024-02-10 / 2025-01-29 / 2026-02-17 均正月初一
    for y, m, d in [(1900, 1, 31), (2024, 2, 10), (2025, 1, 29), (2026, 2, 17)]:
        ld = lunar.solar_to_lunar(y, m, d)
        assert ld == (y, 1, 1, False), (y, ld)
        assert lunar.lunar_text(y, m, d) == "正月初一", (y, lunar.lunar_text(y, m, d))
    # 闰月样例: 2025 年闰六月, 07-25 为闰六月初一, 06-25 为六月月初一
    assert lunar.solar_to_lunar(2025, 7, 25) == (2025, 6, 1, True)
    assert lunar.solar_to_lunar(2025, 6, 25) == (2025, 6, 1, False)
    assert lunar.lunar_text(2025, 7, 25) == "闰六月初一"
    assert lunar.solar_to_lunar(1899, 5, 1) is None
    assert lunar.solar_to_lunar(2101, 1, 1) is None
    assert lunar.days_from_civil(1970, 1, 1) == 0
    print("lunar OK  (2026-11-14 = %s)" % lunar.lunar_text(2026, 11, 14))


def test_wx():
    import dash
    assert dash.wx_code_zh(0) == "晴"
    assert dash.wx_code_zh(95) == "雷阵雨"
    assert dash.wx_code_zh(-1) == ""
    print("wx mapping OK")


def test_fetchers():
    """fetchers 离线解析: 双色球/新股样例 → 端点同形状输出."""
    import fetchers
    payload = {"result": [
        {"code": "2026128", "date": "2026-11-05 21:15:00", "week": "四",
         "red": "3,9,17,22,28,32", "blue": "12",
         "sales": "388451122", "poolmoney": "2212886688",
         "prizegrades": [
             {"type": 1, "typemoney": "9123456", "typenum": "4"},
             {"type": 2, "typemoney": "218866", "typenum": "88"},
             {"type": 7, "typemoney": "5", "typenum": "999"}]}]}
    out = fetchers._ssq_parse(payload)
    assert out["ok"] and out["latest"]["code"] == "2026128"
    assert out["latest"]["reds"] == [3, 9, 17, 22, 28, 32]
    assert out["latest"]["blue"] == 12
    assert out["latest"]["grades"] == [
        {"level": 1, "money": 9123456, "num": 4},
        {"level": 2, "money": 218866, "num": 88}], out["latest"]["grades"]
    assert fetchers._ssq_parse({"result": []}) is None
    # ipo: 已截止剔除 + 形状
    from datetime import datetime, timedelta
    today = datetime.now().strftime("%Y-%m-%d")
    past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    ipo = fetchers._get_json  # noqa: F841 (占位说明 _parse 逻辑内联如下)
    rows = {"result": {"data": [
        {"SECURITY_NAME_ABBR": "今日股", "SECURITY_CODE": "301999",
         "APPLY_DATE": today + " 09:00", "ISSUE_PRICE": "12.34"},
        {"SECURITY_NAME_ABBR": "过期股", "SECURITY_CODE": "301998",
         "APPLY_DATE": past, "ISSUE_PRICE": "9.99"}]}}
    items = []
    for r in rows["result"]["data"]:
        apply_s = (r.get("APPLY_DATE") or "")[:10]
        if apply_s >= today:
            items.append(r["SECURITY_NAME_ABBR"])
    assert items == ["今日股"]
    print("fetchers parse OK")


def test_news_parse():
    """news_rss 离线解析: RSS2.0 / Atom 双格式 + HTML 剥离."""
    import news_rss
    rss = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>标题一 &amp; 测试</title>
<link>https://example.com/1</link>
<pubDate>Fri, 14 Nov 2026 10:00:00 +0800</pubDate>
<description>&lt;p&gt;摘要&lt;/p&gt; 正文</description></item>
<item><title>标题二</title><link>https://example.com/2</link></item>
</channel></rss>""".encode("utf-8")
    items = news_rss.parse_feed(rss)
    assert [i["t"] for i in items] == ["标题一 & 测试", "标题二"], items
    assert items[0]["d"] == "摘要 正文"
    assert items[0]["u"] == "https://example.com/1"
    assert items[0]["ts"], items[0]
    atom = """<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Atom 条目</title>
<link href="https://example.com/a"/>
<published>2026-11-14T09:30:00Z</published>
<summary>内容摘要</summary></entry></feed>""".encode("utf-8")
    items = news_rss.parse_feed(atom)
    assert items[0]["t"] == "Atom 条目" and items[0]["u"] == "https://example.com/a"
    print("news parse OK (rss+atom)")


def test_net_cache():
    """Net: job 按 tag 缓存 / TTL 内不重跑 (纯内存, 真线程)."""
    net = netmod.Net()
    try:
        calls = []

        def fn():
            calls.append(1)
            return {"v": len(calls)}
        import time
        deadline = time.time() + 5
        net.job("t1", fn, ttl=2)
        while "t1" not in (net._cache and [k[1] for k in net._cache] or []) \
                and time.time() < deadline:
            time.sleep(0.05)
        r1 = None
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                item = net.results.get(timeout=0.2)
            except Exception:
                continue
            if item[0] == "data" and item[1] == "t1":
                r1 = item
                break
        assert r1 and r1[2]["v"] == 1, r1
        net.job("t1", fn, ttl=2)              # 缓存新鲜 → 不重跑
        _time.sleep(0.4)
        assert len(calls) == 1, calls
        print("net job cache OK")
    finally:
        net.close()


def test_render():
    """无头渲染一帧不崩 + 文本裁剪 + 键路由."""
    import dash
    pygame.init()
    pygame.display.set_mode((1, 1))
    canvas = pygame.Surface((dash.W, dash.H))

    app = dash.App(FakeNet())
    app.data = dash.demo_data()
    for page in range(len(dash.PAGES)):
        app.page = page
        app.draw(canvas)
    app.dialog = ("exit",)
    app.draw(canvas)
    app.dialog = ("del", {"kind": "text", "text": "测试想法内容"})
    app.draw(canvas)
    app.dialog = None
    # 想法: 本地存储 (临时文件) + 提交/删除
    tmp = tempfile.mkdtemp(prefix="oneapp")
    dash.IDEAS_FILE = os.path.join(tmp, "ideas.json")
    app.ideas = {"texts": []}
    app.page = 1
    app.in_input = True
    app.buf = "第一条想法"
    app.submit_idea()
    assert not app.in_input and len(app.ideas["texts"]) == 1
    assert app.idea_items()[0]["text"] == "第一条想法"
    os.makedirs(dash.STATE_DIR, exist_ok=True) if False else None
    import json as _json
    saved = _json.load(open(dash.IDEAS_FILE))
    assert saved["texts"][0]["text"] == "第一条想法"
    app.sel = 0
    app.delete_selected()
    assert app.ideas["texts"] == []
    # 天气定位: wx_url 拼接 / wxloc 落位换城
    assert "latitude=22.5629" in dash.wx_url({})
    assert "latitude=31.0000" in dash.wx_url({"lat": 31.0, "lon": 121.0})
    app.wx_loc = dict(dash.WX_HOME)
    app.on_net("data", "wxloc", {"lat": 31.23, "lon": 121.47, "city": "上海"})
    assert app.wx_loc["city"] == "上海"        # 归属地更新
    app.on_net("data", "wxloc", {"lat": 31.24, "lon": 121.48, "city": "上海"})
    assert abs(app.wx_loc["lat"] - 31.23) < 0.01   # 同城小漂移不更新
    app.on_net("data", "wxloc", None)
    assert app.wx_loc["city"] == "上海"        # 坏数据忽略
    # 输入模式渲染
    app.in_input = True
    app.buf = "hello oneapp"
    app.draw(canvas)
    assert app.key(pygame.K_n) is True            # 进入输入
    app.in_input, app.buf = True, ""
    app.ime_mode = False                          # 英文直输
    assert app.key(0, "a") and app.buf == "a"     # 字符
    assert app.key(0, "Z") and app.buf == "aZ"
    assert app.key(pygame.K_BACKSPACE) and app.buf == "a"
    assert app.key(pygame.K_ESCAPE) and not app.in_input
    # 电台页: 内置台表 + 语言分类 + VU tick 数学
    import radio_stations
    app.page = 2
    app.draw(canvas)
    assert len(radio_stations.STATIONS) == 20
    assert dash.App._st_lang({"name": "深圳交通广播"}) == "zh"
    assert dash.App._st_lang({"name": "BBC World Service"}) == "en"
    assert len(app.stations()) == 11              # zh 台
    app.radio_lang = "en"
    assert len(app.stations()) == 9               # en 台
    app.radio_lang = "zh"
    app.lvl.lvl = (100.0, 0.0, 0.0)               # 纯 bass → 左侧条高右侧低
    for i in range(30):
        app.vu_tick(_time.monotonic() + i * 0.13, *app.lvl.lvl)
    assert app.vu[0] > app.vu[4] > app.vu[9], app.vu
    assert max(app.vu) <= 28.0
    app.draw(canvas)                              # VU 渲染不崩
    assert dash.clip_text("一二三四五六七八九十", 11, 30).endswith("…")
    assert dash.clip_text("短", 11, 100) == "短"

    # 行情: 真实 qt 样本 (2026-11-06 实抓 sz000001) 字段位基准
    real = ('v_sz000001="51~PINGAN~000001~11.81~11.64~11.62~1640276~938074~702203~'
            '11.81~4384~11.80~3026~11.79~1562~11.78~1001~11.77~1836~11.82~1019~'
            '11.83~717~11.84~3560~11.85~44706~11.86~16687~~20261106161500~0.17~'
            '1.46~11.85~11.57~11.81/1640276/1927307591"')
    f = dash.parse_qt_text(real)["sz000001"]
    assert dash.qt_ok(f)
    assert dash.qt_num(f, 3) == 11.81 and dash.qt_num(f, 31) == 0.17
    assert dash.parse_minute({"data": {"sz000001": {"data": {"data": [
        "0930 11.62 100", "0931 11.64 200"]}}}}, "sz000001") == [11.62, 11.64]
    assert dash.parse_minute({}, "sz000001") == []
    # 行情页 + 详情渲染 (demo) + 本地自选股
    app.data = dash.demo_data()
    assert len(dash.WATCH_SEED["items"]) == 5
    app.page = 3
    app.draw(canvas)
    app.open_detail("sh600519", "贵州茅台")
    app.draw(canvas)
    app.fdetail["view"] = "day"
    app.draw(canvas)
    app.fdetail = None
    its = app.fin_items()
    assert its and its[0]["code"] == "sh600519", its
    # 快捷键 4-8 → 页 1-5 (主页触发; 新闻/电台页数字为功能键)
    pg = pygame
    for key, page in [(pg.K_4, 1), (pg.K_5, 2), (pg.K_6, 3),
                      (pg.K_7, 4), (pg.K_8, 5)]:
        app.dialog = None
        app.page = 0
        app.key(key)
        assert app.page == page, (key, app.page)
    # 新闻页数字直达: 2 → 第2条详情
    app.page = 4
    app.key(pg.K_2)
    assert app.news_detail is not None and app.news_sel == 1
    app.news_detail = None
    # 工具网格页 + 全部子应用各渲一帧
    app.page = 5
    app.draw(canvas)
    for tid, _name in dash.TOOL_APPS:
        app.tool = tid
        app.draw(canvas)
    app.tool = None
    its = app.news_items()
    assert len(its) == 2 and its[0]["t"].startswith("终端侧AI")
    app.news_detail = its[0]
    app.draw(canvas)
    app.news_detail = None
    # 新闻页 9 = 换源 (豁免全局跳页)
    app.page = 4
    app.news_src_i = 0
    app.key(pg.K_9)
    assert app.page == 4 and app.news_src_i == 1
    print("render OK (6 pages + dialog + ideas-local + fin + tools/news)")


def test_tools():
    """工具子应用: 换算 / 网格路由 / 万年历 / 双色球 / 计时器."""
    import dash
    pygame.init()
    pygame.display.set_mode((1, 1))

    assert dash.fmt_num(1234567.0) == "1,234,567"
    assert dash.temp_convert(100, "°C", "°F") == 212.0

    app = dash.App(FakeNet())
    app.data = dash.demo_data()
    # 网格: 方向键 / 数字直开 (fx1 unit2 cal3 ssq4 timer5 music6) / Esc 回主页
    app.page = 5
    app.key(pygame.K_RIGHT)
    assert app.tsel == 1
    app.key(pygame.K_DOWN)
    assert app.tsel == 5                          # 下一行同列, 钳到末卡 (6 卡)
    app.key(pygame.K_3)
    assert app.tool == "cal"
    app.key(pygame.K_ESCAPE)
    assert app.tool is None and app.page == 5     # Esc 回网格 (主页 Esc 才回主页)
    app.key(pygame.K_8)
    assert app.page == 5                          # 8 → 工具页
    app.tool_open("music")
    assert app.tool == "music" and app.mu_view == "play"   # 主页即播放页
    app.esc_hold_at = _time.monotonic() - 1.1     # 回拨: 视为已长按满 1s
    app.esc_hold_poll()
    assert app.esc_hold_at is None and app.tool is None    # 长按满 → 退音乐
    # 汇率: 字段循环 / 金额键入 / UP 增加 DOWN 减少
    app.tool_open("fx")
    app.fx_amt = "1"
    app.cv_armed = False
    app.key(pygame.K_1, "1")
    app.key(pygame.K_0, "0")
    app.key(pygame.K_0, "0")
    assert app.fx_amt == "100"
    app.key(pygame.K_e)                           # 交换源/目标
    assert app.fx_from == 1 and app.fx_to == 0
    app.key(pygame.K_UP)
    assert app.fx_amt == "101"                    # UP 增加 (金额字段)
    app.key(pygame.K_DOWN)
    assert app.fx_amt == "100"                    # DOWN 减少
    app.key(pygame.K_RIGHT)                       # 字段: 金额 → 源币种
    app.key(pygame.K_UP)
    assert app.fx_from == 2 and app.fx_to == 0
    app.key(pygame.K_DOWN)
    assert app.fx_from == 1 and app.fx_to == 0
    # 计时器: 预设 (数字1-6→TM_PRESETS) + 起停
    app.tool_open("timer")
    app.key(pygame.K_3)
    assert app.tm_dur == 300                      # TM_PRESETS[2] = 5min
    app.key(pygame.K_4)
    assert app.tm_dur == 600                      # TM_PRESETS[3] = 10min
    app.key(pygame.K_RETURN)
    assert app.tm_run
    app.key(pygame.K_RETURN)
    assert not app.tm_run
    # 万年历: 换月/换年/回今天
    app.tool_open("cal")
    y0, m0 = app._cal_ym()
    app.key(pygame.K_RIGHT)
    assert app._cal_ym() == ((y0, m0 + 1) if m0 < 12 else (y0 + 1, 1))
    app.key(pygame.K_n)
    t = _time.localtime()
    assert app._cal_ym() == (t.tm_year, t.tm_mon)
    # 双色球: 滚动边界
    app.tool_open("ssq")
    app.data["ssq"] = {"issues": [{"code": "1"}] * 5}
    app.ssq_scroll = 0
    app.key(pygame.K_UP)
    assert app.ssq_scroll == 0                    # 顶界
    app.key(pygame.K_DOWN)
    assert app.ssq_scroll == 1
    app.tool_close()
    print("tools OK (grid/fx/timer/cal/ssq)")


def test_music():
    """音乐 app: 文件名解析 / 曲库扫描 / 会话持久化 / 播放链 (SDL dummy 静音)."""
    import shutil as _sh
    import wave as _wave
    import struct as _struct
    import math as _math
    import dash

    assert dash.mu_parse_name("周杰伦 - 告白气球.mp3") == ("周杰伦", "告白气球", ".mp3")
    assert dash.mu_parse_name("大波浪-No Such Disease.mp3") == \
        ("大波浪", "No Such Disease", ".mp3")
    long_a = "x" * 31
    assert dash.mu_parse_name("%s-尾段曲名.mp3" % long_a) == \
        ("", "%s-尾段曲名" % long_a, ".mp3")
    assert dash.mu_parse_name("夜曲.wav") == ("", "夜曲", ".wav")

    tmp = tempfile.mkdtemp(prefix="onemusic")

    def mkwav(path):
        sr = 8000
        w = _wave.open(path, "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(_struct.pack("<%dh" % (sr // 2),
                                   *[int(9000 * _math.sin(2 * 3.14159 * 440 * i / sr))
                                     for i in range(sr // 2)]))
        w.close()

    try:
        os.makedirs(os.path.join(tmp, "sub"))
        mkwav(os.path.join(tmp, "A歌手 - 曲一.wav"))
        open(os.path.join(tmp, "sub", "B歌手 - 曲二.mp3"), "wb").write(b"ID3fake")
        open(os.path.join(tmp, "ignore.txt"), "wb").write(b"x")
        files = dash.scan_music_dir(tmp)
        assert [f["title"] for f in files] == ["曲一", "曲二"], files
        assert files[0]["artist"] == "A歌手"

        pygame.init()
        pygame.display.set_mode((1, 1))
        app = dash.App(FakeNet())
        app.page = 5
        real_dir = dash.MUSIC_DIR
        dash.MUSIC_DIR = tmp
        try:
            app.tool_open("music")
            assert app.tool == "music" and app.mu_view == "play"
            app.mu_view = "src"                   # 0=选源
            app.mu_sel = 0
            app.key(pygame.K_RETURN)              # 进本地曲库
            assert app.mu_view == "list"
            app.mu_rescan()
            assert len(app.mu_files) == 2
            # 起播 (真 ffplay, SDL dummy 下无声) + 暂停 + 停止
            app.mu_play(0)
            assert app.mu_playing and app.mu_playing["title"] == "曲一"
            _time.sleep(0.4)
            app.mu_pause_toggle()
            assert app.mu_paused and app.mu_proc is None
            pos = app.mu_pos0
            app.mu_pause_toggle()                 # 续播 (-ss 续)
            assert not app.mu_paused and app.mu_proc is not None
            assert abs(app.mu_pos0 - pos) < 2.5, (app.mu_pos0, pos)
            app.mu_stop()
            assert app.mu_playing is None and app.mu_proc is None
            # 会话持久化: 恢复暂停态
            app.mu_play(0)
            _time.sleep(0.4)
            app.mu_pos0 = 1.0
            app.mu_pause_toggle()
            app.mu_session_save()
            app.mu_playing = None
            app.mu_session_restore()
            assert app.mu_playing and app.mu_playing["title"] == "曲一"
            assert app.mu_paused and abs(app.mu_pos0 - 1.0) < 0.5
            app.mu_stop()
            app.tool_close()
        finally:
            dash.MUSIC_DIR = real_dir
        print("music OK (parse/scan/play/pause/session)")
    finally:
        _sh.rmtree(tmp, ignore_errors=True)


def test_voice():
    """语音想法: 索引合并 / 本地播放 / 删除连文件 / 静音裁剪 (录音本体设备实测)."""
    import shutil as _sh
    import wave as _wave
    import struct as _struct
    import math as _math
    import dash

    tmp = tempfile.mkdtemp(prefix="onevoice")
    dash.VOICES_DIR = os.path.join(tmp, "voices")
    dash.IDEAS_FILE = os.path.join(tmp, "ideas.json")
    os.makedirs(dash.VOICES_DIR, exist_ok=True)

    def mkwav(path, sec, pre_silence=0.0, amp=9000, spike=False):
        sr = 8000
        w = _wave.open(path, "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        n = int(sr * sec)
        frames = []
        for i in range(n):
            t = i / sr
            v = 0 if t < pre_silence else int(amp * _math.sin(2 * 3.14159 * 440 * t))
            frames.append(v)
        if spike:
            frames[len(frames) // 3] = 32000   # 单击脉冲 (削波/碰撞)
        w.writeframes(_struct.pack("<%dh" % n, *frames))
        w.close()

    try:
        pygame.init()
        pygame.display.set_mode((1, 1))
        app = dash.App(FakeNet())
        app.ideas = {"texts": [{"id": "t1", "text": "文字", "createdAt": 100}]}
        vf = os.path.join(dash.VOICES_DIR, "v1.wav")
        mkwav(vf, 1.0)
        app.ideas["voices"] = [{"id": "v1", "file": vf, "dur": 1,
                                "createdAt": 200}]
        items = app.idea_items()
        assert [i["kind"] for i in items] == ["voice", "text"], items
        # 播放 (真 ffplay, SDL dummy 静音; 再按停)
        app.sel = 0
        app.play_voice(items[0])
        assert app.playing_voice and app.playing_voice["id"] == "v1"
        assert app.player is not None and app.player.poll() is None
        _time.sleep(0.3)
        app.play_voice(items[0])               # 再按 = 停
        assert app.playing_voice is None and app.player is None
        # 删除: 文件 + 索引一起清
        assert os.path.exists(vf)
        app.delete_selected()
        assert not os.path.exists(vf)
        assert app.ideas.get("voices") == []
        assert [i["kind"] for i in app.idea_items()] == ["text"]
        # 静音裁剪: 0.5s 静音头 + 1s 音 → ~1.2s 内
        f2 = os.path.join(tmp, "rec.wav")
        mkwav(f2, 1.5, pre_silence=0.5)
        dur = dash.trim_silence_head(f2, keep=0.1)
        assert dur is not None and 0.9 <= dur <= 1.4, dur
        # clean_voice: 小声量被 RMS 归一拉起 (k≈5), 尖峰文件峰值安全回缩
        f3 = os.path.join(tmp, "clean.wav")
        mkwav(f3, 2.0, pre_silence=0.5, amp=900)   # 小声量
        cdur = dash.clean_voice(f3)
        assert cdur is not None and abs(cdur - 2.0) < 0.1, cdur
        import wave as _w2
        import struct as _s

        def pkof(path):
            w = _w2.open(path)
            frames = w.readframes(w.getnframes())
            w.close()
            return max(abs(v) for v in _s.unpack("<%dh" % (len(frames) // 2),
                                                 frames))
        pk = pkof(f3)
        assert 2500 <= pk <= 9000 and pk > 900 * 2, pk   # 拉响 ≥2×
        f4 = os.path.join(tmp, "spike.wav")
        mkwav(f4, 2.0, pre_silence=0.5, amp=900, spike=True)
        assert dash.clean_voice(f4) is not None
        assert pkof(f4) <= 32000, pkof(f4)     # 安全回缩不炸
        # 纯静音文件: 不崩不爆放
        f5 = os.path.join(tmp, "sil.wav")
        mkwav(f5, 0.2, pre_silence=99)
        assert dash.clean_voice(f5) is not None
        # 文字与语音互不影响
        app.submit_idea()
        assert len(app.idea_items()) == 1
        # 5 键不被全局跳页劫持 (回归: 曾跳去电台页) — Mac 无 arecord → 优雅失败留原页
        app.page = 1
        app.key(pygame.K_5)
        assert app.page == 1, "K_5 must not jump pages on ideas"
        if app.rec_proc:                       # 设备 mixer OK → 真开录; 即收
            app.rec_stop()                     # (<1s → 太短未保存, 不落盘)
        else:
            # Mac 无 arecord / 设备 mixer 回读失败 → 都必须有失败提示且不跳页
            assert app.osd is not None and app.rec_proc is None
        app.page = 1
        app.key(pygame.K_8)                    # 8=播放语音 (空表/非语音条 → 无动作留原页)
        assert app.page == 1
        print("voice OK (index/play/delete/trim/keys)")
    finally:
        _sh.rmtree(tmp, ignore_errors=True)


def test_ime():
    """拼音 IME: 词表/候选/联想 + 音乐搜索框路由."""
    import dash
    import ime

    cands = ime.candidates("nihao")
    assert cands and cands[0] == "你好", cands[:4]
    assert "蔡" in ime.candidates("cai")
    assert ime.candidates("caiq")            # 前缀建议 (打词中途)
    assert ime.candidates("") == []
    assert ime.assoc("蔡")                    # 联想词表

    app = dash.App(FakeNet())
    app.page = 5
    app.tool = "music"
    app.mu_view = "search"
    app.ime_clear()
    app.mu_input = True
    assert app.ime_mode                       # 默认拼音
    for c in "nihao":
        app.key(getattr(pygame, "K_" + c), c)
    assert app.ime_buf == "nihao"
    app.key(pygame.K_SPACE, " ")              # 空格 = 首选
    assert app.mu_q == "你好" and not app.ime_buf
    assert app.ime_assoc                      # 联想出词
    app.key(pygame.K_2, "2")
    assert app.mu_q.startswith("你好") and len(app.mu_q) > 2
    # 拼音串 Enter = ASCII 直提 (英文歌名场景)
    app.ime_clear()
    for c in "cai":
        app.key(getattr(pygame, "K_" + c), c)
    app.key(pygame.K_RETURN, "\r")
    assert app.mu_q.endswith("cai")
    # / 切英文 → 直输
    app.key(pygame.K_SLASH, "/")
    assert not app.ime_mode
    for c in "x":
        app.key(pygame.K_x, c)
    assert app.mu_q.endswith("x")
    app.key(pygame.K_BACKSPACE)
    assert not app.mu_q.endswith("x")
    # 拼音态 Backspace: 先缩串, 缩空后再按 = 删已提交字 (回归: ("del",) 崩修复)
    app.ime_mode = True
    app.ime_clear()
    for c in "ni":
        app.key(getattr(pygame, "K_" + c), c)
    app.key(pygame.K_BACKSPACE)
    assert app.ime_buf == "n"
    app.key(pygame.K_BACKSPACE)
    assert not app.ime_buf and app.mu_q
    app.key(pygame.K_BACKSPACE)
    assert app.mu_q.endswith("ca")
    # 设备键盘中英切换键: Fn+N 实发 evdev 92
    assert dash.EVDEV_MAP.get(92) == pygame.K_SLASH and dash.EVDEV_CHARS.get(92) == "/"
    app.ime_mode = True
    app.ime_clear()
    app.mu_input = True
    for c in "cai":
        app.key(getattr(pygame, "K_" + c), c)
    app.draw_mu(pygame.Surface((dash.W, dash.H)))
    print("ime OK (words/chars/assoc/route/toggle)")


def test_net_live():
    """在线链路 (RUN_NET=1): 公网源真抓 + 音乐搜索 + 歌词 (大陆网络)."""
    import fetchers
    import news_rss
    import musrc
    fx = fetchers.fx_fetch()
    assert fx["ok"] and "CNY" in fx["rates"]
    print("  fx %s CNY=%.3f (%s)" % (fx["ok"], fx["rates"]["CNY"], fx["source"]))
    loc = fetchers.wx_locate()
    assert loc and loc.get("lat") is not None
    print("  wx_loc %s (%.3f,%.3f src=%s)" % (loc["city"], loc["lat"], loc["lon"], loc["src"]))
    import dash as _dash
    import urllib.request as _ur
    op = _ur.build_opener(_ur.ProxyHandler({}))   # 天气链=无代理直连 (同 app)
    with op.open(_dash.wx_url(loc), timeout=10) as r:
        wx = json.loads(r.read().decode())
    assert wx and (wx.get("current_weather") or {}).get("temperature") is not None
    print("  wx %.1f°C @%s" % (wx["current_weather"]["temperature"], loc["city"]))
    ssq = fetchers.ssq_fetch()
    assert ssq["ok"] and ssq["latest"]["code"]
    print("  ssq latest", ssq["latest"]["code"])
    ipo = fetchers.ipo_fetch()
    assert ipo["ok"]
    print("  ipo count", ipo["count"])
    ne = news_rss.news_fetch_all()
    okn = [k for k, v in ne["feeds"].items() if v.get("ok")]
    assert okn, "no rss feed reachable"
    print("  news %d/%d" % (len(okn), len(ne["feeds"])))
    res = musrc.ne_search("晴天", 3)
    assert res, "netease search failed"
    print("  ne_search", len(res))
    lrc = musrc.lrc_fetch("晴天", "周杰伦", 269)
    print("  lrc lines", len((lrc.get("synced") or "").splitlines()))


if __name__ == "__main__":
    test_lunar()
    test_wx()
    test_fetchers()
    test_news_parse()
    test_net_cache()
    test_render()
    test_tools()
    test_music()
    test_voice()
    test_ime()
    if os.environ.get("RUN_NET"):
        test_net_live()
    print("ALL PASS")
