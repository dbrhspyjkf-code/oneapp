#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ONEAPP 逻辑冒烟测试 (无网络/无显示): python3 test_dash.py

商店版 (零本地服务) 测试集: 纯离线, 公网数据层用样例数据验证解析.
在线链路 (RSS 真抓 / 音乐搜索 / 歌词) 用 RUN_NET=1 额外跑 test_net_live.
"""

import json
import os
import tempfile
import subprocess
import sys
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
        net.invalidate("t1")                  # 强制作废 → 必须真重跑
        # (旧版 invalidate 只弹字符串键, job 缓存键是 ("job", tag) 元组
        #  → 从未生效; 邮件强刷被 TTL 缓存静默吞, 2026-11-15 真机定位)
        net.job("t1", fn, ttl=2)
        r2 = None
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                item = net.results.get(timeout=0.2)
            except Exception:
                continue
            if item[0] == "data" and item[1] == "t1":
                r2 = item
                break
        assert r2 and r2[2]["v"] == 2, (r2, calls)
        print("net job cache OK (ttl/invalidate)")
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
    # 电台页: 内置台表 (四组) + 分组列表 + VU tick 数学
    import radio_stations
    app.page = 2
    app.draw(canvas)
    assert len(radio_stations.STATIONS) == 204
    assert app.radio_tabs() == ["★"] + radio_stations.GROUPS
    app.radio_grp = 1                             # 国家
    assert all(s["grp"] == "国家" for s in app.radio_list())
    app.radio_grp = 4                             # 国际
    assert all(s["grp"] == "国际" for s in app.radio_list())
    app.radio_grp = 0                             # ★ (空)
    app.radio_fav = []                    # 用户真机收藏不进测试 (渲染冒烟)
    assert app.radio_list() == []
    app.wx_loc = {"city": "深圳", "prov": "广东省"}  # 本地置顶: 同城>同省
    app.radio_grp = 3                             # 地方
    lst = app.radio_list()
    assert lst[0]["city"] == "深圳" and lst[1]["city"] == "深圳"
    app.radio_grp = 2                             # 省级
    lst = app.radio_list()
    assert all(s.get("prov") == "广东" for s in lst[:5])   # 广东 5 台置顶区
    assert radio_stations.local_rank(lst[0], "广东", "深圳") == 1
    app.wx_loc = None
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
    # 设备上可能有真实 watch.json (用户手动加过股) — 测试强制种子态
    app.watch = {"items": [dict(x) for x in dash.WATCH_SEED["items"]]}
    its = app.fin_items()
    assert its and its[0]["code"] == "sh600519", its
    # 自选股: qt_code 幂等 (行情没显示的根因) + n 添加 / d 删除 (本地 json)
    assert dash.qt_code("sh600519") == "sh600519"
    assert dash.qt_code("600519") == "sh600519"
    assert dash.qt_code("000001") == "sz000001"
    assert dash.norm_stock_code("300750") == "sz300750"
    assert dash.norm_stock_code("sh600519") == "sh600519"
    assert dash.norm_stock_code("abc") is None
    dash.WATCH_FILE = os.path.join(tmp, "watch.json")
    app.watch = {"items": [{"code": "sh600519", "name": ""}]}
    app.key(pygame.K_n)
    assert app.fin_input
    app.ime_mode = False
    for chr_ in "002594":
        app.key(0, chr_)
    app.key(pygame.K_RETURN)
    assert not app.fin_input
    assert [i["code"] for i in app.watch["items"]] == ["sh600519", "sz002594"]
    app.fsel = 1
    app.key(pygame.K_d)
    assert app.dialog and app.dialog[0] == "delwatch"
    assert app.dialog[1] == "sz002594", app.dialog       # 带 code 非下标
    app.draw(canvas)                                     # dialog 渲染 (曾崩点)
    app.key(pygame.K_RETURN)
    assert [i["code"] for i in app.watch["items"]] == ["sh600519"]
    app.draw(canvas)                              # 顶部跑马灯渲染
    # 东财妙想自选股同步: 响应解析 (纯函数) + s 设置流 + job 投递
    import fetchers
    sample = {"code": 0, "message": "ok", "data": {"allResults": [
        {"result": {"dataList": [
            {"SECURITY_CODE": "600519", "SECURITY_SHORT_NAME": "贵州茅台",
             "MARKET_SHORT_NAME": "SH"},
            {"SECURITY_CODE": "000001", "SECURITY_SHORT_NAME": "平安银行",
             "MARKET_SHORT_NAME": "SZ"},
            {"SECURITY_CODE": "hk00700", "SECURITY_SHORT_NAME": "腾讯",
             "MARKET_SHORT_NAME": "HK"}]}}]}}
    pr = fetchers.em_watchlist_parse(sample)
    assert pr["ok"] and [i["code"] for i in pr["items"]] == \
        ["sh600519", "sz000001"], pr
    assert pr["items"][0]["name"] == "贵州茅台"
    assert not fetchers.em_watchlist_parse({"code": -1})["ok"]
    dash.EM_KEY_FILE = os.path.join(tmp, "em_key.json")
    app.key(pygame.K_s)
    assert app.fin_sync and app.ime_mode is False
    for chr_ in "testkey123":
        app.key(0, chr_)
    app.key(pygame.K_RETURN)
    assert not app.fin_sync
    import json as _jj
    assert _jj.load(open(dash.EM_KEY_FILE))["key"] == "testkey123"
    assert ("job", "emwatch") in app.net.got, app.net.got[-3:]
    app.on_net("data", "emwatch", pr)
    assert [i["code"] for i in app.watch["items"]] == \
        ["sh600519", "sz000001"]                     # 替换 + 港股剔除
    app.draw(canvas)                                 # 同步框渲染
    # 每日一句: 内置 CSV 本地轮换 (与 hermes 同算法, 车机/ONEAPP 同句)
    ds = dash.daily_sentence()
    assert ds["ok"] and ds["text"] and ds["from"], ds
    assert dash.daily_sentence() == ds              # 同日稳定
    app2 = dash.App(FakeNet())
    assert app2.data.get("hitokoto", {}).get("text") == ds["text"]
    app2.on_net("mode", "online", None)
    assert app2.data.get("hitokoto", {}).get("text") == ds["text"]
    # 主页数据磁盘缓存: 收到即落盘, 新 App 实例 (模拟重启) 秒显
    # (CACHE_DIR 指 tmp, 防污染真实缓存)
    import json as _j
    dash.CACHE_DIR = os.path.join(tmp, "cache")
    cachef = os.path.join(dash.CACHE_DIR, "wx.json")
    app.on_net("data", "wx", {"current_weather": {"temperature": 24.6}})
    assert _j.load(open(cachef))["current_weather"]["temperature"] == 24.6
    app2 = dash.App(FakeNet())
    assert app2.data.get("wx", {}).get("current_weather", {})\
        .get("temperature") == 24.6
    app2.on_net("mode", "online", None)            # 模式切换也不丢
    assert app2.data.get("wx", {}).get("current_weather", {})\
        .get("temperature") == 24.6
    # 主页自选股卡: quotes 缓存 + 渲染
    app.on_net("text", "quotes",
               'v_sh600519="51~贵州茅台~600519~1513.00~1521.00~-8.00~'
               '100~200~300~~20261114~~-8.00~-0.53~1514~1508"')
    assert os.path.exists(os.path.join(dash.CACHE_DIR, "quotes.json"))
    app3 = dash.App(FakeNet())
    app3.watch = {"items": [{"code": "sh600519", "name": ""}]}
    assert app3.fin_items()[0].get("price") == 1513.0   # 缓存秒显合并行情
    app3.page = 0
    app3.draw(canvas)
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
    # 新闻页: ←→ 切换源, ↑↓ 选择 (9 不再换源; 4-8 跳页不劫持 1-7 选条)
    app.page = 4
    app.news_src_i = 0
    app.key(pg.K_9)
    assert app.page == 4 and app.news_src_i == 0      # 9 已无功能
    app.key(pg.K_RIGHT)
    assert app.news_src_i == 1 and app.news_sel == 0 and app.news_detail is None
    app.key(pg.K_LEFT)
    assert app.news_src_i == 0                        # 循环回绕
    app.key(pg.K_LEFT)
    assert app.news_src_i == len(dash.NEWS_SOURCES) - 1  # 向左回绕到末源
    app.key(pg.K_RIGHT)
    assert app.news_src_i == 0
    app.news_detail = None
    sel0 = app.news_sel
    app.key(pg.K_DOWN)
    assert app.news_sel == sel0 + 1                   # ↓ 选择下一条
    app.key(pg.K_UP)
    assert app.news_sel == sel0                       # ↑ 选择上一条
    # P = 换页 (4→5 工具; 方向键已被选条/切源占用)
    app.key(pg.K_p)
    assert app.page == 5, app.page
    app.page = 4
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
    assert app.tsel == 6                          # 下一行同列, 钳到末卡 (7 卡)
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
    real_last = dash.MU_LAST_CACHE        # 隔离: 会话落盘不得碰设备真 mu_last.json
    dash.MU_LAST_CACHE = os.path.join(tmp, "state", "mu_last.json")

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
            # 回归 (2026-11-14 mu_ff 闪退): 播放态必须能完整画播放页
            # (出声锚点判据 self.mu_ff — 商店版删音乐 AirPlay 后必须保留初始化)
            app.mu_view = "play"
            app.mu_aud_up = None
            app.draw(pygame.Surface((dash.W, dash.H)))
            # 回归 (2026-11-14 歌词不显示): _pl_lrc_parse 丢 @staticmethod →
            # on_net 投递 TypeError 被主循环 except 吞 → mu_lyr 永远空
            app.on_net("data", "mulyr:t", {
                "synced": "[00:05.5]你好\n[00:10]世界", "plain": None})
            assert app.mu_lyr == [(5.5, "你好"), (10.0, "世界")], app.mu_lyr
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
        dash.MU_LAST_CACHE = real_last
        _sh.rmtree(tmp, ignore_errors=True)


def test_mu_airplay():
    """音乐 AirPlay: 输出恢复/7 键循环+持久化/起播产端命令与消费端 argv/
    mu_tick 双判据 (消费端退=曲终)/音量路由."""
    import shutil as _sh
    import dash

    pygame.init()
    pygame.display.set_mode((1, 1))
    tmp = tempfile.mkdtemp(prefix="onemuap")
    real_last, real_out = dash.MU_LAST_CACHE, dash.MU_OUT_CACHE
    real_cache = dash.RAOP_CACHE
    dash.MU_LAST_CACHE = os.path.join(tmp, "mu_last.json")
    dash.MU_OUT_CACHE = os.path.join(tmp, "mu_out.json")
    dash.RAOP_CACHE = os.path.join(tmp, "raop.json")
    devs = [{"ip": "1.1.1.4", "port": 7000, "name": "A音箱", "id": "ida"},
            {"ip": "1.1.1.8", "port": 7000, "name": "B音箱", "id": "idb"}]
    netmod.save_json_file(dash.RAOP_CACHE, devs)

    class _Stdin:                          # 产端接 cons.stdin 的存根
        def write(self, b):
            pass

        def close(self):
            pass

    class _P:                              # 假进程: 录命令, poll 恒活
        def __init__(self, argv, tag):
            self.argv, self.tag = argv, tag
            self.stdin = _Stdin()
            self._rc = None

        def poll(self):
            return self._rc

        def terminate(self):
            self._rc = 0

        def kill(self):
            self._rc = -9

        def wait(self, timeout=None):
            return self._rc

    spawns = []

    def fake_popen(cmd, **kw):
        p = _P(cmd if isinstance(cmd, list) else [str(cmd)],
               "cons" if cmd[1:2] == ["-c"] else "prod")
        spawns.append(p)
        return p

    try:
        # 恢复: 按设备 id → mu_out=2; 未知 id → 0
        netmod.save_json_file(dash.MU_OUT_CACHE, {"id": "idb"})
        app = dash.App(FakeNet())
        assert app.mu_out == 2, app.mu_out
        assert app.mu_out_name() == "AirPlay·B音箱"
        netmod.save_json_file(dash.MU_OUT_CACHE, {"id": "gone"})
        app2 = dash.App(FakeNet())
        assert app2.mu_out == 0 and app2.mu_out_name() == "本机"

        # 7 键循环 (播放页) + 持久化: 0→1→2→0
        app.page = 5
        app.tool_open("music")
        app.mu_view = "play"
        app.raop = devs
        app.mu_out = 0
        played = []

        def fake_raop(self, idx, f, target, pos, dur, dev):
            played.append((idx, round(pos, 1), dev["id"]))
            self.mu_proc = _P(["ffmpeg"], "prod")
            self.mu_ff = _P([sys.executable, "-c"], "cons")
            self.mu_aud_up = None
            return True

        real_raop = dash.App._mu_play_raop
        dash.App._mu_play_raop = fake_raop
        try:
            app.key(pygame.K_7)
            assert app.mu_out == 1 and played == []
            app.key(pygame.K_7)
            assert app.mu_out == 2 and played == []
            app.key(pygame.K_7)
            assert app.mu_out == 0
            assert netmod.load_json_file(dash.MU_OUT_CACHE, {}) == {"id": ""}
            # 播放中切输出 = 同曲同位续播
            app.mu_out = 0
            app.mu_playing = {"path": "/x.wav", "title": "T", "dur": 90}
            app.mu_play_idx = 0
            app.mu_files = [app.mu_playing]
            app.mu_paused = False
            app.mu_pos0, app.mu_t0 = 30.0, _time.monotonic() - 5
            app.mu_aud_up = _time.monotonic() - 5
            app.mu_proc = _P(["ffplay"], "prod")
            app.key(pygame.K_7)
            assert app.mu_out == 1 and played and played[-1][2] == "ida"
            assert abs(played[-1][1] - 35.0) < 1.5, played  # 墙钟同位续播
        finally:
            dash.App._mu_play_raop = real_raop

        # 真起播链: 产端命令 (s16le/-re/-ss/http 重连) + 消费端 argv (歌名/歌手)
        app.mu_out = 1
        app.raop = devs
        app.mu_playing = None
        app.mu_files = [{"path": "http://x/y.mp3", "title": "晴天",
                         "artist": "周杰伦", "dur": 60}]
        real_pop = dash.subprocess.Popen
        dash.subprocess.Popen = fake_popen
        try:
            app.mu_play(0, 12.0)
            cons = [p for p in spawns if p.tag == "cons"]
            prod = [p for p in spawns if p.tag == "prod"]
            assert cons and prod, "双进程未起"
            cargv = cons[-1].argv
            assert cargv[1] == "-c" and "PcmSource" in cargv[2], cargv[:3]
            assert cargv[3:6] == ["1.1.1.4", "7000", "ida"], cargv
            assert cargv[6] == "晴天" and cargv[7] == "周杰伦", cargv
            assert cargv[8].startswith("/tmp/oneapp_muvol_"), cargv
            p = prod[-1].argv
            assert p[:5] == ["ffmpeg", "-y", "-loglevel", "error", "-vn"]
            assert "-re" in p and "-ss" in p and "12.0" in p
            i = p.index("-f")
            assert p[i:i + 6] == ["-f", "s16le", "-ar", "44100", "-ac", "2"]
            assert p[i + 6] == "-"
            assert app.mu_ff is not None and app.mu_proc is not None
            assert app.mu_out == 1                      # 成功不回落
            # mu_tick: 消费端退 (rc0) 且墙钟近尾声 → 曲终停播
            app.mu_aud_up = _time.monotonic() - 59
            app.mu_dur = 60
            app.mu_ff._rc = 0
            app.mu_proc._rc = 224                       # 产端 EPIPE 正常竞态
            app.mu_advance = True
            app.mu_view = "play"
            for _ in range(4):
                app.mu_tick()
                _time.sleep(0.05)
            assert app.mu_playing is None and app.mu_advance is False
            assert app.mu_ff is None and app.mu_proc is None
        finally:
            dash.subprocess.Popen = real_pop

        # 音量路由: 音乐 AirPlay 会话 → _raop_volume (OSD 带音乐输出名)
        routed = []
        real_vol = dash.App._raop_volume
        dash.App._raop_volume = lambda self, code, out_name=None: \
            routed.append((code, out_name))
        try:
            app.raop = devs
            app.mu_out = 1
            app.mu_ff = _P([sys.executable, "-c"], "cons")
            app.tool = "music"
            app.adjust_volume(115)                      # Fn+S
            assert routed == [(115, "AirPlay·A音箱")], routed
            app.mu_ff._rc = 0                           # 会话已死 → 不再劫持
            app.adjust_volume(115)
            assert len(routed) == 1
        finally:
            dash.App._raop_volume = real_vol
        print("mu airplay OK (restore/cycle/cmd/argv/tick/vol)")
    finally:
        dash.MU_LAST_CACHE, dash.MU_OUT_CACHE = real_last, real_out
        dash.RAOP_CACHE = real_cache
        _sh.rmtree(tmp, ignore_errors=True)


def test_radio_out():
    """电台输出: 设备过滤/缓存恢复 + O 键循环续播 + 失败回落 + 无序号键位."""
    import shutil as _sh
    import dash
    pygame.init()
    pygame.display.set_mode((1, 1))
    canvas = pygame.Surface((dash.W, dash.H))
    tmp = tempfile.mkdtemp(prefix="oneapp")
    dash.RAOP_CACHE = os.path.join(tmp, "raop.json")
    dash.RADIO_OUT_FILE = os.path.join(tmp, "radio_out.json")
    try:
        # 过滤: 只留音箱 (电视/电脑/手机剔除, 缺 ip/id 剔除)
        assert dash._raop_ok({"ip": "1.2.3.4", "id": "x", "name": "起居室HOMEPOD"})
        assert not dash._raop_ok({"ip": "1.2.3.4", "id": "x", "name": "客厅电视"})
        assert not dash._raop_ok({"ip": "1.2.3.4", "id": "x", "name": "MacBook"})
        assert not dash._raop_ok({"ip": "1.2.3.4", "id": "x", "name": "iPhone"})
        assert not dash._raop_ok({"ip": "1.2.3.4", "name": "音箱"})   # 无 id
        assert not dash._raop_ok({"id": "x", "name": "音箱"})         # 无 ip

        # 缓存恢复: radio_out.json 按设备 id 对位 → radio_out=2; 未知 id → 0
        devs = [{"ip": "1.2.3.4", "port": 7000, "name": "A音箱", "id": "ida"},
                {"ip": "5.6.7.8", "port": 7000, "name": "B音箱", "id": "idb"}]
        netmod.save_json_file(dash.RAOP_CACHE, devs)
        netmod.save_json_file(dash.RADIO_OUT_FILE, "idb")
        app = dash.App(FakeNet())
        assert app.radio_out == 2, app.radio_out
        assert app.radio_out_name() == "AirPlay·B音箱"
        netmod.save_json_file(dash.RADIO_OUT_FILE, "gone")
        assert dash.App(FakeNet()).radio_out == 0

        # O 键: 本机→A→B→本机 循环 + 持久化; 播放中换输出 = 停旧+续播同台
        netmod.save_json_file(dash.RADIO_OUT_FILE, "")
        app3 = dash.App(FakeNet())
        app3.page = 2
        app3.raop = devs
        played = []

        def fake_raop(self, st, dev):
            played.append((st["key"], dev["id"]))
            self.playing_key = st["key"]
            return True

        real_raop = dash.App._radio_play_raop
        dash.App._radio_play_raop = fake_raop
        try:
            st = app3.radio_list(1)[0]
            app3.playing_key = st["key"]
            app3.key(pygame.K_o)              # 本机→A (停旧 + 续播同台)
            assert app3.radio_out == 1 and played[-1] == (st["key"], "ida")
            assert app3.playing_key == st["key"]
            assert netmod.load_json_file(dash.RADIO_OUT_FILE, None) == "ida"
            app3.key(pygame.K_o)              # A→B
            assert app3.radio_out == 2 and played[-1] == (st["key"], "idb")
            app3.key(pygame.K_o)              # B→本机
            assert app3.radio_out == 0
            assert netmod.load_json_file(dash.RADIO_OUT_FILE, None) == ""
            app3.playing_key = None
            app3.draw(canvas)                 # 输出角标渲染不崩
        finally:
            dash.App._radio_play_raop = real_raop

        # Enter 切台: 播放中选异台 → 一次 Enter 直接切 (旧码先停后播要按两次);
        # 同台 → 播/停
        app3b = dash.App(FakeNet())
        app3b.page = 2
        app3b.raop = devs
        app3b.radio_out = 1
        app3b.radio_grp = 1                      # 国家组 (Enter 走当前组台表)
        played2 = []

        def fake_raop2(self, st, dev):
            played2.append(st["key"])
            self.playing_key = st["key"]
            return True

        dash.App._radio_play_raop = fake_raop2
        try:
            sts = app3b.radio_list(1)
            app3b.rsel = 0
            app3b.key(pygame.K_RETURN)          # 停播起播 A
            assert app3b.playing_key == sts[0]["key"] and len(played2) == 1
            app3b.rsel = 2
            app3b.key(pygame.K_RETURN)          # 播放中切 C: 一次 Enter
            assert app3b.playing_key == sts[2]["key"], "切台应一次 Enter 直达"
            assert len(played2) == 2
            app3b.key(pygame.K_RETURN)          # 同台: Enter = 停
            assert app3b.playing_key is None and len(played2) == 2
        finally:
            dash.App._radio_play_raop = real_raop

        # 空设备: O 键提示并保持本机 (顺带触发后台补扫)
        app4 = dash.App(FakeNet())
        app4.page = 2
        app4.raop = []
        app4.key(pygame.K_o)
        assert app4.radio_out == 0 and app4.osd[0] == "无 AirPlay 设备"

        # RAOP 起播失败 → 回落本机 (radio_out 归零, 本机 Popen 也败 → 播放失败)
        class Boom:
            def __init__(self, *a, **k):
                raise OSError("no pyatv")

        real_pop = dash.subprocess.Popen
        dash.subprocess.Popen = Boom
        try:
            app5 = dash.App(FakeNet())
            app5.page = 2
            app5.raop = [devs[0]]
            app5.radio_out = 1
            app5.radio_play({"key": "k", "url": "http://x/stream", "name": "台"})
            assert app5.radio_out == 0 and app5.playing_key is None
        finally:
            dash.subprocess.Popen = real_pop

        # 数字键不再换台 (列表无序号, 1-8 直播已废): 按 3/5 不起播不选中
        app6 = dash.App(FakeNet())
        app6.page = 2
        mark = (app6.rsel, app6.playing_key)
        app6.key(pygame.K_3)
        app6.key(pygame.K_5)
        assert (app6.rsel, app6.playing_key) == mark
        assert app6.page == 2                 # 5 不再被电台占用 → 跳页 (5=电台)

        # AirPlay 会话内音量: 路由(不碰 pactl) + SETVOL 走 FIFO + 记忆落盘
        # + Fn+A 静音/恢复 + RAOPVOL 回执采纳 + radio_stop 关命令通道
        dash.RAOP_VOL_FILE = os.path.join(tmp, "raop_vol.json")
        app8 = dash.App(FakeNet())
        app8.page = 2
        app8.raop = devs
        app8.radio_out = 1

        class FakeCons:
            def poll(self):
                return None

        app8.raop_cons = FakeCons()
        app8.playing_key = "k"
        vol_fifo = os.path.join(tmp, "vol.fifo")
        os.mkfifo(vol_fifo)
        app8.vol_fifo = vol_fifo
        app8.vol_w = os.open(vol_fifo, os.O_RDWR | os.O_NONBLOCK)
        assert app8.raop_vol == 50             # 无记忆默认 50
        # 会话内绝不能落到本地 pactl 路径
        real_run = dash.subprocess.run
        dash.subprocess.run = \
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("pactl hit"))
        try:
            app8.adjust_volume(115)            # Fn+S 音量+
        finally:
            dash.subprocess.run = real_run
        assert app8.raop_vol == 55
        assert os.read(app8.vol_w, 64) == b"SETVOL 55\n"
        assert netmod.load_json_file(dash.RAOP_VOL_FILE, None) == 55
        assert "55%" in app8.osd[0] and "AirPlay·A音箱" in app8.osd[0]
        app8.adjust_volume(114)                # Fn+D 音量-
        assert app8.raop_vol == 50 and os.read(app8.vol_w, 64) == b"SETVOL 50\n"
        app8.adjust_volume(113)                # Fn+A 静音
        assert app8.raop_vol == 0 and os.read(app8.vol_w, 64) == b"SETVOL 0\n"
        app8.adjust_volume(113)                # 再按恢复
        assert app8.raop_vol == 50 and os.read(app8.vol_w, 64) == b"SETVOL 50\n"
        # RAOPVOL 回执采纳 (音箱/iPhone 侧音量变化 → pyatv 视角回流)
        app8.raop_vol = 0
        wsrc = "import sys, time; print('RAOPVOL 37', flush=True); time.sleep(0.4)"
        wproc = subprocess.Popen([sys.executable, "-c", wsrc],
                                 stdout=subprocess.PIPE)
        dash.App._raop_watch(app8, wproc)      # 读到行后随 EOF 返回
        assert app8.raop_vol == 37, app8.raop_vol
        app8.radio_stop()                      # 命令通道随会话关
        assert app8.vol_w is None and not os.path.exists(vol_fifo)
        assert app8.raop_cons is None

        # 会话不在 (未播放/已停) → 不走 AirPlay, 落回本地且标签注明 本机
        app9 = dash.App(FakeNet())
        app9.page = 2
        app9.radio_out = 1
        app9.raop = devs
        app9.playing_key = None
        assert app9.adjust_volume(115) is None  # Mac 无 pactl → OSD 兜底不崩

        # 内嵌 -c 脚本语法完整性 (三引号截断教训) + PcmSource 全读/字节序语义
        # (miniaudio UNKNOWN 格式探测对无限管道永不返回 → open_source 挂死,
        # stream_file 从未发声 — PCM 直通绕开整个解码层; readframes 尾须
        # s16le→大端 L16 交换, 漏交换 = 全频道沙沙声)
        compile(dash._ATVR_SCAN, "atvr_scan", "exec")
        compile(dash._ATVR_PLAY, "atvr_play", "exec")
        try:
            import pyatv  # noqa: F401
        except ImportError:
            pyatv = None
        ns = {}
        if pyatv is not None:
            exec(dash._ATVR_PCM_LIB, ns)
        if pyatv is not None:
            _io = __import__("io")
            data = bytes(range(256)) * 600              # 153600B
            _src = _io.BytesIO(data)
            assert ns["_prefill"](_src, 65536) is False  # 数据够: 存活门通过
            _src2 = _io.BytesIO(b"x" * 50)
            assert ns["_prefill"](_src2, 65536) is True  # 短产端: EOF → 不烧连接
            _arr = __import__("array")

            def _ref_l16(bs):                            # 参考交换: 同 pyatv 官源
                a = _arr.array("h", bytes(bs[:len(bs) // 2 * 2]))
                if __import__("sys").byteorder == "little":
                    a.byteswap()
                return a.tobytes()

            assert ns["_to_l16"](data[:1000]) == _ref_l16(data[:1000])
            assert ns["_to_l16"](b"\x01") == b""         # 奇字节截齐不抛

            async def _pcm_test():
                src = ns["PcmSource"](_io.BytesIO(data), 44100, 2, 2, "台")
                assert (src.sample_rate, src.channels, src.sample_size) == \
                    (44100, 2, 2)
                got = await src.readframes(250)          # 250 帧 ×4B = 1000B 恒满
                assert got == _ref_l16(data[:1000])      # 大端 L16 输出
                got = await src.readframes(40000)        # 160000B > 剩余 152600B
                assert got == _ref_l16(data[1000:])      # 真 EOF 才短读
                empty = ns["PcmSource"](_io.BytesIO(b""), 44100, 2, 2, "台")
                assert await empty.readframes(10) == b""
                md = await src.get_metadata()
                assert md.title == "台"

            asyncio = __import__("asyncio")
            asyncio.run(_pcm_test())


        # 设备 ffmpeg SIGTERM 会卡死 futex (真机实证) → radio_stop 必须
        # 补刀 SIGKILL: 产端/消费端都是忽略 SIGTERM 的替身, 停播后 2s 内死透
        stubborn = "import signal,time; signal.signal(signal.SIGTERM," \
                   " signal.SIG_IGN); time.sleep(30)"
        app7 = dash.App(FakeNet())
        app7.page = 2
        prod = subprocess.Popen([sys.executable, "-c", stubborn])
        cons = subprocess.Popen([sys.executable, "-c", stubborn])
        app7.player, app7.raop_cons = prod, cons
        app7.playing_key = "k"
        app7.radio_stop()
        deadline = _time.monotonic() + 2.5
        while _time.monotonic() < deadline \
                and (prod.poll() is None or cons.poll() is None):
            _time.sleep(0.05)
        assert prod.poll() is not None and cons.poll() is not None, \
            "radio_stop 未杀掉忽略 SIGTERM 的进程 (futex 僵尸复现)"
        print("radio out OK (filter/restore/cycle/fallback/keys/vol/kill)")
    finally:
        _sh.rmtree(tmp, ignore_errors=True)


def test_radio_groups():
    """电台分组/收藏: ★组增删持久化 + 切组光标记忆 + 5 键在电台页不被跳页截走."""
    import shutil as _sh
    import dash
    pygame.init()
    pygame.display.set_mode((1, 1))
    canvas = pygame.Surface((dash.W, dash.H))
    tmp = tempfile.mkdtemp(prefix="oneapp")
    dash.RADIO_FAV_CACHE = os.path.join(tmp, "radio_fav.json")
    dash.RADIO_OUT_FILE = os.path.join(tmp, "radio_out.json")
    dash.RAOP_CACHE = os.path.join(tmp, "raop.json")
    try:
        import radio_stations
        assert dash.App.radio_tabs() == ["★", "国家", "省级", "地方", "国际"]
        assert radio_stations.norm_prov("广东省") == "广东"
        assert radio_stations.norm_prov("Guangdong") == "广东"
        assert radio_stations.norm_prov("内蒙古自治区") == "内蒙古"
        assert radio_stations.norm_prov("Overijssel") is None
        assert radio_stations.norm_city("深圳市") == "深圳"
        st = radio_stations.STATIONS[0]
        assert radio_stations.local_rank(
            {"prov": "广东", "city": "深圳"}, "广东", "深圳") == 0
        assert radio_stations.local_rank({"prov": "湖南"}, "广东", None) == 2

        app = dash.App(FakeNet())
        app.page = 2
        app.radio_grp = 1                            # 国家
        nat = app.radio_list()
        assert nat and all(x["grp"] == "国家" for x in nat)
        app.rsel = 2                                 # 光标记忆: 切走再切回
        app.radio_grp_cycle(1)
        app.radio_grp_cycle(-1)
        assert app.radio_grp == 1 and app.rsel == 2, "切组应恢复各组光标"

        app.radio_grp = 1
        app.rsel = 0
        app.key(pygame.K_5)                          # 5 收藏 (不落跳页分支)
        assert app.page == 2
        assert app.radio_fav == [nat[0]["key"]]
        import os as _os
        assert _os.path.exists(dash.RADIO_FAV_CACHE)
        app.radio_grp = 0                            # ★ 组出现该台
        assert [x["key"] for x in app.radio_list()] == [nat[0]["key"]]
        app.key(pygame.K_5)                          # 再按取消
        assert app.radio_fav == [] and app.radio_list() == []
        # 持久化回读 (新实例恢复收藏)
        app.radio_grp = 1
        app.rsel = 0
        app.key(pygame.K_5)
        app2 = dash.App(FakeNet())
        assert app2.radio_fav == [nat[0]["key"]], "收藏应随 radio_fav.json 恢复"
        app.draw(canvas)                             # 全组绘制冒烟 (含空★组)
        app.radio_grp = 3
        app.draw(canvas)
        print("radio groups OK (tabs/fav/persist/cursor/local)")
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


def test_mail_parse():
    """mail.py 解析层: RFC2047 头 / GBK 正文 / HTML 剥离 / 引言签名裁剪."""
    import email as email_mod
    import mail as mailmod
    raw = (b"From: =?gbk?B?1cXI/Q==?= <li@163.com>\r\n"
           b"Subject: =?utf-8?B?5L2g5aW9?=\r\n"
           b"Date: Fri, 14 Nov 2026 09:30:00 +0800\r\n"
           b"Content-Type: multipart/alternative; boundary=\"B\"\r\n\r\n"
           b"--B\r\n"
           b"Content-Type: text/plain; charset=\"gbk\"\r\n\r\n"
           b"\xc4\xe3\xba\xc3\xa3\xac\xd5\xe2\xca\xc7\xd5\xfd\xce\xc4\xa1\xa3"
           b"\r\n> \xd2\xfd\xd1\xd4\r\n-- \r\n\xc7\xa9\xc3\xfb\r\n"
           b"--B\r\n"
           b"Content-Type: text/html; charset=\"utf-8\"\r\n\r\n"
           b"<p>hello&nbsp;<b>world</b></p><script>x()</script>\r\n"
           b"--B--\r\n")
    msg = email_mod.message_from_bytes(raw)
    assert mailmod.decode_subject(msg["Subject"]) == "你好"
    assert mailmod.decode_from(msg["From"], inbox=True) == "li@163.com"
    assert mailmod.decode_from(msg["From"], inbox=False) == "张三"
    body = mailmod.extract_body(msg)
    assert body == "你好，这是正文。", body          # 引言/签名已裁
    assert mailmod.extract_preview(msg).startswith("你好，这是正文。")
    # 纯 HTML 邮件: plain 缺失 → 剥离兜底
    raw2 = (b"From: x@y.com\r\nSubject: t\r\n"
            b"Content-Type: text/html; charset=\"utf-8\"\r\n\r\n"
            b"<style>p{}</style><p>hi&nbsp;ok</p>")
    msg2 = email_mod.message_from_bytes(raw2)
    assert mailmod.extract_body(msg2) == "hi ok", mailmod.extract_body(msg2)
    assert __import__("re").match(r"^\d\d-\d\d \d\d:\d\d$",
                                  mailmod.fmt_date(msg["Date"]))
    assert mailmod.fmt_date(None) == ""
    print("mail parse OK (RFC2047/GBK/html/quotes)")


def test_mail_utf7():
    """mail.py IMAP UTF-7: 解码已知 163 夹名 + 编解码往返."""
    import mail as mailmod
    assert mailmod._decode_imap_utf7("&XfJT0ZAB-") == "已发送"
    assert mailmod._decode_imap_utf7("&g0l6P3ux-") == "草稿箱"
    assert mailmod._decode_imap_utf7("INBOX") == "INBOX"
    assert mailmod._decode_imap_utf7("abc&-def") == "abc&def"
    assert mailmod._encode_imap_utf7("已发送") == "&XfJT0ZAB-"
    for s in ("已发送", "草稿箱", "草稿&箱", "Notes", "收&件箱2"):
        assert mailmod._decode_imap_utf7(mailmod._encode_imap_utf7(s)) == s, s
    assert mailmod.should_skip("&g0l6P3ux-") and mailmod.should_skip("草稿箱")
    assert mailmod.should_skip("Junk")            # QQ 垃圾夹 (英文名)
    assert mailmod.should_skip("Drafts")          # QQ 草稿夹 (英文名)
    assert not mailmod.should_skip("INBOX") and not mailmod.should_skip("已发送")
    assert not mailmod.should_skip("Sent Messages")   # QQ 已发送保留
    print("mail utf7 OK (163 folder names/roundtrip)")


def test_mail_accounts():
    """mail.py 账号存储: 0600 落盘 / 增删改 / provider 注册表与未知域回落."""
    import mail as mailmod
    import stat as stat_mod
    tmp = tempfile.mkdtemp(prefix="oneapp")
    mailmod.MAIL_FILE = os.path.join(tmp, "mail.json")
    assert mailmod.load_accounts() == {"accounts": [], "last": ""}
    assert mailmod.upsert_account("x@163.com", "code1")["ok"]
    # 同邮箱 (大小写不敏感) 再存 = 改授权码, 不新增
    assert mailmod.upsert_account("X@163.com", "code2")["ok"]
    d = mailmod.load_accounts()
    assert len(d["accounts"]) == 1 and d["accounts"][0]["auth"] == "code2"
    assert d["last"] == "x@163.com"
    assert stat_mod.S_IMODE(os.stat(mailmod.MAIL_FILE).st_mode) == 0o600
    mailmod.upsert_account("y@qq.com", "k")
    mailmod.remove_account("x@163.com")
    d = mailmod.load_accounts()
    assert [a["email"] for a in d["accounts"]] == ["y@qq.com"]
    assert d["last"] == "y@qq.com"
    p = mailmod.provider_for("x@163.com")
    assert p["imap"] == "imap.163.com" and p["port"] == 993
    assert mailmod.provider_for("u@UnknownMail.example")["imap"] == \
        "imap.unknownmail.example"                 # 未知域回落 (预留扩展)
    assert not mailmod.upsert_account("noat", "k")["ok"]
    assert not mailmod.upsert_account("a@b.com", "")["ok"]
    print("mail accounts OK (0600/upsert/provider)")


def test_mail_keys():
    """邮件 app 键流: 网格直开 → 登录表单 → 文件夹 → 列表 → 详情 → 逐层 Esc."""
    import mail as mailmod
    import dash
    pygame.init()
    pygame.display.set_mode((1, 1))
    canvas = pygame.Surface((dash.W, dash.H))
    tmp = tempfile.mkdtemp(prefix="oneapp")
    mailmod.MAIL_FILE = os.path.join(tmp, "mail.json")
    app = dash.App(FakeNet())
    # 网格数字直开: 邮件第 7 卡
    app.page = 5
    app.key(pygame.K_7)
    assert app.tool == "mail" and app.mail_lv == "acct"
    # 登录表单: 邮箱 → Enter → 授权码 → Enter 提交
    app.key(pygame.K_RETURN)
    assert app.mail_lv == "login" and app.mail_edit == 0
    for c in "user@163.com":
        app.key(0, c)
    app.key(pygame.K_RETURN)
    assert app.mail_edit == 1
    for c in "authcode1":
        app.key(0, c)
    assert app.mail_auth_buf == "authcode1"
    app.key(pygame.K_RETURN)
    assert ("job", "mail_login") in app.net.got
    # 登录成功 → 落盘 0600 + 直进文件夹页 (虚拟夹: 未读/红旗; INBOX 提升)
    app.on_net("data", "mail_login", {"ok": True, "unread": 3,
                                      "email": "user@163.com",
                                      "auth": "authcode1"})
    assert app.mail_lv == "folders" \
        and app.mail_cur.get("email") == "user@163.com"
    import stat as stat_mod
    assert stat_mod.S_IMODE(os.stat(mailmod.MAIL_FILE).st_mode) == 0o600
    assert ("job", "mail_folders:user@163.com") in app.net.got
    app.on_net("data", "mail_folders:user@163.com", {"ok": True, "folders": [
        {"raw": "INBOX", "name": "INBOX", "unread": 3, "total": 12},
        {"raw": "\"&XfJT0ZAB-\"", "name": "已发送", "unread": 0, "total": 4}]})
    assert [f["raw"] for f in app.mail_folders][:2] == ["@unread", "@flagged"]
    assert app.mail_folders[2]["raw"] == "INBOX"          # 收件箱提至第一行
    assert app.mail_folders[3]["name"] == "已发送"
    assert app.mail_unread_n["user@163.com"] == 3  # 绘制时拼 "未读 3" (0 不显示)
    app.mail_sel = 2                              # INBOX (第三列)
    # 三列网格导航: ←→ 同行 ±1, ↑↓ 跨行 ±3
    app.key(pygame.K_RIGHT)
    assert app.mail_sel == 3
    app.key(pygame.K_LEFT)
    assert app.mail_sel == 2
    app.key(pygame.K_UP)
    assert app.mail_sel == 0
    app.key(pygame.K_DOWN)
    assert app.mail_sel == 3
    app.mail_sel = 0
    # 全部未读: Enter → 聚合视图 (跨夹搜索)
    app.key(pygame.K_RETURN)
    assert app.mail_lv == "list" and app.mail_folder == "@unread"
    assert ("job", "mail_all:user@163.com:unread") in app.net.got
    app.on_net("data", "mail_all:user@163.com:unread", {"ok": True, "total": 2,
                                                    "msgs": [
        {"uid": "11", "subject": "你好", "from": "a@b.com",
         "date": "11-14 09:00", "preview": "内容", "seen": False,
         "flagged": False, "fld": "INBOX", "fldn": "INBOX"},
        {"uid": "10", "subject": "旧的", "from": "c@d.com",
         "date": "11-13 08:00", "preview": "", "seen": True,
         "flagged": True, "fld": "Bob", "fldn": "Bob"}]})
    assert app.mail_total == 2 and len(app.mail_msgs) == 2
    assert app.mail_got                     # 列表已落地 (区分空/加载中)
    app.key(pygame.K_x)                           # 列表层丝印方向: X=↓
    assert app.mail_sel == 1
    app.key(pygame.K_f)                           # F=↑ (输入层仍是字母)
    assert app.mail_sel == 0
    # 未读+旗标口径: 已读且无旗标的行本地即时消失 (uid 10 已读但有旗 → 保留)
    view = app._mail_view_msgs()
    assert [m["uid"] for m in view] == ["11", "10"], view
    app.mail_msgs[1]["flagged"] = False
    assert [m["uid"] for m in app._mail_view_msgs()] == ["11"]
    app.mail_msgs[1]["flagged"] = True
    app.key(pygame.K_RETURN)                      # 开详情 → mail_body:夹:uid
    assert app.mail_lv == "detail" and app.mail_body_uid == "11"
    assert app.mail_body_fld == "INBOX"
    assert ("job", "mail_body:INBOX:11") in app.net.got
    app.on_net("data", "mail_body:INBOX:11",
               {"ok": True, "uid": "11", "fld": "INBOX",
                "text": "第一行\n第二行",
                "subject": "你好", "msg_id": "<m11@163.com>",
                "refs": "", "from_addr": "a@b.com", "date": "11-14 09:00"})
    assert app.mail_body == "第一行\n第二行"
    assert app.mail_c_meta.get("msg_id") == "<m11@163.com>"
    # 详情 4/5/6: 标已读 / 红旗 / 回复 (tag 带夹+uid) + Esc 回列表
    app.key(pygame.K_4)
    assert ("job", "mail_act:INBOX:11:mark_read") in app.net.got
    app.key(pygame.K_5)
    assert ("job", "mail_act:INBOX:11:flag") in app.net.got
    app.key(pygame.K_6)                           # 回复 → compose 预填
    assert app.mail_lv == "compose" and app.mail_c_reply
    assert app.mail_c["to"] == "a@b.com" and app.mail_c["subject"] == "Re: 你好"
    assert app.mail_c_quote.startswith("在 11-14 09:00，a@b.com 写道：\n> 第一行")
    assert app._silk_input_active()               # 写信态: F/X/Z/C 保持字母
    app.key(pygame.K_ESCAPE)                      # Esc 回详情 (入口层)
    assert app.mail_lv == "detail"
    assert not app._silk_input_active()           # 列表/详情态: 丝印方向生效
    app.key(pygame.K_ESCAPE)
    assert app.mail_lv == "list"
    # 列表 5: 写新邮件 (空白) → Esc 回列表
    app.key(pygame.K_5)
    assert app.mail_lv == "compose" and not app.mail_c_reply \
        and app.mail_c == {"to": "", "subject": "", "body": ""}
    app.key(pygame.K_ESCAPE)
    assert app.mail_lv == "list"
    app.key(pygame.K_ESCAPE)
    assert app.mail_lv == "folders"
    # 文件夹层 (列表 Esc 按需进入): 虚拟夹置顶 → Enter 真实 INBOX 换夹
    app.on_net("data", "mail_folders", {"ok": True, "folders": [
        {"raw": "INBOX", "name": "INBOX", "unread": 3, "total": 12},
        {"raw": "\"&XfJT0ZAB-\"", "name": "已发送", "unread": 0, "total": 4}]})
    assert [f["raw"] for f in app.mail_folders][:2] == ["@unread", "@flagged"]
    app.mail_sel = 2                              # INBOX (第三列)
    app.key(pygame.K_RIGHT)                   # 三列网格: → 同行 +1
    assert app.mail_sel == 3
    app.key(pygame.K_LEFT)
    assert app.mail_sel == 2
    app.key(pygame.K_UP)                      # ↑ 跨行 -3 (钳 0)
    assert app.mail_sel == 0
    app.key(pygame.K_DOWN)                    # ↓ 跨行 +3
    assert app.mail_sel == 3
    app.mail_sel = 2                              # INBOX (虚拟夹 2 个在前)
    app.key(pygame.K_RETURN)
    assert app.mail_lv == "list" and app.mail_folder == "INBOX"
    assert sum(1 for t in app.net.got
               if t[0] == "job" and t[1].startswith("mail_list")) >= 1
    app.on_net("data", "mail_list:user@163.com",
               {"ok": True, "total": 25, "page": 0, "has_more": True,
                "msgs": [{"uid": "b%d" % i, "subject": "m%d" % i,
                          "from": "x", "date": "d", "preview": "",
                          "seen": True, "flagged": False}
                         for i in range(10)]})
    assert len(app.mail_msgs) == 10 and app.mail_has_more
    assert app._mail_view_msgs() == app.mail_msgs  # 单夹全量 (已读也显示)
    app.key(pygame.K_6)                       # 加载更多 (更早 10 封)
    assert ("job", "mail_list:user@163.com:p1") in app.net.got
    app.on_net("data", "mail_list:user@163.com:p1",
               {"ok": True, "total": 25, "page": 1, "has_more": False,
                "msgs": [{"uid": "a%d" % i, "subject": "o%d" % i,
                          "from": "x", "date": "d", "preview": "",
                          "seen": True, "flagged": False}
                         for i in range(10)]})
    assert len(app.mail_msgs) == 20 and not app.mail_has_more
    app.key(pygame.K_ESCAPE)
    assert app.mail_lv == "folders"
    app.key(pygame.K_ESCAPE)
    assert app.mail_lv == "acct" and app.mail_cur is None
    # 账号层 d 删除 → dialog 确认 → 落盘
    app.key(pygame.K_d)
    assert app.dialog and app.dialog[0] == "delmail"
    app.key(pygame.K_RETURN)
    assert app.mail.get("accounts") == []
    # Esc 账号层 → 回工具网格
    app.key(pygame.K_ESCAPE)
    assert app.tool is None
    # 五层渲染冒烟 (空态/滚动/旗标角标)
    app.tool_open("mail")
    app.mail_unread["user@163.com"] = 5
    app.draw(canvas)
    app.mail_lv = "login"
    app.mail_acct_buf = "u@163.com"
    app.mail_auth_buf = "abc"
    app.draw(canvas)
    app.mail_lv = "folders"
    app.mail_cur = {"email": "u@163.com", "auth": "x"}
    app.mail_folders = [{"raw": "INBOX", "name": "INBOX", "unread": 2,
                         "total": 9}]
    app.mail_sel = 0
    app.draw(canvas)
    app.mail_lv = "list"
    app.mail_folder = "INBOX"
    app.mail_msgs = [{"uid": "1", "subject": "s", "from": "f", "date": "d",
                      "preview": "p" * 300, "seen": False, "flagged": True}]
    app.mail_sel = 0
    app.draw(canvas)
    app.mail_lv = "detail"
    app.mail_body_uid = "1"                   # 详情按 uid 定位 (与列表行对齐)
    app.mail_body = "正文" * 200
    app.mail_body_scroll = 3
    app.draw(canvas)
    app.mail_lv = "compose"                   # 写信层冒烟 (有引用)
    app.mail_c = {"to": "x@163.com", "subject": "Re: s", "body": "好的"}
    app.mail_c_quote = "在 x，y 写道：\n> 原文"
    app.mail_c_reply = True
    app.mail_cf = 2
    app.draw(canvas)
    app.ime_buf = "ni"
    app.ime_cands = ["你"]
    app.draw(canvas)
    app.ime_clear()
    print("mail keys OK (grid/login/list/folders/list/detail/compose/del/esc/render)")


def test_mail_all():
    """fetch_all 全账号聚合: 逐夹搜索合并 / Date 倒序 / fld+fldn 标注 /
    垃圾草稿夹跳过 / INBOX 不重复 (163 网页版 智能视图的 IMAP 替代)."""
    import mail as mailmod
    orig_cls = mailmod.imaplib.IMAP4_SSL
    selected = []

    class FakeIMAP:
        def __init__(self, host, port, timeout=None):
            self.box = None

        def login(self, u, p):
            return ("OK", ["b"])

        def noop(self):
            return ("OK", ["b"])

        def logout(self):
            return ("OK", ["b"])

        def socket(self):
            return self

        def settimeout(self, t):
            pass

        def xatom(self, *a):
            return ("OK", [])

        def list(self):
            rows = []
            for b in ("INBOX", "Bob", '"&XfJT0ZAB-"', "Junk", "Drafts"):
                rows.append(('() "." %s' % b).encode())
            return ("OK", rows)


        def select(self, box, readonly=False):
            self.box = box.strip('"')
            selected.append(self.box)
            return ("OK", [b"9"])

        def uid(self, cmd, *args):
            a = [cmd] + [x.decode() if isinstance(x, bytes) else x
                         for x in args]
            if "SEARCH" in a:
                key = a[-1]
                uids = {("INBOX", "UNSEEN"): b"3",
                        ("Bob", "UNSEEN"): b"5 9",
                        ("Bob", "FLAGGED"): b"9",
                        ("&XfJT0ZAB-", "FLAGGED"): b"2"}.get(
                            (self.box, key), b"")
                return ("OK", [uids])
            head = "(FLAGS BODY.PEEK[HEADER.FIELDS" in a[-1]
            out = []
            for tok in a[1].split(","):
                if head:
                    hdr = {("INBOX", "3"): b"From: inbox@x.com\r\nSubject: inbox-new\r\n"
                           b"Date: Mon, 16 Nov 2026 08:00:00 +0800\r\n\r\n",
                           ("Bob", "5"): b"From: old@x.com\r\nSubject: old\r\n"
                           b"Date: Mon, 09 Nov 2026 10:00:00 +0800\r\n\r\n",
                           ("Bob", "9"): b"From: new@x.com\r\nSubject: new\r\n"
                           b"Date: Sat, 14 Nov 2026 10:00:00 +0800\r\n\r\n",
                           ("&XfJT0ZAB-", "2"): b"From: me@163.com\r\nSubject: sent\r\n"
                           b"Date: Sun, 15 Nov 2026 09:00:00 +0800\r\n\r\n"}.get(
                               (self.box, tok), b"")
                    if hdr:
                        out.append((b"%s (UID %s BODY[HEADER.FIELDS" % (
                            tok.encode(), tok.encode()), hdr))
                else:
                    fl = {("INBOX", "3"): b"", ("Bob", "5"): b"\\Seen", ("Bob", "9"): b"\\Flagged",
                          ("&XfJT0ZAB-", "2"): b"\\Flagged \\Seen"}.get(
                              (self.box, tok), b"")
                    out.append((b"%s (UID %s FLAGS (%s) BODY[]<0>" % (
                        tok.encode(), tok.encode(), fl),
                        b"Subject: t\r\n\r\nhello"))
            return ("OK", out)

    try:
        mailmod.imaplib.IMAP4_SSL = FakeIMAP
        mailmod._SESS.update(M=None, email="", at=0.0)
        r = mailmod.fetch_all({"email": "t@163.com", "auth": "k"}, "uf", 30)
        assert r["ok"], r
        assert r["total"] == 4, r                       # INBOX 1 + Bob 2 + 已发送 1
        msgs = r["msgs"]
        assert [m["uid"] for m in msgs] == ["3", "2", "9", "5"], msgs  # Date 倒序 (INBOX 纳入)
        assert [m["fldn"] for m in msgs] == ["INBOX", "已发送", "Bob", "Bob"]
        assert not msgs[0]["flagged"] and not msgs[0]["seen"]   # INBOX 新未读
        assert msgs[1]["flagged"] and msgs[1]["seen"]   # 已发送副本双旗态
        assert msgs[2]["flagged"] and not msgs[2]["seen"]
        assert msgs[3]["seen"] and not msgs[3]["flagged"]
        assert all(m["preview"].startswith("hello") for m in msgs)
        assert "INBOX" in selected                # INBOX 纳入聚合 (默认视图=聚合)
        assert "Junk" not in selected and "Drafts" not in selected
        r2 = mailmod.fetch_all({"email": "t@163.com", "auth": "k"},
                               "flagged", 30)
        assert r2["ok"] and [m["uid"] for m in r2["msgs"]] == ["2", "9"]
        r3 = mailmod.fetch_all({"email": "t@163.com", "auth": "k"},
                               "unread", 30)   # UI 别名: 只搜 UNSEEN 单键
        assert r3["ok"] and [m["uid"] for m in r3["msgs"]] == ["3", "9", "5"], r3
    finally:
        mailmod.imaplib.IMAP4_SSL = orig_cls
        mailmod._SESS.update(M=None, email="", at=0.0)
    print("mail all OK (aggregate/sort/skip/fld-tag)")


def test_mail_sess():
    """mail.py IMAP 会话复用: 同账号 TTL 内免重登录, 收尾不登出缓存会话;
    过期/换账号重连; 163 登录限流对策."""
    import mail as mailmod
    orig_cls = mailmod.imaplib.IMAP4_SSL

    class FakeIMAP:
        n = 0

        def __init__(self, host, port, timeout=None):
            FakeIMAP.n += 1
            self.id = FakeIMAP.n
            self.logins = self.noops = self.logouts = 0

        def login(self, u, p):
            self.logins += 1
            return ("OK", ["b"])

        def noop(self):
            self.noops += 1
            return ("OK", ["b"])

        def logout(self):
            self.logouts += 1
            return ("OK", ["b"])

        def socket(self):                     # NOOP 探活短超时用 (5s 修复)
            return self

        def settimeout(self, t):
            pass

        def xatom(self, *a):
            return ("OK", [])

        def select(self, box, ro=False):
            return ("OK", [b"2"])

        def uid(self, cmd, *args):
            if "SEARCH" in args:
                return ("OK", [b""])
            return ("OK", [b""])

    try:
        mailmod.imaplib.IMAP4_SSL = FakeIMAP
        mailmod._SESS.update(M=None, email="", at=0.0)
        acct = {"email": "t@163.com", "auth": "k"}
        M1, e = mailmod._connect(acct)
        assert e is None and M1.id == 1
        M2, e = mailmod._connect(acct)            # TTL 内复用, 免登录
        assert e is None and M2 is M1 and M1.logins == 1 and M1.noops == 1
        mailmod.fetch_msgs(acct, "INBOX", 5)      # 操作收尾不登出缓存会话
        assert M1.logouts == 0
        mailmod._SESS["at"] -= 999                # 过期 → NOOP 前就重连
        M3, e = mailmod._connect(acct)
        assert e is None and M3 is not M1 and M1.logouts == 1
        acct2 = {"email": "u@qq.com", "auth": "k"}
        M4, e = mailmod._connect(acct2)           # 换账号 → 旧会话让位
        assert e is None and M4 is not M3 and M3.logouts == 1
        assert M4.logins == 1
    finally:
        mailmod.imaplib.IMAP4_SSL = orig_cls
        mailmod._SESS.update(M=None, email="", at=0.0)
    print("mail sess OK (reuse/expiry/switch/release)")


def test_driver_sym():
    """ZERO 键盘修饰层解码 (纯函数): 官方 sym 层码表 (launcher
    cp0_keyboard_keymap.c default_keymap 同源) — sym+2=@ sym+b=.,
    字母行全是标点: q=~ w=` e=_ r=- t=+ y== u=[ i=] o={ p=}
    a=; s=: d=' (f 无) g=< h=> j=> k=\\ l=| v=, b=. n=/ m=?"""
    import dash
    mods_sym = {383: True, 389: False, 42: False}
    mods_none = {383: False, 389: False, 42: True}
    mods_shift = {383: False, 389: False, 42: True}
    # 数字行 (内核 sym 层码 → 丝印符号; 27=sym+2 内核发 27 而非 3)
    assert dash.keychar(26, "[", mods_sym) == "!"
    assert dash.keychar(27, "]", mods_sym) == "@"
    assert dash.keychar(39, ";", mods_sym) == "#"
    assert dash.keychar(40, "'", mods_sym) == "$"
    assert dash.keychar(41, "`", mods_sym) == "%"
    assert dash.keychar(43, "\\", mods_sym) == "^"
    assert dash.keychar(51, ",", mods_sym) == "&"
    assert dash.keychar(52, ".", mods_sym) == "*"
    assert dash.keychar(53, "/", mods_sym) == "("
    assert dash.keychar(94, "", mods_sym) == ")"
    # 字母行全标点 (官方表) — sym+b=91="." (用户报障案例), sym+q=55="~"
    assert dash.keychar(55, "", mods_sym) == "~"
    assert dash.keychar(69, "", mods_sym) == "`"
    assert dash.keychar(70, "", mods_sym) == "_"
    assert dash.keychar(71, "", mods_sym) == "-"
    assert dash.keychar(72, "", mods_sym) == "+"
    assert dash.keychar(73, "", mods_sym) == "="
    assert dash.keychar(74, "", mods_sym) == "["
    assert dash.keychar(75, "", mods_sym) == "]"
    assert dash.keychar(76, "", mods_sym) == "{"
    assert dash.keychar(77, "", mods_sym) == "}"
    assert dash.keychar(79, "", mods_sym) == ";"
    assert dash.keychar(80, "", mods_sym) == ":"
    assert dash.keychar(81, "", mods_sym) == "'"
    assert dash.keychar(82, "", mods_sym) == '"'
    assert dash.keychar(83, "", mods_sym) == "<"
    assert dash.keychar(85, "", mods_sym) == ">"
    assert dash.keychar(86, "", mods_sym) == "\\"
    assert dash.keychar(89, "", mods_sym) == "|"
    assert dash.keychar(90, "", mods_sym) == ","
    assert dash.keychar(91, "", mods_sym) == "."
    assert dash.keychar(92, "", mods_sym) == "/"
    assert dash.keychar(93, "", mods_sym) == "?"
    # 保留键: 退格/回车/空格 不被 sym 改写
    assert dash.keychar(14, "", mods_sym) == ""
    assert dash.keychar(28, "", mods_sym) == ""
    assert dash.keychar(57, " ", mods_sym) == " "
    # sym + 未表项字母 (基层码直发时) → 大写兜底
    assert dash.keychar(30, "a", mods_sym) == "A"
    # shift 不换码表: 字母大写, 数字原样
    assert dash.keychar(30, "a", mods_shift) == "A"
    assert dash.keychar(3, "2", mods_shift) == "2"
    # 无修饰: 原样
    assert dash.keychar(3, "2", mods_none) == "2"
    assert dash.keychar(27, "]", mods_none) == "]"
    # 驱动码表: 裸 F/Z/X/C 是字母 (输入态打字), 方向键在 fn 层码
    assert dash.EVDEV_MAP[33] == pygame.K_f and dash.EVDEV_CHARS[33] == "f"
    assert dash.EVDEV_MAP[44] == pygame.K_z and dash.EVDEV_CHARS[44] == "z"
    assert dash.EVDEV_MAP[45] == pygame.K_x and dash.EVDEV_CHARS[45] == "x"
    assert dash.EVDEV_MAP[46] == pygame.K_c and dash.EVDEV_CHARS[46] == "c"
    assert dash.EVDEV_MAP[103] == pygame.K_UP      # fn+F
    assert dash.EVDEV_MAP[108] == pygame.K_DOWN    # fn+X
    assert dash.EVDEV_MAP[105] == pygame.K_LEFT    # fn+Z
    assert dash.EVDEV_MAP[106] == pygame.K_RIGHT   # fn+C
    print("driver sym OK (official sym table/@/.:letters-punct/shiftcaps/fzxc)")


def test_mail_compose():
    """写信/回复全流程: 英文直输三字段 → Enter 发送 → 成功清草稿 / 失败保留;
    回复预填 + In-Reply-To; IME 模式切换; 六层渲染冒烟."""
    import mail as mailmod
    import dash
    pygame.init()
    pygame.display.set_mode((1, 1))
    canvas = pygame.Surface((dash.W, dash.H))
    tmp = tempfile.mkdtemp(prefix="oneapp")
    mailmod.MAIL_FILE = os.path.join(tmp, "mail.json")
    app = dash.App(FakeNet())
    app.tool_open("mail")
    app.mail_cur = {"email": "me@163.com", "auth": "k"}
    app.mail_folder = "INBOX"
    app.mail_lv = "list"
    # 列表 5 → 写新邮件 (默认英文直输, 收件人邮箱优先; / 切拼音)
    app.key(pygame.K_5)
    assert app.mail_lv == "compose" and app.mail_cf == 0
    assert app.ime_mode is False
    for c in "friend@qq.com":
        app.key(0, c)
    assert app.mail_c["to"] == "friend@qq.com"
    app.key(pygame.K_RETURN)                      # → 主题
    assert app.mail_cf == 1
    for c in "Hi there":
        app.key(0, c)
    assert app.mail_c["subject"] == "Hi there"
    app.key(pygame.K_RETURN)                      # → 正文
    assert app.mail_cf == 2
    for c in "Long time no see, ok?":
        app.key(0, c)
    app.key(pygame.K_RETURN)                      # 正文 Enter = 发送
    assert ("job", "mail_send") in app.net.got
    assert app.mail_c_busy
    app.on_net("data", "mail_send", {"ok": True})
    assert not app.mail_c_busy \
        and app.mail_c == {"to": "", "subject": "", "body": ""}
    assert app.mail_lv == "list"
    # 校验: 无 @ 收件人 → 不发包
    app.key(pygame.K_5)
    app.key(pygame.K_RETURN)                      # to 空 Enter → 主题
    app.key(pygame.K_LEFT)                        # ←→ 切回收件人
    app.key(0, "n")
    app.key(pygame.K_RETURN)                      # to → 主题
    app.key(pygame.K_RETURN)                      # 主题 → 正文
    app.key(pygame.K_RETURN)                      # 正文 → 发送尝试 (to=n 无 @)
    assert app.net.got.count(("job", "mail_send")) == 1   # 没有第二次
    assert app.mail_cf == 0                       # 焦点拉回收件人
    # 发送失败: 草稿保留, 留在写信层
    app.mail_c["to"] = "friend@qq.com"
    app.mail_cf = 2
    app.key(pygame.K_RETURN)
    assert app.net.got.count(("job", "mail_send")) == 2
    app.on_net("data", "mail_send", {"ok": False, "err": "登录被拒"})
    assert app.mail_lv == "compose" \
        and app.mail_c["to"] == "friend@qq.com"
    # Esc 退拼音串 → 再 Esc 回列表 (草稿丢弃)
    app.ime_mode = True
    app.ime_buf = "ni"
    app.key(pygame.K_ESCAPE)
    assert app.mail_lv == "compose" and not app.ime_buf
    app.key(pygame.K_ESCAPE)
    assert app.mail_lv == "list"
    # 回复: 详情 6 → 预填 + 发送带 In-Reply-To; 完成回详情
    app.mail_lv = "detail"
    app.mail_body_uid = "7"
    app.mail_c_meta = {"uid": "7", "subject": "Re: 问一下", "text": "问题内容",
                       "msg_id": "<m7@qq.com>", "refs": "<m6@163.com>",
                       "from_addr": "boss@qq.com", "date": "11-15 10:00"}
    app.key(pygame.K_6)
    assert app.mail_lv == "compose" and app.mail_c_reply
    assert app.mail_c["to"] == "boss@qq.com"
    assert app.mail_c["subject"] == "Re: 问一下"   # 重复 Re: 不叠加
    assert app.mail_c_quote.startswith("在 11-15 10:00，boss@qq.com 写道：")
    app.key(pygame.K_RETURN)                      # to → subject
    app.key(pygame.K_RETURN)                      # subject → body
    app.key(0, "O")
    app.key(0, "K")
    app.key(pygame.K_RETURN)                      # 发送
    assert app.net.got.count(("job", "mail_send")) == 3
    app.on_net("data", "mail_send", {"ok": True})
    assert app.mail_lv == "detail" and not app.mail_c_reply  # 回跳详情
    # 发送闭包: 引用块拼正文 + 回信头 (直接调闭包验证形状)
    app.mail_lv = "compose"
    app.mail_c = {"to": "a@b.com", "subject": "s", "body": "我的回复"}
    app.mail_c_quote = "在 x，y 写道：\n> 原文"
    app.mail_c_reply = True
    app.mail_c_meta = {"msg_id": "<m@x>", "refs": "<r@x>"}
    fn = app._mail_send_fn()
    r = fn()                                      # mail.send_mail 校验层拦 (假账号)
    assert not r["ok"]                            # 不会真发 (账号假)
    print("mail compose OK (new/reply/ime/send/fail/esc/render)")

    # 渲染冒烟: compose 空态 / 三字段有值+引用 / IME 候选条让位底栏
    app.mail_c_reply = True
    app.draw(canvas)                              # 空正文 + 引用
    app.mail_c = {"to": "a@b.com", "subject": "标题在截断边缘" * 3,
                  "body": "第一行\n第二行"}
    app.mail_cf = 2
    app.draw(canvas)
    app.ime_buf = "ni"
    app.ime_cands = ["你", "泥"]
    app.draw(canvas)
    app.ime_clear()
    print("mail compose render OK")


def test_mail_live():
    """163 真链 (RUN_NET=1 且本机有 ~/.config/imap-smtp-email/.env 时跑;
    无凭据自动跳过 — 设备上无此文件, 商店版测试不受影响)."""
    import mail as mailmod
    envf = os.path.expanduser("~/.config/imap-smtp-email/.env")
    if not os.path.exists(envf):
        print("mail live SKIP (no ~/.config/imap-smtp-email/.env)")
        return
    env = {}
    for line in open(envf):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"')
    addr, auth = env.get("IMAP_USER", ""), env.get("IMAP_PASS", "")
    if not addr or not auth:
        print("mail live SKIP (empty IMAP_USER/IMAP_PASS)")
        return
    acct = {"email": addr, "auth": auth}
    r = mailmod.test_login(acct)
    assert r["ok"], r
    print("  login OK unread=%d" % r["unread"])
    r = mailmod.list_folders(acct)
    assert r["ok"] and r["folders"], r
    inbox = next(f for f in r["folders"] if f["name"].upper() == "INBOX")
    print("  folders %d, INBOX total=%s unread=%s" % (
        len(r["folders"]), inbox.get("total"), inbox.get("unread")))
    r = mailmod.fetch_msgs(acct, inbox["raw"], 5)
    assert r["ok"], r
    for m in r["msgs"]:                   # 单夹口径 = 最近全量 (含已读)
        pass
        print("  %s [%s%s] %s | %s" % (m["date"], "未读" if not m["seen"] else "",
                                       "旗" if m["flagged"] else "",
                                       m["from"][:22], m["subject"][:34]))
    if r["msgs"]:
        uid = r["msgs"][0]["uid"]
        b = mailmod.fetch_body(acct, inbox["raw"], uid)
        assert b["ok"], b
        assert "msg_id" in b and "from_addr" in b   # 回复元数据齐
        print("  body %d chars, msg_id=%s: %s" % (
            len(b["text"]), b.get("msg_id", "")[:24],
            b["text"][:60].replace("\n", " / ")))
    # SMTP 自发自收 (须 MAIL_LIVE_SEND=1 显式开启, 避免常规跑测发信).
    # 轮询用 UID 增量 + 客户端解码主题 — 163 服务端 HEADER 搜索对
    # RFC2047 编码主题不可靠 (邮件在但搜不到, 2026-11-15 实测).
    if os.environ.get("MAIL_LIVE_SEND"):
        import email
        subj = "ONEAPP 发送链路测试 %d" % int(_time.time())
        M, err = mailmod._connect(acct)
        assert not err, err
        M.select("INBOX", readonly=True)
        st, d = M.uid("SEARCH", None, "ALL")
        base = max(int(x) for x in d[0].split()) \
            if st == "OK" and d and d[0] and d[0].split() else 0
        try:
            M.logout()
        except Exception:
            pass
        r = mailmod.send_mail(acct, addr, subj, "链路自测邮件, 可忽略。")
        assert r["ok"], r
        print("  smtp sent to self: %s (base uid>%d)" % (subj, base))
        got = None
        for _ in range(12):                    # 投递轮询 ≤120s (自发延迟波动大)
            _time.sleep(10)
            M, err = mailmod._connect(acct)
            if err:
                continue
            try:
                M.select("INBOX", readonly=True)
                st, d = M.uid("SEARCH", None, "ALL")
                if st != "OK" or not d or not d[0]:
                    continue
                for uid in reversed([x for x in d[0].split()
                                     if int(x) > base]):
                    st2, dd = M.uid("FETCH", uid,
                                    "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
                    if st2 != "OK" or not dd or not isinstance(dd[0], tuple):
                        continue
                    msg = email.message_from_bytes(dd[0][1])
                    if mailmod.decode_subject(msg["Subject"]) == subj:
                        got = uid.decode("ascii")
                        break
                if got:
                    break
            finally:
                try:
                    M.logout()
                except Exception:
                    pass
        assert got, "自发自收未达 (120s)"
        print("  loopback OK uid=%s (到达态不管已读否 — 未读+旗标列表口径正确)" % got)
    print("mail live OK (163 imap+smtp direct)")


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


def test_tv():
    """电视: 台表完整性 + argv 形状 + 播放状态机 (打桩 spawn) + 键位/持久化."""
    import dash
    import tv_channels
    pygame.init()
    pygame.display.set_mode((1, 1))
    canvas = pygame.Surface((dash.W, dash.H))
    tmp = tempfile.mkdtemp(prefix="oneapp")
    dash.TV_LAST_CACHE = os.path.join(tmp, "tv_last.json")
    dash.TV_FAV_CACHE = os.path.join(tmp, "tv_fav.json")

    # ── 台表: 95 条实测源, 4 组齐全, 无空名/坏协议/短时效 token;
    #    每频道 urls 列表 (≥30 台多路备胎), aptv host 挂 UA ──
    assert len(tv_channels.CHANNELS) == 95
    assert tv_channels.GROUPS == ["央视", "卫视", "地方", "国际"]
    for g in tv_channels.GROUPS:
        assert any(c[1] == g for c in tv_channels.CHANNELS), g
    names = [c[0] for c in tv_channels.CHANNELS]
    assert len(set(names)) == len(names), "频道重名"
    for n, g, us in tv_channels.CHANNELS:
        assert n and isinstance(us, list) and us, n
        for u in us:
            assert u.startswith(("http://", "https://")), (n, u)
            assert "txSecret" not in u and "user_session_id" not in u, n
        assert g in tv_channels.GROUPS
    assert sum(1 for c in tv_channels.CHANNELS if len(c[2]) > 1) >= 30
    assert tv_channels.UA_BY_HOST.get("101.35.240.114") == "AptvPlayer-UA"

    # ── argv: 硬解 copy 前缀 + 解码器旗标; 软解无; 关音频 -an ──
    app = dash.App(FakeNet())
    app.page = 5
    app.tool = "tv"
    a_hw = app.tv_argv("http://x/live.m3u8", True, True)
    assert "-c:v" in a_hw and "h264_v4l2m2m" in a_hw
    assert "-num_capture_buffers" in a_hw
    vf = a_hw[a_hw.index("-vf") + 1]
    assert vf.startswith("copy,fps=12,scale=320:170"), vf
    assert a_hw[a_hw.index("-pix_fmt") + 1] == "rgb24"
    assert a_hw[a_hw.index("-f") + 1:][:2] == ["rawvideo", "pipe:1"]
    assert a_hw[-3:] == ["-f", "pulse", "default"]
    a_sw = app.tv_argv("http://x/live.m3u8", False, True)
    assert "h264_v4l2m2m" not in a_sw
    assert a_sw[a_sw.index("-vf") + 1].startswith("fps=12,scale=320:170")
    a_an = app.tv_argv("http://x/live.m3u8", False, False)
    assert a_an[-1] == "-an" and "pulse" not in a_an
    a_ua = app.tv_argv("http://101.35.240.114:88/live.php?id=CCTV1", False, True)
    assert a_ua[a_ua.index("-user_agent") + 1] == "AptvPlayer-UA"
    assert "101.35.240.114" in a_ua[a_ua.index("-i") + 1]
    assert "-user_agent" not in a_sw
    # 硬解探测: 无 v4l2 的机器 (Mac) 恒 False; True 仅可能出现在有
    # /sys/class/video4linux 的设备 (bcm2835-codec-decode)
    hw_probe = dash.App.tv_hwdec_available()
    assert isinstance(hw_probe, bool)
    assert hw_probe is False or os.path.isdir("/sys/class/video4linux")

    # ── 播放状态机: 打桩 spawn, 管道喂真帧字节 → 读帧线程只留最新 ──
    class FakeProc:
        def __init__(self):
            self.returncode = None
            self.o_r, self.o_w = os.pipe()     # stdout: 测试端写帧
            self.e_r, self.e_w = os.pipe()     # stderr
            self.stdout = os.fdopen(self.o_r, "rb", buffering=0)
            self.stderr = os.fdopen(self.e_r, "rb", buffering=0)

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    procs = []
    open_w = []                                # 测试退出兜底关管道

    def fake_spawn(self, cmd):
        p = FakeProc()
        procs.append((p, cmd))
        open_w.extend((p.o_w, p.e_w))
        return p

    real_spawn = dash.App._tv_spawn
    dash.App._tv_spawn = fake_spawn
    real_log = dash.App._tv_log
    dash.App._tv_log = lambda self, line: None   # 测试不污染 /tmp/oneapp_tv.log
    app2 = None
    try:
        app.tv_grp = 1                         # 默认 0=收藏(空), 钉央视组
        lst = app.tv_list()
        assert len(lst) == 19 and lst[0][0] == "CCTV1"   # 央视组
        app.draw(canvas)                       # 列表页渲染不崩
        # Enter 起播 → 读帧线程消费管道帧
        app.tv_key(pygame.K_RETURN)
        assert app.tv_playing and app.tv_playing[0] == "CCTV1"
        assert app.tv_playing[2][0] in procs[0][1]       # 首路 URL 进 argv
        frame1 = bytes([64]) * (dash.W * dash.H * 3)
        frame2 = bytes([200]) * (dash.W * dash.H * 3)
        os.write(procs[-1][0].o_w, frame1 + frame2)
        for _ in range(60):                    # ≤3s 等读帧线程
            if app.tv_frames >= 2:
                break
            _time.sleep(0.05)
        assert app.tv_frames == 2, app.tv_frames
        assert app.tv_frame == frame2          # 只留最新帧
        assert app.tv_first_at is not None
        app.draw(canvas)                       # 播放页渲染 (真 frombuffer 路径)
        # 播放态 ↑ 换台: 杀旧起新 (央视组循环 → 上一台 = 组内最后台)
        app.tv_key(pygame.K_UP)
        assert app.tv_playing[0] == lst[-1][0]
        assert len(procs) == 2
        assert procs[0][0].returncode is not None       # 旧进程已停
        # 播放态 Esc → 回列表 (tool 仍是 tv)
        app.key(pygame.K_ESCAPE)
        assert app.tv_playing is None and app.tool == "tv"
        assert len(procs) == 2                 # Esc 不再起新进程

        # ── 看门狗: 起播超时, 单路频道 (钉软解) → 无备胎直接报错 ──
        one = ("单路台", "央视", ["http://dead/1.m3u8"])
        app.tv_err = ""
        app.tv_play(one, hw=False)
        app.tv_started = _time.monotonic() - 21.0
        app.tv_watchdog()
        assert app.tv_playing is None and app.tv_err, app.tv_err
        assert procs[-1][0].returncode is not None

        # ── failover: 首路失败自动换二路, 全尽才报无法播放 ──
        two = ("双路台", "央视", ["http://a/1.m3u8", "http://b/2.m3u8"])
        app.tv_play(two, hw=False)
        assert "http://a/1.m3u8" in procs[-1][1] and app.tv_url_i == 0
        app.tv_started = _time.monotonic() - 21.0      # 首路超时
        app.tv_watchdog()
        assert app.tv_playing and app.tv_url_i == 1    # 已自动切二路
        assert "http://b/2.m3u8" in procs[-1][1]
        app.tv_started = _time.monotonic() - 21.0      # 二路也超时
        for _ in range(4):                # 设备: 换路后 hw 重探, 多一步 hw→sw
            app.tv_watchdog()
            if app.tv_playing is None:
                break
            app.tv_started = _time.monotonic() - 21.0
        assert app.tv_playing is None and "无法播放" in app.tv_err

        # ── 断流 (出过帧 + 8s 无新帧) → 换二路续播而非直接停 ──
        app.tv_play(two, hw=False)
        os.write(procs[-1][0].o_w, frame1)
        for _ in range(60):
            if app.tv_frames >= 1:
                break
            _time.sleep(0.05)
        assert app.tv_frames == 1
        app.tv_last_at = _time.monotonic() - 9.0
        app.tv_watchdog()
        assert app.tv_playing and app.tv_url_i == 1 \
            and "http://b/2.m3u8" in procs[-1][1]
        app.tv_stop()                                    # 清场

        # ── 播放态 ←→ 手动换线路 (卡顿自救); 单路提示 ──
        app.tv_play(two, hw=False)
        app.tv_key(pygame.K_RIGHT)             # 1路 → 2路
        assert app.tv_playing and app.tv_url_i == 1
        assert "http://b/2.m3u8" in procs[-1][1]
        app.tv_key(pygame.K_RIGHT)             # 2路 → 循环回 1路
        assert app.tv_url_i == 0 and "http://a/1.m3u8" in procs[-1][1]
        app.tv_key(pygame.K_LEFT)              # 反向回 2路
        assert app.tv_url_i == 1
        app.tv_stop()
        app.tv_play(one, hw=False)             # 单路频道: 提示不换
        app.tv_key(pygame.K_RIGHT)
        assert app.tv_url_i == 0 and app.osd \
            and "无备用线路" in app.osd[0]
        app.tv_stop()                                    # 清场

        # ── 同线路可修优先: 硬解 v4l2 报错 → 软解 (不换路) ──
        app.tv_err = ""
        ch = app.tv_list()[0]                          # CCTV1 (多路)
        app.tv_play(ch, hw=True, audio=True, att=0)    # 手工指定硬解
        assert procs[-1][0].returncode is None
        procs[-1][0].returncode = 1                    # 模拟进程已退
        app.tv_dead = {"rc": 1, "err": "h264_v4l2m2m: unknown decoder"}
        app.tv_watchdog()
        assert app.tv_playing and app.tv_att == 1 and app.tv_url_i == 0
        assert "h264_v4l2m2m" not in procs[-1][1]
        # 同路软解再挂: 无音频流 → -an (梯级 2, 仍同路)
        procs[-1][0].returncode = 1
        app.tv_dead = {"rc": 1,
                       "err": "Stream map '0:a:0' matches no streams"}
        app.tv_watchdog()
        assert app.tv_playing and app.tv_att == 2 and app.tv_url_i == 0
        assert procs[-1][1][-1] == "-an"
        # 同路 -an 也 404 → 换二路 (att 3)
        procs[-1][0].returncode = 1
        app.tv_dead = {"rc": 1, "err": "Server returned 404"}
        app.tv_watchdog()
        assert app.tv_playing and app.tv_url_i == 1 and app.tv_att == 3
        # 二路也 404 → 线路穷尽 → 报错回列表 (设备: 404 走换路/软解逐步收)
        procs[-1][0].returncode = 1
        app.tv_dead = {"rc": 1, "err": "Server returned 404"}
        for _ in range(4):
            app.tv_watchdog()
            if app.tv_playing is None:
                break
            procs[-1][0].returncode = 1
            app.tv_dead = {"rc": 1, "err": "Server returned 404"}
        assert app.tv_playing is None
        assert "404" in app.tv_err, app.tv_err
        app.tv_stop()                                    # 清场

        # ── 音频焦点: 电台起播把电视踢停 ──
        app.tv_play(ch)
        real_popen = dash.subprocess.Popen
        dash.subprocess.Popen = lambda *a, **k: (_ for _ in ()).throw(
            FileNotFoundError("no ffplay in test"))
        try:
            app.radio_play({"key": "k", "name": "n",
                            "url": "http://x/1.m3u8", "freq": 0})
        finally:
            dash.subprocess.Popen = real_popen
        assert app.tv_playing is None

        # ── 键位: 5 页签循环 (收藏+四分组) + 每组光标独立 + 持久化/恢复 ──
        app.tv_grp = 1                        # 前段换台改过光标, 重置走键位
        app.tv_sel = 0
        app.tv_sels = {1: 0}
        app.tv_key(pygame.K_RIGHT)            # 央视→卫视
        assert app.tv_grp == 2
        app.tv_sel = 5
        app.tv_key(pygame.K_RIGHT)            # → 地方 (卫视光标记 5)
        assert app.tv_grp == 3 and app.tv_sel == 0
        app.tv_key(pygame.K_RIGHT)            # → 国际
        app.tv_key(pygame.K_RIGHT)            # 国际 → 收藏 (循环)
        assert app.tv_grp == 0 and app.tv_sel == 0
        app.tv_key(pygame.K_LEFT)             # 收藏 → 国际 (反向循环)
        assert app.tv_grp == 4
        app.tv_key(pygame.K_LEFT)             # → 地方
        app.tv_key(pygame.K_LEFT)             # → 卫视恢复光标 5
        assert app.tv_grp == 2 and app.tv_sel == 5
        app.tv_sel = 3
        app.tv_key(pygame.K_RETURN)           # 起播即存档 (grp 存页签名)
        assert netmod.load_json_file(dash.TV_LAST_CACHE, None) \
            == {"grp": "卫视", "sel": 3}
        app.tv_stop()
        app2 = dash.App(FakeNet())            # 新会话恢复 (名寻位)
        app2.tool = "tv"
        app2.tv_restore()
        assert app2.tv_grp == 2 and app2.tv_sel == 3
        # 旧格式迁移: int grp (无收藏位) 顺移 +1
        netmod.save_json_file(dash.TV_LAST_CACHE, {"grp": 1, "sel": 2})
        app2.tv_restore()
        assert app2.tv_grp == 2 and app2.tv_sel == 2
        app2.tv_grp = 1
        app2.draw(canvas)                     # 恢复位列表渲染不崩
        app2.key(pygame.K_ESCAPE)             # 列表 Esc → 回工具网格 (外层链)
        assert app2.tool is None

        # ── 收藏: 5 键收/取消 + 收藏组打开即看 + 播放中收藏 ──
        app.tv_fav = []
        app.tv_grp, app.tv_sel = 1, 0         # 央视组 CCTV1
        app.tv_key(pygame.K_5)                # 列表: 收藏选中台
        assert app.tv_fav == ["CCTV1"]
        assert netmod.load_json_file(dash.TV_FAV_CACHE, None) == ["CCTV1"]
        wl = app.tv_list(2)                   # 卫视组首台
        app.tv_grp, app.tv_sel = 2, 0
        app.tv_key(pygame.K_5)
        assert app.tv_fav == ["CCTV1", wl[0][0]]
        app.tv_key(pygame.K_LEFT)             # 卫视→央视
        app.tv_key(pygame.K_LEFT)             # 央视→收藏
        assert app.tv_grp == 0
        assert [c[0] for c in app.tv_list()] == ["CCTV1", wl[0][0]]
        app.draw(canvas)                      # 收藏组渲染 (★标+底栏) 不崩
        app.tv_sel = 1
        app.tv_key(pygame.K_RETURN)           # 收藏组 Enter 直接看
        assert app.tv_playing and app.tv_playing[0] == wl[0][0]
        app.tv_key(pygame.K_5)                # 播放中 5 = 取消收藏当前台
        assert app.tv_fav == ["CCTV1"]
        app.tv_stop()
        app.tv_grp, app.tv_sel = 0, 0
        app.draw(canvas)                      # 单条收藏组不崩
        app.tv_key(pygame.K_5)                # 组内取消 → 空收藏
        assert app.tv_fav == []
        app.draw(canvas)                      # 空收藏组提示不崩
        app.tv_key(pygame.K_LEFT)             # 空组仍可 ←→ 离开 → 国际
        assert app.tv_grp == 4
    finally:
        dash.App._tv_spawn = real_spawn
        dash.App._tv_log = real_log
        app.tv_stop()
        if app2:
            app2.tv_stop()
        for fd in open_w:
            try:
                os.close(fd)
            except OSError:
                pass
    print("tv OK  (95 源 · 状态机/看门狗/重试梯级/焦点/持久化)")


if __name__ == "__main__":
    test_lunar()
    test_wx()
    test_fetchers()
    test_news_parse()
    test_net_cache()
    test_render()
    test_tools()
    test_music()
    test_mu_airplay()
    test_radio_out()
    test_radio_groups()
    test_tv()
    test_voice()
    test_ime()
    test_mail_parse()
    test_mail_utf7()
    test_mail_accounts()
    test_mail_keys()
    test_mail_all()
    test_mail_sess()
    test_driver_sym()
    test_mail_compose()
    if os.environ.get("RUN_NET"):
        test_net_live()
        test_mail_live()
    print("ALL PASS")
