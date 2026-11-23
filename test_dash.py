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
        self.jobs = []                       # (tag, fn) 云同步测试直调

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
        self.jobs.append((tag, fn))

    def last_job(self, tag):
        hit = [fn for t, fn in self.jobs if t == tag]
        return hit[-1] if hit else None

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
    # 大乐透: 500.com HTML 行解析 (前区×5 + 后区×2 + 注数奖金/奖池, 千分位)
    html = ('<tbody id="tdata">'
            '<tr class="t_tr1"><!--<td>2</td>-->'
            '<td class="t_tr1">26132</td>'
            '<td class="cfont2">02</td><td class="cfont2">09</td>'
            '<td class="cfont2">16</td><td class="cfont2">26</td>'
            '<td class="cfont2">35</td>'
            '<td class="cfont4">06</td><td class="cfont4">11</td>'
            '<td class="t_tr1">754,624,606</td><td class="t_tr1">3</td>'
            '<td class="t_tr1">8,814,866</td><td class="t_tr1">128</td>'
            '<td class="t_tr1">101,697</td><td class="t_tr1">321,917,081</td>'
            '<td class="t_tr1">2026-11-18</td></tr>'
            '<tr class="t_tr1"><td class="t_tr1">垃圾行</td></tr>'
            '</tbody>')
    d = fetchers._dlt_parse(html)
    assert d["ok"] and d["latest"]["code"] == "26132"
    assert d["latest"]["reds"] == [2, 9, 16, 26, 35]
    assert d["latest"]["blues"] == [6, 11]
    assert d["latest"]["week"] == "三"
    assert d["latest"]["pool"] == 321917081
    assert d["latest"]["grades"] == [
        {"level": 1, "money": 8814866, "num": 3},
        {"level": 2, "money": 101697, "num": 128}], d["latest"]["grades"]
    assert fetchers._dlt_parse("<html></html>") is None
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
    # 动态源表: set_feeds 换生效表 (on=False 不抓), defaults=内置种子
    d = news_rss.defaults()
    assert len(d) == 10 and d[0]["id"] == "ai" \
        and d[-2]["id"] == "xh_world" and d[-1]["id"] == "xh_mil" \
        and d[0]["on"] is True
    news_rss.set_feeds([{"id": "c_x", "name": "例站", "url":
                         "https://example.com/feed", "on": True},
                        {"id": "ai", "name": "AI 新闻",
                         "url": d[0]["url"], "on": False}])
    assert "c_x" in news_rss._ACTIVE and "ai" not in news_rss._ACTIVE
    news_rss.set_feeds([])                 # 空表 → 不抓任何源 (不炸)
    assert news_rss._ACTIVE == {}
    news_rss.set_feeds(d)                  # 还原内置表 (后续用例/在线链路用)
    assert set(news_rss._ACTIVE) == set(news_rss.FEEDS)
    print("news parse OK (rss+atom+动态源表)")


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
    tmp = tempfile.mkdtemp(prefix="oneapp")
    dash.SETTINGS_FILE = os.path.join(tmp, "settings.json")   # 设备真档隔离:
    fnf = os.path.join(tmp, "news_feeds.json")   # 新闻页断言依赖 ai 居首,
    dash.NEWS_FEEDS_FILE = fnf                   # 真实源表 (用户改过显隐)
    dash.KV_FILES.update({"news_feeds": fnf})    # 不能进断言

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
    # 涨跌幅高→低排序: demo 行情 平安银行 +0.61 > 茅台 -0.53 > 无行情种子
    # (保持 watch 顺序); watch 原序首=茅台 → 排序后首位必是平安银行
    assert its and its[0]["code"] == "sz000001", its
    assert [i["code"] for i in its] == [
        "sz000001", "sh600519", "sh601318", "sz300750", "sh688981"], its
    assert its[0]["pchg"] == 0.61 and its[1]["pchg"] == -0.53, its
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
    # n 添加 → 本地落盘 + 上云 OSD (云同步走 kv; 东财镜像同步已删)
    app.buf = "601318"                               # 新代码 (不撞去重门)
    app.fin_input = False
    app.fin_add_commit()
    assert [i["code"] for i in app.watch["items"]] == \
        ["sh600519", "sh601318"], app.watch["items"]
    app.draw(canvas)                                 # 行情页渲染 (无 KEY 框)
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
    # 快捷键 4-7/9 → 页 1-5, 8 邮件 (2026-11-16 主页对调: 8=邮件 9=工具;
    # 新闻/电台页数字为功能键)
    pg = pygame
    for key, page in [(pg.K_4, 1), (pg.K_5, 2), (pg.K_6, 3),
                      (pg.K_7, 4), (pg.K_9, 5)]:
        app.dialog = None
        app.page = 0
        app.key(key)
        assert app.page == page, (key, app.page)
    app.dialog = None
    app.page = 0
    app.key(pg.K_8)
    assert app.tool == "mail" and app.page == 0   # 主页 8 直开邮件
    app.tool_close()
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
        if tid == "lot":
            app.lot_type = 1                      # 大乐透页也渲一帧
            app.draw(canvas)
            app.lot_type = 0
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
    assert app.news_src_i == len(app.news_srcs()) - 1  # 向左回绕到末源
    app.key(pg.K_RIGHT)
    assert app.news_src_i == 0
    app.news_detail = None
    sel0 = app.news_sel
    app.key(pg.K_DOWN)
    assert app.news_sel == sel0 + 1                   # ↓ 选择下一条
    app.key(pg.K_UP)
    assert app.news_sel == sel0                       # ↑ 选择上一条
    # P = 列表翻页 (2026-11-21 用户定版, 不再跳工具页; Shift+P 上翻);
    # demo 仅 2 条, 翻页被钳回原地 — 断言核心是留在新闻页
    app.key(pg.K_p)
    assert app.page == 4, app.page
    app.key(pg.K_p, "P")                              # 大写 = 上翻
    assert app.page == 4 and app.news_detail is None
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
    # 网格: 方向键 / 数字直开 (fx1 unit2 cal3 lot4 timer5 music6) / Esc 回主页
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
    # 主页底栏 8 邮件 / 9 工具 (2026-11-16 用户定版: 对调): 网格不再列邮件
    assert "mail" not in [t for t, _ in dash.TOOL_APPS]
    app.tool = None
    app.page = 0
    app.key(pygame.K_9)
    assert app.page == 5                          # 主页 9 → 工具网格
    app.key(pygame.K_ESCAPE)
    assert app.page == 0
    app.key(pygame.K_8)
    assert app.tool == "mail"                     # 主页 8 直开邮件
    # 回归 (2026-11-16 底栏重叠): 工具态 page 仍 0, 页签条必须让位工具底栏
    s2 = pygame.Surface((dash.W, dash.H))
    s2.fill((1, 2, 3))                            # 哨兵底色
    SENT = [(x, yy) for x in range(5, 20) for yy in range(150, 168)]
    app.draw_tabs(s2)
    assert all(s2.get_at(p)[:3] == (1, 2, 3) for p in SENT)   # 工具开着不画
    app.tool_close()
    app.draw_tabs(s2)
    assert any(s2.get_at(p)[:3] != (1, 2, 3) for p in SENT)   # 主页正常画
    app.page = 5
    app.tool_open("music")
    assert app.tool == "music" and app.mu_view == "play"   # 主页即播放页
    app.key(pygame.K_ESCAPE)                      # Esc 直接退出 (无 1s 长按)
    assert app.tool is None
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
    # 单位换算: r 换类 / e 交换 (真实键值+unicode 形态 — 走 key() 全链,
    # key(0,ch) 绕过映射层抓不住丝印劫持, 2026-11-16 用户报 r/e 失效补测)
    app.tool_open("unit")
    assert app.tool == "unit"
    app.cv_field = 0                  # 与 fx 共用字段模型, fx 段落残留 1
    c0, f0, t0 = app.u_cat, app.u_from, app.u_to
    app.key(pygame.K_r, "r")                      # r 换类
    assert app.u_cat == (c0 + 1) % len(dash.UCATS)
    nu = len(dash.UCATS[app.u_cat][1])
    assert app.u_from < nu and app.u_to < nu      # 类别切换后光标钳位
    app.key(pygame.K_e, "e")                      # e 交换源/目标
    assert app.u_from == t0 and app.u_to == f0
    app.key(pygame.K_LEFT)
    assert app.cv_field == 3                      # 类别字段 (unit 4 字段, 环绕)
    app.key(pygame.K_ESCAPE)
    assert app.tool is None                       # Esc 回网格
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
    # 彩票: ↑↓ 滚动边界 + ←→ 换彩种 (光标归零/环绕)
    app.tool_open("lot")
    app.data["ssq"] = {"issues": [{"code": "1"}] * 5}
    app.data["dlt"] = {"issues": [{"code": "1"}] * 7}
    app.lot_scroll = 0
    app.key(pygame.K_UP)
    assert app.lot_scroll == 0                    # 顶界
    app.key(pygame.K_DOWN)
    assert app.lot_scroll == 1
    app.key(pygame.K_RIGHT)
    assert app.lot_type == 1 and app.lot_scroll == 0   # 大乐透, 光标归零
    app.key(pygame.K_DOWN)
    assert app.lot_scroll == 1                    # 大乐透独立滚动
    app.key(pygame.K_LEFT)
    assert app.lot_type == 0 and app.lot_scroll == 0   # 回双色球
    app.key(pygame.K_LEFT)
    assert app.lot_type == 1                      # ← 环绕到大乐透
    app.tool_close()
    print("tools OK (grid/fx/timer/cal/lot)")


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
            # 回归 (2026-11-16 键位定版2): 播放页 5=输出 7=停止 8=选源, 0 兼容
            app.mu_view = "play"
            app.key(pygame.K_5, "5")              # 输出 (无设备→OSD 保持本机)
            assert app.mu_view == "play" and app.mu_playing
            app.key(pygame.K_8, "8")              # 选源 (播放后台继续)
            assert app.mu_view == "src" and app.mu_playing
            app.mu_view = "play"
            app.key(pygame.K_0, "0")              # 0 兼容选源
            assert app.mu_view == "src"
            app.mu_view = "play"
            app.key(pygame.K_7, "7")              # 停止 → 回源选择
            assert app.mu_playing is None and app.mu_view == "src"
            app.draw(pygame.Surface((dash.W, dash.H)))   # 均分底栏渲染无异常
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
    mu_tick 双判据 (消费端退=曲终)/音量路由/外地死路隔离+16s 连接超时回本机/
    本地时长探针记忆化."""
    import shutil as _sh
    import dash

    pygame.init()
    pygame.display.set_mode((1, 1))
    tmp = tempfile.mkdtemp(prefix="onemuap")
    real_last, real_out = dash.MU_LAST_CACHE, dash.MU_OUT_CACHE
    real_cache = dash.RAOP_CACHE
    real_net = dash._raop_local_net
    dash.MU_LAST_CACHE = os.path.join(tmp, "mu_last.json")
    dash.MU_OUT_CACHE = os.path.join(tmp, "mu_out.json")
    dash.RAOP_CACHE = os.path.join(tmp, "raop.json")
    dash._raop_local_net = lambda: "1.1.1"    # 模拟"在家里" (音箱同网段)
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
            app.key(pygame.K_5)                   # 5=输出 (2026-11-16 定版)
            assert app.mu_out == 1 and played == []
            app.key(pygame.K_5)
            assert app.mu_out == 2 and played == []
            app.key(pygame.K_5)
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
            app.key(pygame.K_5)
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

        # ── 外地/换网 (音箱不在本网段): 5 键不进死路 + 僵会话回本机 ──
        dash._raop_local_net = lambda: "9.9.9"
        app.mu_out = 1
        app.raop = devs
        app.mu_playing = None
        app.mu_proc = None
        app.mu_ff = None
        app.mu_view = "play"              # 上一段曲终 mu_view 已回 src
        app.key(pygame.K_5)               # 未播放: 归位本机 + 明示本网段无音箱
        assert app.mu_out == 0, app.mu_out
        assert app.osd[0] == "无可用 AirPlay 音箱 (本网段)", app.osd
        assert netmod.load_json_file(dash.MU_OUT_CACHE, {}) == {"id": ""}

        # 本地时长探针记忆化: 首次 ffprobe 记回条目, 之后起播不再探
        # (CM0 上 UI 线程同步 ffprobe = 播放/续播/切输出卡顿元凶)
        probed = []
        real_probe = dash.mu_probe_dur
        dash.mu_probe_dur = lambda p: (probed.append(p) or 88.0)
        loc = {"path": "/tmp/x.mp3", "title": "L"}     # 无 dur
        app.mu_files = [loc]
        real_pop2 = dash.subprocess.Popen
        dash.subprocess.Popen = fake_popen
        try:
            app.mu_play(0)
            app.mu_stop()
            app.mu_play(0)
            assert probed == ["/tmp/x.mp3"], probed    # 第二次不再探
            assert loc.get("dur") == 88.0
        finally:
            dash.subprocess.Popen = real_pop2
            dash.mu_probe_dur = real_probe

        healed = []
        real_mu_play = dash.App.mu_play
        dash.App.mu_play = \
            lambda self, i, p=0.0: healed.append((i, round(p, 1)))
        try:
            app.mu_playing = {"path": "/x.wav", "title": "T", "dur": 90}
            app.mu_files = [app.mu_playing]
            app.mu_play_idx = 0
            app.mu_paused = False
            app.mu_pos0, app.mu_t0 = 30.0, _time.monotonic() - 5
            app.mu_proc = _P(["ffmpeg"], "prod")
            app.mu_ff = _P([sys.executable, "-c"], "cons")   # 僵会话: 消费端
            app.mu_aud_up = None                             #   活着但连不上
            app.mu_out = 1
            app.key(pygame.K_5)           # 播放中 → 僵会话立即回本机同位续播
            assert app.mu_out == 0 and healed == [(0, 30.0)], (app.mu_out,
                                                               healed)

            # 16s 连接超时看门狗: RAOP-UP 未到 → mu_tick 自动回本机
            app.mu_proc = _P(["ffmpeg"], "prod")
            app.mu_ff = _P([sys.executable, "-c"], "cons")
            app.mu_out = 1
            app.mu_is_raop = True
            app.mu_raop_t0 = _time.monotonic() - 20
            app.mu_aud_up = None
            app.mu_advance = True
            app.mu_tick()
            assert app.mu_out == 0 and len(healed) == 2, (app.mu_out, healed)
            assert app.mu_is_raop is False and app.mu_raop_t0 is None
            # 刚起播 (t0 新鲜): 不触发
            app.mu_proc = _P(["ffmpeg"], "prod")
            app.mu_ff = _P([sys.executable, "-c"], "cons")
            app.mu_is_raop = True
            app.mu_raop_t0 = _time.monotonic()
            app.mu_aud_up = None
            app.mu_tick()
            assert len(healed) == 2
            # 本机会话 (mu_is_raop=False): 看门狗永不误杀
            app.mu_proc = _P(["ffplay"], "prod")
            app.mu_ff = None
            app.mu_is_raop = False
            app.mu_raop_t0 = _time.monotonic() - 99
            app.mu_aud_up = None
            app.mu_tick()
            assert len(healed) == 2 and app.mu_proc is not None
            app.mu_kill()                 # 清场
        finally:
            dash.App.mu_play = real_mu_play
        print("mu airplay OK (travel-dead-end/watchdog/probe-cache)")
    finally:
        dash.MU_LAST_CACHE, dash.MU_OUT_CACHE = real_last, real_out
        dash.RAOP_CACHE = real_cache
        dash._raop_local_net = real_net
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
    real_net = dash._raop_local_net
    dash._raop_local_net = lambda: "1.2.3"    # 模拟"在家里" (设备同网段)
    try:
        # 过滤: 只留音箱 (电视/电脑/手机剔除, 缺 ip/id 剔除)
        assert dash._raop_ok({"ip": "1.2.3.4", "id": "x", "name": "起居室HOMEPOD"})
        assert not dash._raop_ok({"ip": "1.2.3.4", "id": "x", "name": "客厅电视"})
        assert not dash._raop_ok({"ip": "1.2.3.4", "id": "x", "name": "MacBook"})
        assert not dash._raop_ok({"ip": "1.2.3.4", "id": "x", "name": "iPhone"})
        assert not dash._raop_ok({"ip": "1.2.3.4", "name": "音箱"})   # 无 id
        assert not dash._raop_ok({"id": "x", "name": "音箱"})         # 无 ip
        # 网段过滤: 外地 (本机在 9.9.9.x) 时家里缓存音箱不可用 → 空表
        dash._raop_local_net = lambda: "9.9.9"
        appf = dash.App(FakeNet())
        appf.raop = devs = [{"ip": "1.2.3.4", "port": 7000, "name": "A音箱",
                             "id": "ida"}]
        assert appf._raop_avail() == []
        dash._raop_local_net = lambda: "1.2.3"
        assert [d["id"] for d in appf._raop_avail()] == ["ida"]
        assert appf._raop_avail() is not appf.raop   # 过滤副本不混用缓存对象表

        # 缓存恢复: radio_out.json 按设备 id 对位 → radio_out=2; 未知 id → 0;
        # 外地 (设备不在本网段) → 不恢复死路选择, 归位 0
        devs = [{"ip": "1.2.3.4", "port": 7000, "name": "A音箱", "id": "ida"},
                {"ip": "1.2.3.8", "port": 7000, "name": "B音箱", "id": "idb"}]
        netmod.save_json_file(dash.RAOP_CACHE, devs)
        netmod.save_json_file(dash.RADIO_OUT_FILE, "idb")
        app = dash.App(FakeNet())
        assert app.radio_out == 2, app.radio_out
        assert app.radio_out_name() == "AirPlay·B音箱"
        netmod.save_json_file(dash.RADIO_OUT_FILE, "gone")
        assert dash.App(FakeNet()).radio_out == 0
        netmod.save_json_file(dash.RADIO_OUT_FILE, "idb")
        dash._raop_local_net = lambda: "9.9.9"
        assert dash.App(FakeNet()).radio_out == 0, "外地不应恢复死路输出"
        dash._raop_local_net = lambda: "1.2.3"
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
        assert app4.radio_out == 0 \
            and app4.osd[0] == "无 AirPlay 设备 (本网段无音箱)", app4.osd

        # 外地 (缓存音箱不在本网段): O 键不再循环进死路 — 归位本机 +
        # 播放中的 AirPlay 僵会话立即回本机重放 (2026-11-19 外地卡死实证)
        dash._raop_local_net = lambda: "9.9.9"
        app4b = dash.App(FakeNet())
        app4b.page = 2
        app4b.raop = devs
        app4b.radio_out = 1
        stk = app4b.radio_list(1)[0]
        app4b.playing_key = stk["key"]        # 模拟 AirPlay 会话中 (真台 key)
        replayed = []
        real_rplay = dash.App.radio_play
        dash.App.radio_play = \
            lambda self, st: replayed.append(st["key"])
        try:
            app4b.key(pygame.K_o)
            assert app4b.radio_out == 0, app4b.radio_out
            assert app4b.osd[0] == "无 AirPlay 设备 (本网段无音箱)"
            assert replayed == [stk["key"]], replayed   # 僵会话回本机重放
            app4b.playing_key = None
            app4b.key(pygame.K_o)                # 未播放: 只提示不重放
            assert app4b.radio_out == 0 and replayed == [stk["key"]]
        finally:
            dash.App.radio_play = real_rplay
        dash._raop_local_net = lambda: "1.2.3"

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
        dash._raop_local_net = real_net
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
        app.key(pygame.K_5)                          # 再按取消 → 二次确认框
        assert app.dialog and app.dialog[0] == "unfav"
        assert app.radio_fav == [nat[0]["key"]]      # 未确认前不动
        app.draw(canvas)                             # 确认框渲染冒烟
        app.key(pygame.K_5)                          # 任意非裁决键 = 关框保留
        assert app.dialog is None and app.radio_fav == [nat[0]["key"]]
        app.key(pygame.K_5)                          # 再开框 → Enter 真删
        app.key(pygame.K_RETURN)
        assert app.dialog is None
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
        # 播放器按魔数选: wav → paplay / 假 webm → ffplay
        assert dash._is_pcm_wav(vf) and not dash._is_pcm_wav(
            os.path.join(dash.VOICES_DIR, "fake.webm"))
        app.sel = 0
        app.play_voice(items[0])
        assert app.playing_voice and app.playing_voice["id"] == "v1"
        assert app.player is not None and app.player.poll() is None
        _time.sleep(0.3)
        app.play_voice(items[0])               # 再按 = 停
        assert app.playing_voice is None and app.player is None
        # 2×2 视窗滚动: 6 条时 ↓↓ 翻窗, ←→ 行内不出行
        for i in range(2):
            vfx = os.path.join(dash.VOICES_DIR, "x%d.wav" % i)
            mkwav(vfx, 1.0)
            app.ideas["voices"].append({"id": "x%d" % i, "cid": "%07d" % i,
                                        "file": vfx, "dur": 1,
                                        "createdAt": 300 + i})
        app.ideas["texts"].append({"id": "t2", "cid": "7" * 12,
                                   "text": "更多", "createdAt": 400})
        app.sel = 0
        app.move_sel(2)                        # ↓ 第二行
        assert app.sel == 2, app.sel
        app.move_sel(2)                        # ↓ 第三行 → 视口滚动
        assert app.sel == 4 and app.scroll == 2, (app.sel, app.scroll)
        app.move_sel(1)                        # → (idx4 = 末行单卡, 行尾不动)
        assert app.sel == 4, app.sel
        app.move_sel(1)                        # 已在行尾, 仍不动
        assert app.sel == 4, app.sel
        app.move_sel(-2)                       # ↑ 回第二行 (保持列), 窗口已对
        assert app.sel == 2 and app.scroll == 2, (app.sel, app.scroll)
        # 清理滚动测试追加物, 恢复删除断言前置态
        for v in list(app.ideas["voices"]):
            if v["id"].startswith("x"):
                try:
                    os.remove(v["file"])
                except OSError:
                    pass
        app.ideas["voices"] = [v for v in app.ideas["voices"]
                               if not v["id"].startswith("x")]
        app.ideas["texts"] = [t for t in app.ideas["texts"] if t["id"] != "t2"]
        app.items_sorted = None
        app.sel = 0
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


def test_settings_app():
    """设置页(TAB): 页签切换 / KEY 弹窗编辑保存 (0600) / 卡片开关与超 3 提醒 / 主题应用."""
    import dash
    pygame.init()
    pygame.display.set_mode((1, 1))
    canvas = pygame.Surface((dash.W, dash.H))
    tmp = tempfile.mkdtemp(prefix="oneapp")
    dash.SETTINGS_FILE = os.path.join(tmp, "settings.json")
    app = dash.App(FakeNet())
    app.tool = "set"                          # 卡片页签 (默认)
    assert app.set_tab == 0
    import stat as stat_mod
    assert stat_mod.S_IMODE(os.stat(dash.SETTINGS_FILE).st_mode) == 0o600
    # 卡片开关 (USAGE 卡已移除, 4 张: sched/hitokoto/news/fin 缺省全开):
    # 4 张有效 → 关 fin (随意) → 再开 fin 被拒 (>3) → 关新闻 → fin 可开
    rows = app._set_rows()
    sw_fin = [i for i, r in enumerate(rows) if r[0] == "sw:fin"][0]
    app.set_sel = sw_fin
    app.key(pygame.K_RETURN)                  # ON → OFF 随意
    assert app.cfg["cards"].get("fin") is False
    assert app._card_on_n() == 3
    app.key(pygame.K_RETURN)                  # OFF → ON 到 4 → 被拒
    assert app.cfg["cards"].get("fin") is not True
    assert app.osd and "先关" in app.osd[0], app.osd
    app.cfg["cards"]["news"] = False          # 关新闻 → 有效 2 → fin 可开
    assert app._card_on_n() == 2
    app.key(pygame.K_RETURN)
    assert app.cfg["cards"].get("fin") is True
    assert app._card_on_n() == 3
    app.draw(canvas)                          # 设置页渲染冒烟 (KEY 掩码/开关)
    # 主题页签: ←→ 切到 主题 → 选 纯黑 (theme:6) → theme_idx 应用
    app.key(pygame.K_LEFT)                    # ← 左页签 (主题在左? 方向循环)
    app.key(pygame.K_RIGHT)
    app.set_tab = 2                           # 直接定位主题页签 (卡片/账户/主题)
    app.set_sel = 6                           # theme:6 = 纯黑
    app.key(pygame.K_RETURN)
    assert app.theme_idx == 6, app.theme_idx
    import json as json_mod
    assert json_mod.load(open(dash.THEME_FILE)).get("idx") == 6
    # 主页渲染冒烟 (USAGE 卡已移除, 右列只剩 提醒行程/每日一句/新闻/自选股)
    app.tool = None
    app.page = 0
    app.draw(canvas)
    print("settings app OK (tabs/key popup 0600/toggle/remind/theme apply)")



def test_cloud():
    """想法云同步: 设置行/云配置保存→ping/文字上云幂等/删除联动 cdel/
    tombstone 对账双向删/云端视图键位 (9 进 Enter 播 7 删 Esc 回)."""
    import dash
    pygame.init()
    pygame.display.set_mode((1, 1))
    canvas = pygame.Surface((dash.W, dash.H))
    tmp = tempfile.mkdtemp(prefix="oneapp")
    dash.SETTINGS_FILE = os.path.join(tmp, "settings.json")
    dash.IDEAS_FILE = os.path.join(tmp, "ideas.json")
    app = dash.App(FakeNet())
    app.set_tab = 1                           # 账户页签
    _arows = [r[0] for r in app._set_rows()]
    assert "key:cloud" not in _arows and "wxbind" in _arows
    app.set_tab = 0                           # 回卡片页签 (开关行)
    # ≤3 归一: 4 张全开 (含 sched 缺省 True) → 只保留前 3, 其余显式 False
    app.cfg["cards"].update(hitokoto=True, news=True, fin=True)
    app._card_norm()
    assert app._card_on_n() == 3
    assert [ck for ck in dash.CARD_KEYS if app._card_on(ck)] ==         list(dash.CARD_KEYS[:3])
    # 计数含缺省键: sched 缺省 True + 显式 2 张 = 有效 3 → 开 fin 被拒
    app.cfg["cards"] = {"hitokoto": True, "news": True,
                        "fin": False}
    assert app._card_on_n() == 3
    rows = app._set_rows()
    app.tool = "set"
    app.set_tab = 0
    app.set_sel = [i for i, r in enumerate(rows) if r[0] == "sw:fin"][0]
    app.key(pygame.K_RETURN)
    app.tool = None
    assert app._card_on("fin") is False        # 被拒, 保持关
    assert "先关" in (app.osd[0] if app.osd else "")
    # 云配置弹窗保存 → cfg 落盘 + ping 入队
    app.tool = "set"
    app.set_edit = "cloud"
    app.set_buf = "https://notes.apisz.top/"
    app.key(pygame.K_RETURN)
    assert app.cfg["cloud_url"] == "https://notes.apisz.top/"
    app.set_edit = "tok"
    app.set_buf = "a" * 24
    app.key(pygame.K_RETURN)
    assert app.cfg["cloud_tok"] == "a" * 24
    assert app.net.last_job("cloudping") is not None
    orig_raw = netmod.http_raw
    calls = []
    netmod.http_raw = lambda url, **k: (
        calls.append((k.get("method", "GET"), url))
        or (200, json.dumps({"ok": 1, "user": "zero"}).encode()))
    d = app.net.last_job("cloudping")()
    assert d["ok"] == 1 and d["user"] == "zero"
    app.on_net("data", "cloudping", d)
    assert app.osd and "云连接 ✓" in app.osd[0]
    # 文字想法提交 → cid 12hex + cloudsync job → POST 幂等键
    app.tool = None
    app.buf = "第一条云想法"
    app.submit_idea()
    it = app.ideas["texts"][-1]
    assert it.get("cid") and len(it["cid"]) == 12
    calls.clear()                          # ping 的 GET 出栈, 只看上传
    d = app.net.last_job("cloudsync")()
    assert d["up_ids"] == [it["cid"]] and not d["err"]
    assert any(m == "POST" and u.endswith("/v1/notes") for m, u in calls)
    app.on_net("data", "cloudsync", d)
    assert app.ideas["texts"][-1]["up"] == 1
    assert json.load(open(dash.IDEAS_FILE))["texts"][-1]["up"] == 1
    # 本地删除 (有 cid) → cdel 队列 → 同步 DELETE → 队列清空
    app.sel = 0
    app.delete_selected()
    assert app.ideas.get("cdel") == [it["cid"]]
    calls.clear()
    app._cloud_trig = 0.0
    app.cloud_sync_trigger()
    d = app.net.last_job("cloudsync")()
    assert d["del_done"] == [it["cid"]]
    assert any(m == "DELETE" and it["cid"] in u for m, u in calls)
    app.on_net("data", "cloudsync", d)
    assert app.ideas.get("cdel") == []
    # 节流不吞 pending: 节流窗口内新落的条目必须立即触发补传
    n_jobs = len(app.net.jobs)
    app._cloud_trig = _time.monotonic()        # 刚同步过 (窗口内)
    app.ideas["texts"].append({"id": "x-2", "cid": "d" * 12,
                               "text": "紧随其后", "createdAt": 2})
    app.cloud_sync_trigger()
    assert len(app.net.jobs) > n_jobs          # pending 豁免节流
    netmod.http_raw = lambda url, **k: (
        (200, json.dumps({"ok": 1}).encode()) if k.get("method") == "POST"
        else (200, json.dumps({"ids": [], "now": 5.0}).encode()))
    d = app.net.last_job("cloudsync")()
    assert d["up_ids"] == ["d" * 12]
    app.on_net("data", "cloudsync", d)
    assert app.ideas["texts"][-1]["up"] == 1
    n_jobs = len(app.net.jobs)
    app._cloud_trig = _time.monotonic()
    app.cloud_sync_trigger()                   # 无 pending → 节流生效
    assert len(app.net.jobs) == n_jobs
    netmod.http_raw = orig_raw
    # tombstone 对账: 云端删 → 本地条目+文件清, csync 持久化
    vp = os.path.join(tmp, "v1.wav")
    open(vp, "wb").write(b"RIFF....")
    app.ideas["voices"].append({"id": "v1", "cid": "c" * 12, "file": vp,
                                "dur": 3, "createdAt": 1, "up": 1})
    def raw_tomb(url, **k):
        if "/v1/tombstones" in url:
            return 200, json.dumps({"ids": ["c" * 12],
                                    "now": 12345.0}).encode()
        return 0, b""
    netmod.http_raw = raw_tomb
    app._cloud_trig = 0.0
    app.cloud_sync_trigger()
    d = app.net.last_job("cloudsync")()
    assert d["gone_tomb"] and d["csync"] == 12345.0
    app.on_net("data", "cloudsync", d)
    assert app.ideas["voices"] == [] and not os.path.exists(vp)
    assert json.load(open(dash.IDEAS_FILE))["csync"] == 12345.0
    # 云端视图: 9 进 (触发 sync+list) → 光标 → Enter 下载播 → 7 删云+本地孪生 → Esc 回
    netmod.http_raw = orig_raw
    app.on_net("data", "cloudlist", {"ok": 1, "notes": [
        {"id": "t" * 12, "kind": "text", "text": "云端文字",
         "ts": 1794700000.0},
        {"id": "c" * 12, "kind": "voice", "dur": 4, "ts": 1794700100.0}]})
    app.page = 1
    app.key(pygame.K_9)
    assert app.iview == "cloud"
    assert app.net.last_job("cloudlist") is not None
    app.draw(canvas)
    app.key(pygame.K_DOWN)
    assert app.csel == 1
    netmod.http_raw = lambda url, **k: (
        (200, b"RIFFWAVE") if "/v1/voice/" in url else (0, b""))
    app.key(pygame.K_RETURN)
    d = app.net.last_job("cloudvoice")()
    assert d["id"] == "c" * 12 and d["data"] == b"RIFFWAVE"
    app.on_net("data", "cloudvoice", d)
    assert os.path.exists("/tmp/oneapp_cloud_%s.wav" % ("c" * 12))
    vp2 = "/tmp/oneapp_cloud_%s.wav" % ("c" * 12)
    app.ideas["voices"].append({"id": "v2", "cid": "c" * 12, "file": vp2,
                                "dur": 4, "createdAt": 2, "up": 1})
    netmod.http_raw = lambda url, **k: (
        (200, b"{}") if k.get("method") == "DELETE" else (0, b""))
    app.key(pygame.K_7)
    d = app.net.last_job("clouddel")()
    assert d["id"] == "c" * 12 and d["st"] == 200
    app.on_net("data", "clouddel", d)
    assert [x["id"] for x in app.cloud_notes] == ["t" * 12]
    assert app.ideas["voices"] == []
    netmod.http_raw = orig_raw
    app.key(pygame.K_ESCAPE)
    assert app.iview == "local"
    # 云端全量 merge: 云端有本地无 → 镜像入 (owner=当前账户); 已传条目内容对齐
    app.cfg["cloud_user"] = "zero"
    def raw_merge(url, **k):
        if "/v1/tombstones" in url:
            return 200, json.dumps({"ids": [], "now": 6.0}).encode()
        if url.endswith("/v1/notes") or "/v1/notes?" in url:
            return 200, json.dumps({"notes": [
                {"id": "e" * 12, "kind": "text", "text": "他端新增",
                 "ts": 900.0, "category": "work"},
                {"id": "d" * 12, "kind": "text", "text": "紧随其后",
                 "ts": 800.0}]}).encode()
        return 200, b"{}"
    netmod.http_raw = raw_merge
    app._cloud_trig = 0.0
    app.cloud_sync_trigger()
    d = app.net.last_job("cloudsync")()
    assert len(d["cloud_list"]) == 2
    app.on_net("data", "cloudsync", d)
    texts = {t["cid"]: t for t in app.ideas["texts"]}
    assert texts["e" * 12]["text"] == "他端新增"
    assert texts["e" * 12]["owner"] == "zero" and texts["e" * 12]["up"] == 1
    assert texts["d" * 12]["text"] == "紧随其后"   # 已传条目内容向云端对齐
    netmod.http_raw = orig_raw
    print("cloud OK (settings/ping/upload idempotent/cdel/tombstone/"
          "cloud view keys/merge)")


def test_reg():
    """注册表单: 设置行进入/三字段输入 (name/invite 自动小写, 密码原样)/
    ↑↓ 换行/空值+密码长度提示/提交出码 (device/start reg 会话) → 关表单+
    QR 弹层 mode=reg / poll approved 存 token / poll failed → fail 态 /
    过期重出码走 qrlogin 存档 / 出码失败保表单 / Esc 关."""
    import dash
    import types
    pygame.init()
    pygame.display.set_mode((1, 1))
    canvas = pygame.Surface((dash.W, dash.H))
    tmp = tempfile.mkdtemp(prefix="oneapp")
    dash.SETTINGS_FILE = os.path.join(tmp, "settings.json")
    dash.IDEAS_FILE = os.path.join(tmp, "ideas.json")
    app = dash.App(FakeNet())
    app.set_tab = 1                           # 账户页签 (reg 行所在)
    assert [r[0] for r in app._set_rows()].index("reg") == 3
    # rid=reg 行 (sel=3, glm/ds 行移除后前移) → Enter 开表单
    app.tool = "set"
    assert app._set_rows()[3][0] == "reg"
    app.set_sel = 3
    app.key(pygame.K_RETURN)
    assert app.reg == {"name": "", "invite": "", "password": "", "field": 0}
    # F/X/Z/C 真实键值 = 字母 (丝印方向让位, _silk_input_active 含 reg 表单;
    # 若白名单缺失, k 会被换成 K_UP 且 ch 丢弃 → 断言炸)
    for kk, c2 in ((pygame.K_f, "f"), (pygame.K_x, "x"),
                   (pygame.K_z, "z"), (pygame.K_c, "c")):
        app.key(kk, c2)
    assert app.reg["name"] == "fxzc", app.reg
    for _ in range(4):
        app.key(pygame.K_BACKSPACE)
    assert app.reg["name"] == ""
    # 输入: name 自动小写; ↑↓ 换行; invite 自动小写; password 原样大小写
    for c in "AbC_01":
        app.key(0, c)
    assert app.reg["name"] == "abc_01"
    app.key(pygame.K_UP)
    for c in "DEADbeef01":
        app.key(0, c)
    assert app.reg["invite"] == "deadbeef01"
    app.key(pygame.K_UP)                   # 第 3 行: 密码
    for c in "Pw12":
        app.key(0, c)
    assert app.reg["password"] == "Pw12"   # 密码不强制小写
    app.draw(canvas)                       # 表单渲染冒烟
    # 空用户名 → Enter 提示都要填 (先到先查)
    app.key(pygame.K_UP)                   # 2 → 0 (name)
    while app.reg["name"]:
        app.key(pygame.K_BACKSPACE)
    app.key(pygame.K_RETURN)
    assert app.osd and "都要填" in app.osd[0]
    for c in "AbC_01":
        app.key(0, c)
    assert app.reg["name"] == "abc_01"
    # 密码过短提示 (name/invite 已填, 轮到密码检查)
    app.key(pygame.K_RETURN)
    assert app.osd and "6-64" in app.osd[0]
    app.key(pygame.K_UP)                   # 0 → 1? (前进循环) → 补密码
    while app.reg["field"] != 2:
        app.key(pygame.K_UP)
    while app.reg["password"]:
        app.key(pygame.K_BACKSPACE)
    app.key(pygame.K_RETURN)               # 密码空 → 必填拦下 (用户定版:
    assert app.osd and "6-64" in app.osd[0]    #  注册密码不能空, 密码登录兜底)
    for c in "Pw123456":
        app.key(0, c)
    assert app.reg["password"] == "Pw123456"
    # 提交: device/start 桩 200 → 关表单 + QR 弹层 mode=reg
    app.qr_poll_at = 0.0
    captured = {}
    orig_raw = netmod.http_raw
    fake_segno = types.SimpleNamespace(make=lambda u, error="m":
                                       types.SimpleNamespace(
                                           matrix=[[True, False],
                                                   [False, True]]))
    sys.modules["segno"] = fake_segno      # Mac 无 segno (设备真机已装)
    try:
        def raw_start(url, **k):
            captured["url"] = url
            captured["body"] = json.loads(k["body"].decode())
            return 200, json.dumps(
                {"ok": 1, "device_code": "d" * 32,
                 "url": "https://notes.apisz.top/login#dc=" + "d" * 32,
                 "exp": 0}).encode()
        netmod.http_raw = raw_start
        app.key(pygame.K_RETURN)
        d = app.net.last_job("cloudregqr")()
        assert captured["url"].endswith("/v1/auth/device/start")
        assert captured["body"] == {"name": "zero",
                                    "reg": {"name": "abc_01",
                                            "invite": "deadbeef01",
                                            "password": "Pw123456"}}
        assert d["ok"] == 1 and d["dc"] == "d" * 32 and d["mat"]
        app.on_net("data", "cloudregqr", d)
        assert app.reg is None             # 表单关, 出码弹层接管
        assert app.qrlogin["st"] == "qr" and app.qrlogin["mode"] == "reg"
        assert app.qrlogin["reg"]["name"] == "abc_01"   # 存档供重出码
        app.draw(canvas)                   # QR 弹层渲染冒烟 (reg 标题)
        # poll approved → 存 token + user + csync + reg 版 OSD
        def raw_ok(url, **k):
            if "/v1/auth/device/poll" in url:
                return 200, json.dumps({"status": "approved",
                                        "token": "q" * 48,
                                        "user": "abc_01"}).encode()
            if "/v1/tombstones" in url:
                return 200, json.dumps({"ids": [], "now": 888.0}).encode()
            return 404, b"{}"
        netmod.http_raw = raw_ok
        app._cloud_qr_tick()
        d = app.net.last_job("cloudqrpoll")()
        assert d["st"] == "approved" and d["token"] == "q" * 48
        app.on_net("data", "cloudqrpoll", d)
        assert app.qrlogin is None and app.cfg["cloud_tok"] == "q" * 48
        assert app.cfg["cloud_user"] == "abc_01"
        assert app.net.last_job("cloudping") is not None
        assert app.osd and "注册成功 已绑定微信" in app.osd[0]
        json.load(open(dash.SETTINGS_FILE))
        # poll failed (扫码期重名) → fail 态 + 原因, 不再轮询
        app.qrlogin = {"st": "qr", "dc": "e" * 32, "url": "u",
                       "mat": [[1]], "mode": "reg",
                       "reg": {"name": "abc_01", "invite": "xx"}}
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"status": "failed", "err": "用户名已存在"}).encode())
        app.qr_poll_at = 0.0
        app._cloud_qr_tick()
        d = app.net.last_job("cloudqrpoll")()
        assert d["st"] == "failed" and d.get("err") == "用户名已存在"
        app.on_net("data", "cloudqrpoll", d)
        assert app.qrlogin["st"] == "fail" and "用户名已存在" \
            in app.qrlogin["err"]
        app.draw(canvas)
        # 过期 (none) → reg 走存档重出码 (cloudregqr, 非 cloudqrstart)
        app.qrlogin = {"st": "qr", "dc": "e" * 32, "url": "u",
                       "mat": [[1]], "mode": "reg",
                       "reg": {"name": "abc_01", "invite": "deadbeef01"}}
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"status": "none"}).encode())
        app.qr_poll_at = 0.0
        app._cloud_qr_tick()
        d = app.net.last_job("cloudqrpoll")()
        assert d["st"] == "none"
        app.on_net("data", "cloudqrpoll", d)
        assert app.qrlogin["st"] == "init"
        assert app.net.last_job("cloudregqr") is not None
        netmod.http_raw = raw_start
        d = app.net.last_job("cloudregqr")()   # 从 qrlogin 存档取资料
        assert d["ok"] == 1 and captured["body"]["reg"]["invite"] \
            == "deadbeef01"
        app.on_net("data", "cloudregqr", d)
        assert app.qrlogin["st"] == "qr" and app.qrlogin["dc"] == "d" * 32
        app.key(pygame.K_ESCAPE)           # Esc 关弹层
        assert app.qrlogin is None
    finally:
        netmod.http_raw = orig_raw
        sys.modules.pop("segno", None)
    # 出码失败 (409 重名) → 表单保留 + OSD (弹层未开)
    orig_raw = netmod.http_raw
    try:
        netmod.http_raw = lambda url, **k: (409, json.dumps(
            {"err": "用户名已存在"}).encode())
        app.reg = {"name": "bob", "invite": "xx", "password": "", "field": 0}
        app.key(pygame.K_RETURN)
        d = app.net.last_job("cloudregqr")()
        assert d["ok"] == 0 and d["err"] == "用户名已存在"
        app.on_net("data", "cloudregqr", d)
        assert app.reg is not None and app.qrlogin is None
        assert app.osd and "注册失败" in app.osd[0]
        app.on_net("error", "cloudregqr", None)    # 网络异常事件
        assert app.osd and "网络异常" in app.osd[0]
        app.key(pygame.K_ESCAPE)           # Esc 关表单, 留在设置页
        assert app.reg is None and app.tool == "set"
    finally:
        netmod.http_raw = orig_raw
    print("reg OK (form/fields/pw-raw/submit-qr/qr-overlay/approved/"
          "failed/expire-rearchive/fail-retain/esc)")


def test_login():
    """登录表单 (两字段, TOKEN 登录已去 — 只走密码/扫码): 行进入/
    用户名小写/密码 64 上限/空值提示/密码登录换会话 token 存 tok+user
    关表单 / 401 服务端 err 透传保表单 / Esc 关 / 换号 csync 重置 /
    登录行右侧状态."""
    import dash
    pygame.init()
    pygame.display.set_mode((1, 1))
    canvas = pygame.Surface((dash.W, dash.H))
    tmp = tempfile.mkdtemp(prefix="oneapp")
    dash.SETTINGS_FILE = os.path.join(tmp, "settings.json")
    app = dash.App(FakeNet())
    app.set_tab = 1                           # 账户页签 (登录/扫码/绑定/注册/退出)
    rows = [r[0] for r in app._set_rows()]
    assert rows.index("login") == 0 and rows.index("qrlogin") == 1 \
        and rows.index("wxbind") == 2 and rows.index("reg") == 3
    assert rows[0] == "login" and "key:cloud" not in rows
    assert ("qrlogin", "扫码登录") in app._set_rows()   # 免输token 标签随 TOKEN 去
    app.tool = "set"
    app.draw(canvas)                       # 未登录: login 行右侧 "未登录"
    app.set_sel = rows.index("login")
    app.key(pygame.K_RETURN)
    assert app.login == {"user": "", "pw": "", "field": 0}
    # FXZC 真实键值 = 字母 (白名单含 login 表单) → 落用户名字段且小写
    for kk, c2 in ((pygame.K_f, "f"), (pygame.K_z, "z"),
                   (pygame.K_c, "C")):
        app.key(kk, c2)
    assert app.login["user"] == "fzc"
    app.key(pygame.K_BACKSPACE)
    app.key(pygame.K_BACKSPACE)
    app.key(pygame.K_BACKSPACE)
    app.key(pygame.K_RETURN)               # 全空 → 提示
    assert app.osd and "都要填" in app.osd[0]
    # 字段上限: 用户名 16 / 密码 64 (会话 token 48 hex 落 cloud_tok,
    # 不再手输 — 无 TOKEN 字段)
    for c in "u" * 20:
        app.key(0, c)
    assert len(app.login["user"]) == 16
    app.login["user"] = ""
    app.key(pygame.K_DOWN)
    for c in "p" * 70:
        app.key(0, c)
    assert len(app.login["pw"]) == 64
    app.draw(canvas)                       # 表单渲染冒烟
    # 密码登录: 用户名+密码 → POST /v1/auth/login 换会话 token → 落盘
    app.login = {"user": "lee", "pw": "secret1", "field": 1}
    orig_raw = netmod.http_raw
    def raw_pw(url, **k):
        if "/v1/auth/login" in url:
            body = json.loads(k["body"])
            assert body["username"] == "lee" and body["password"] == "secret1"
            return 200, json.dumps({"ok": 1, "user": "lee",
                                    "token": "s" * 48}).encode()
        if "/v1/tombstones" in url:
            return 200, json.dumps({"ids": [], "now": 555.0}).encode()
        return 500, b"{}"
    netmod.http_raw = raw_pw
    app.key(pygame.K_RETURN)
    d = app.net.last_job("cloudlogin")()
    assert d["ok"] == 1 and d["user"] == "lee" and d["tok"] == "s" * 48
    assert d["now"] == 555.0
    app.cfg["kv_owner"] = "staleold"           # 残留旧号 (真机 lilihome 同款)
    app.on_net("data", "cloudlogin", d)
    assert app.login is None and app.cfg["cloud_tok"] == "s" * 48
    assert app.cfg["cloud_user"] == "lee"
    assert app.cfg["kv_owner"] == "lee"        # 登录即归属落位 (不再反复补对齐)
    assert app.osd and "已登录" in app.osd[0]
    assert app.net.last_job("cloudping") is not None
    assert json.load(open(dash.SETTINGS_FILE))["cloud_user"] == "lee"
    # 失败: 401 → 服务端 err 透传 + 表单保留
    app.login = {"user": "lee", "pw": "bad", "field": 1}
    netmod.http_raw = lambda url, **k: (401, json.dumps(
        {"err": "账号或密码不对"}).encode())
    app.key(pygame.K_RETURN)
    d = app.net.last_job("cloudlogin")()
    assert d["ok"] == 0 and "账号或密码不对" in d["err"]
    app.on_net("data", "cloudlogin", d)
    assert app.login == {"user": "lee", "pw": "bad", "field": 1}
    assert app.osd and "登录失败" in app.osd[0]
    # 只有用户名没密码 → 提示不发请求
    app.login = {"user": "lee", "pw": "", "field": 1}
    njobs = len(app.net.jobs)
    app.key(pygame.K_RETURN)
    assert app.osd and "都要填" in app.osd[0]
    assert len(app.net.jobs) == njobs
    # ping 回填用户名 (cloud_user 变更才落盘)
    app.on_net("data", "cloudping", {"ok": 1, "user": "zero2"})
    assert app.cfg["cloud_user"] == "zero2"
    netmod.http_raw = orig_raw
    app.key(pygame.K_ESCAPE)
    assert app.login is None and app.tool == "set"
    # 换账户: 已登录 → login 行标签变"更换账户"; 密码换号 csync 重置
    assert ("login", "更换账户") in app._set_rows()
    app.ideas = {"texts": [], "voices": [], "csync": 1}
    def raw_switch(url, **k):
        if "/v1/auth/login" in url:
            return 200, json.dumps({"ok": 1, "user": "other",
                                    "token": "n" * 48}).encode()
        if "/v1/tombstones" in url:
            return 200, json.dumps({"ids": [], "now": 777.0}).encode()
        return 500, b"{}"
    netmod.http_raw = raw_switch
    app.login = {"user": "other", "pw": "pw2", "field": 0}
    app.key(pygame.K_RETURN)
    d = app.net.last_job("cloudlogin")()
    assert d["ok"] == 1 and d["now"] == 777.0
    app.on_net("data", "cloudlogin", d)
    assert app.cfg["cloud_user"] == "other"
    assert app.ideas["csync"] == 777.0    # 换号 → csync 重置到新号钟
    assert json.load(open(dash.IDEAS_FILE))["csync"] == 777.0
    netmod.http_raw = orig_raw
    # 绑定微信 (已登录): Enter → job → 出码弹层 mode=wxbind → poll bound
    import types as types_mod
    sys.modules["segno"] = types_mod.SimpleNamespace(
        make=lambda u, error="m": types_mod.SimpleNamespace(
            matrix=[[True, False], [False, True]]))
    orig_raw = netmod.http_raw
    try:
        idx = [r[0] for r in app._set_rows()].index("wxbind")
        app.set_sel = idx
        app.key(pygame.K_RETURN)
        assert app.net.last_job("cloudwxbind") is not None
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"ok": 1, "device_code": "b" * 32,
             "url": "https://notes.apisz.top/login#dc=" + "b" * 32,
             "exp": 0}).encode())
        d = app.net.last_job("cloudwxbind")()
        assert d["ok"] == 1 and d["dc"] == "b" * 32
        app.on_net("data", "cloudwxbind", d)
        assert app.qrlogin["st"] == "qr" and app.qrlogin["mode"] == "wxbind"
        app.draw(canvas)                   # wxbind 弹层渲染冒烟
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"status": "bound"}).encode())
        app.qr_poll_at = 0.0
        app._cloud_qr_tick()
        d = app.net.last_job("cloudqrpoll")()
        assert d["st"] == "bound"
        app.on_net("data", "cloudqrpoll", d)
        assert app.qrlogin is None
        assert app.osd and "微信已绑定" in app.osd[0]
        # 过期重出码: none → wxbind job 重入队
        app.qrlogin = {"st": "qr", "dc": "b" * 32, "url": "u",
                       "mat": [[1]], "mode": "wxbind"}
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"status": "none"}).encode())
        app.qr_poll_at = 0.0
        app._cloud_qr_tick()
        d = app.net.last_job("cloudqrpoll")()
        app.on_net("data", "cloudqrpoll", d)
        assert app.qrlogin["st"] == "init"
        assert app.net.last_job("cloudwxbind") is not None
        app.qrlogin = None                 # 清态防吞后续按键
    finally:
        netmod.http_raw = orig_raw
        sys.modules.pop("segno", None)
    # 退出登录: logout 行 → cfg 清空 + OSD; 未登录再按 → 提示; 标签复原
    assert ("login", "登录") not in app._set_rows()
    idx = [r[0] for r in app._set_rows()].index("logout")
    app.set_sel = idx
    app.key(pygame.K_RETURN)
    assert app.cfg.get("cloud_tok") == "" and app.cfg.get("cloud_user") == ""
    assert app.osd and "已退出" in app.osd[0]
    assert ("login", "登录") in app._set_rows()
    app.key(pygame.K_RETURN)               # 未登录再按 logout
    assert app.osd and "未登录" in app.osd[0]
    # 绑定微信 (未登录) → 提示先登录
    idx = [r[0] for r in app._set_rows()].index("wxbind")
    app.set_sel = idx
    app.key(pygame.K_RETURN)
    assert app.osd and "先登录" in app.osd[0]
    # (管理员TOKEN/生成注册码行已移除 — 管理操作下放 web 管理后台 /admin)
    print("login OK (form/fxzc/ping verify/persist/fail-retain/esc/"
          "switch+csync reset/logout)")


def test_qrlogin():
    """扫码登录: 设置行进入/出码(start)/pending 轮询节流/approved 存 token
    +csync 重置/过期自动重出码/Esc 关/网络异常不断轮询/绘制冒烟."""
    import dash
    import types
    pygame.init()
    pygame.display.set_mode((1, 1))
    canvas = pygame.Surface((dash.W, dash.H))
    tmp = tempfile.mkdtemp(prefix="oneapp")
    dash.SETTINGS_FILE = os.path.join(tmp, "settings.json")
    dash.IDEAS_FILE = os.path.join(tmp, "ideas.json")
    app = dash.App(FakeNet())
    app.set_tab = 1                        # 账户页签 (扫码登录行所在)
    rows = [r[0] for r in app._set_rows()]
    assert rows.index("qrlogin") == 1      # 与 test_login 一致
    app.tool = "set"
    # segno 桩 (Mac 无 segno; 设备真机已装)
    fake_segno = types.SimpleNamespace(make=lambda u, error="m":
                                       types.SimpleNamespace(
                                           matrix=[[True, False],
                                                   [False, True]]))
    sys.modules["segno"] = fake_segno
    orig_raw = netmod.http_raw
    try:
        app.set_sel = rows.index("qrlogin")
        app.key(pygame.K_RETURN)
        assert app.qrlogin is not None and app.qrlogin["st"] == "init"
        assert app.net.last_job("cloudqrstart") is not None
        # Esc 在出码中即可关
        app.key(pygame.K_ESCAPE)
        assert app.qrlogin is None and app.tool == "set"
        # 重进: start 出码 → st=qr + mat
        app.key(pygame.K_RETURN)
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"ok": 1, "device_code": "d" * 32,
             "url": "https://notes.apisz.top/login#dc=" + "d" * 32,
             "exp": 0}).encode())
        d = app.net.last_job("cloudqrstart")()
        assert d["ok"] == 1 and d["dc"] == "d" * 32 and d["mat"]
        app.on_net("data", "cloudqrstart", d)
        assert app.qrlogin["st"] == "qr" and app.qrlogin["mat"] is not None
        app.draw(canvas)                   # QR 渲染冒烟
        # tick 节流: 首帧入队 poll, 2s 内不再入队
        app.qr_poll_at = 0.0
        app._cloud_qr_tick()
        assert app.net.last_job("cloudqrpoll") is not None
        n_jobs = len(app.net.jobs)
        app._cloud_qr_tick()
        assert len(app.net.jobs) == n_jobs
        # pending → wait; 网络异常 → 下帧可重试
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"status": "pending"}).encode())
        d = app.net.last_job("cloudqrpoll")()
        assert d["st"] == "pending"
        app.on_net("data", "cloudqrpoll", d)
        assert app.qrlogin["st"] == "wait"
        app.on_net("error", "cloudqrpoll", None)
        assert app.qr_poll_at == 0.0
        # approved → 存 token + user + csync 重置 + 关扫码页 + ping
        def raw_ok(url, **k):
            if "/v1/auth/device/poll" in url:
                return 200, json.dumps({"status": "approved",
                                        "token": "q" * 48,
                                        "user": "zero"}).encode()
            if "/v1/tombstones" in url:
                return 200, json.dumps({"ids": [], "now": 888.0}).encode()
            return 404, b"{}"
        netmod.http_raw = raw_ok
        app._cloud_qr_tick()
        d = app.net.last_job("cloudqrpoll")()
        assert d["st"] == "approved" and d["token"] == "q" * 48 \
            and d["now"] == 888.0
        app.on_net("data", "cloudqrpoll", d)
        assert app.qrlogin is None
        assert app.cfg["cloud_tok"] == "q" * 48
        assert app.cfg["cloud_user"] == "zero"
        assert app.ideas["csync"] == 888.0
        assert app.net.last_job("cloudping") is not None
        assert app.osd and "扫码登录" in app.osd[0]
        json.load(open(dash.SETTINGS_FILE))
        # 过期 (none) → 自动重出码 (新 qrlogin 态 + start 重入队)
        app._cloud_qr_open()
        app.on_net("data", "cloudqrstart",
                   {"ok": 1, "dc": "e" * 32, "url": "u", "mat": [[1]]})
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"status": "none"}).encode())
        app.qr_poll_at = 0.0
        app._cloud_qr_tick()
        d = app.net.last_job("cloudqrpoll")()
        assert d["st"] == "none"
        app.on_net("data", "cloudqrpoll", d)
        assert app.qrlogin is not None and app.qrlogin["st"] == "init"
        assert app.net.last_job("cloudqrstart") is not None
        # 出码失败 (网络) → fail 态 + 提示, 不再轮询
        app.on_net("error", "cloudqrstart", None)
        assert app.qrlogin["st"] == "fail"
        app.draw(canvas)
        app.key(pygame.K_q)
        assert app.qrlogin is None
    finally:
        netmod.http_raw = orig_raw
        sys.modules.pop("segno", None)
    print("qrlogin OK (row/esc-during-init/start-matrix/poll-throttle/"
          "pending/approved-persist/expire-restart/netfail/esc)")


def test_kv_sync():
    """Oneapp 账户 kv 云同步: 开机拉取/变更即推/LWW (mtime 判新旧)/
    登录对齐确认框 (Enter 上传/Esc 云端覆盖)/模态吞键/队列顺序."""
    import dash
    import news_rss
    pygame.init()
    pygame.display.set_mode((1, 1))
    canvas = pygame.Surface((dash.W, dash.H))
    tmp = tempfile.mkdtemp(prefix="oneapp")
    dash.SETTINGS_FILE = os.path.join(tmp, "settings.json")
    fr = os.path.join(tmp, "radio_fav.json")
    fw = os.path.join(tmp, "watch.json")
    ftv = os.path.join(tmp, "tv_fav.json")
    fnf = os.path.join(tmp, "news_feeds.json")
    dash.KV_FILES.update({"radio_fav": fr, "watch": fw, "tv_fav": ftv,
                          "news_feeds": fnf})
    dash.RADIO_FAV_CACHE = fr
    dash.WATCH_FILE = fw
    dash.TV_FAV_CACHE = ftv
    dash.NEWS_FEEDS_FILE = fnf
    json.dump({"cloud_tok": "t" * 24, "cloud_user": "u"},
              open(dash.SETTINGS_FILE, "w"))
    orig_raw = netmod.http_raw
    now = _time.time()
    try:
        # 开机: 已登录 → kvsync 全服务拉取入队 (news_feeds 首次缺失 → 种子落盘)
        app = dash.App(FakeNet())
        assert len(app.news_feeds) == 10 and json.load(open(fnf))
        tags = [t for t, _ in app.net.jobs if t.startswith("kvsync:")]
        assert tags == ["kvsync:radio_fav", "kvsync:watch",
                        "kvsync:tv_fav", "kvsync:news_feeds"], tags
        # LWW: 云端明显新 → 覆盖本地 (文件+内存)
        json.dump(["old1"], open(fr, "w"))
        os.utime(fr, (now - 100, now - 100))
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"ok": 1, "k": "radio_fav", "v": ["cloud1", "cloud2"],
             "updated": now}).encode())
        d = app.net.last_job("kvsync:radio_fav")()
        assert d["ok"] == 1 and d["v"] == ["cloud1", "cloud2"]
        app.on_net("data", "kvsync:radio_fav", d)
        assert app.radio_fav == ["cloud1", "cloud2"]
        assert json.load(open(fr)) == ["cloud1", "cloud2"]
        # LWW: 本地明显新 → 补推 (kvput 入队, PUT 200)
        netmod.http_raw = lambda url, **k: (
            (200, json.dumps({"ok": 1}).encode()) if url.endswith("/radio_fav")
            and k.get("method") == "PUT"
            else (404, b'{"err": "no such key"}'))
        app.radio_fav = ["mine"]
        app.radio_fav_save()               # 变更即推 (kvput 入队)
        d = app.net.last_job("kvput:radio_fav")()
        assert d["ok"] == 1 and d["st"] == 200
        # 登录对齐: 本地有 → 确认框; 模态吞键
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"ok": 1, "v": ["cloud1", "cloud2", "cloud3"],
             "updated": now}).encode())
        app.kv_align_trigger()
        d = app.net.last_job("kvalign:radio_fav")()
        assert d["ok"] == 1
        app.on_net("data", "kvalign:radio_fav", d)
        assert app.kv_confirm and app.kv_confirm["k"] == "radio_fav" \
            and app.kv_confirm["n"] == 1 and app.kv_confirm["cn"] == 3
        app.set_key(pygame.K_5, "5")       # 非裁决键 → 吞掉
        assert app.kv_confirm is not None
        app.draw(canvas)                   # 确认框渲染冒烟
        # Enter → 上传本地 (kvput 入队) + 队列走到 watch
        netmod.http_raw = lambda url, **k: (200, json.dumps({"ok": 1}).encode())
        app.set_key(pygame.K_RETURN, "")
        assert app.kv_confirm is None
        assert app.net.last_job("kvput:radio_fav") is not None
        assert app.kv_queue == ["watch", "tv_fav", "news_feeds"]
        # watch: Esc → 云端覆盖本地 (apply 不回推)
        json.dump({"items": [{"code": "sz000001", "name": "平安银行"}]},
                  open(fw, "w"))              # 本地有记录 → 该出确认框
        d = app.net.last_job("kvalign:watch")()
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"ok": 1, "v": {"items": [{"code": "sh600519", "name": "贵州茅台"}]},
             "updated": now}).encode())
        d = app.net.last_job("kvalign:watch")()
        app.on_net("data", "kvalign:watch", d)
        assert app.kv_confirm and app.kv_confirm["k"] == "watch"
        n_puts = len([t for t, _ in app.net.jobs if t.startswith("kvput:watch")])
        app.set_key(pygame.K_ESCAPE, "")
        assert app.kv_confirm is None
        assert app.cfg["kv_owner"] == "u"      # 裁决即归属落位
        assert app.watch["items"][0]["code"] == "sh600519"
        assert json.load(open(fw))["items"][0]["code"] == "sh600519"
        assert len([t for t, _ in app.net.jobs
                    if t.startswith("kvput:watch")]) == n_puts  # 不回推
        assert app.kv_queue == ["tv_fav", "news_feeds"]
        # tv_fav: 云端没存过 (404) → 跳过走完
        netmod.http_raw = lambda url, **k: (404, json.dumps(
            {"err": "no such key"}).encode())
        d = app.net.last_job("kvalign:tv_fav")()
        app.on_net("data", "kvalign:tv_fav", d)
        assert app.kv_queue == ["news_feeds"]
        # news_feeds: 404 → 跳过 (本地种子在, 首推交给开机 kvsync)
        d = app.net.last_job("kvalign:news_feeds")()
        app.on_net("data", "kvalign:news_feeds", d)
        assert app.kv_queue == []          # 队列走完
        # 一致免确认 (修: 旧码凡本地有记录就弹 — 裁决完/内容相同时每次
        # 登录原样再弹, 4 服务连问): 归一后 == 云端 → 静默过不弹框
        json.dump(["tv1", "tv2"], open(ftv, "w"))
        app.tv_fav = ["tv1", "tv2"]            # 内存=文件 (真实态一致)
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"ok": 1, "v": ["tv1", "tv2"], "updated": now}).encode())
        app.kv_queue = ["tv_fav"]
        d = app.net.last_job("kvalign:tv_fav")()
        app.on_net("data", "kvalign:tv_fav", d)
        assert app.kv_confirm is None and app.tv_fav == ["tv1", "tv2"]
        assert app.kv_queue == []              # 静默走完, 本地不动
        # news_feeds: 云端旧表缺新内置源 → 归一补齐后 == 本地 → 免确认
        json.dump([{"id": "ai", "name": "AI", "url": "https://x/ai",
                    "on": True}], open(fnf, "w"))
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"ok": 1, "v": [{"id": "ai", "name": "AI",
                             "url": "https://x/ai", "on": True}],
             "updated": now}).encode())
        app.kv_queue = ["news_feeds"]
        d = app.net.last_job("kvalign:news_feeds")()
        app.on_net("data", "kvalign:news_feeds", d)
        assert app.kv_confirm is None and app.kv_queue == []
        # 真不一致仍弹 → Esc 云端覆盖 (回归旧路径)
        json.dump(["tv1"], open(ftv, "w"))
        app.tv_fav = ["tv1"]
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"ok": 1, "v": ["tv2"], "updated": now}).encode())
        app.kv_queue = ["tv_fav"]
        d = app.net.last_job("kvalign:tv_fav")()
        app.on_net("data", "kvalign:tv_fav", d)
        assert app.kv_confirm and app.kv_confirm["k"] == "tv_fav"
        app.set_key(pygame.K_ESCAPE, "")
        assert app.tv_fav == ["tv2"] and app.kv_queue == []
        # Esc 全局路由修: kv_confirm 模态优先于关工具 — 全 key() 入口按
        # Esc 必裁决弹框 (原: 工具态 Esc 直接 tool_close → 弹框永远无法
        # 关闭, 重进设置又见; 用户实测 '按 ESC 返回工具的页面')
        app.tool = "set"
        json.dump(["tvX"], open(ftv, "w"))
        app.tv_fav = ["tvX"]
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"ok": 1, "v": ["tvY"], "updated": now}).encode())
        app.kv_queue = ["tv_fav"]
        d = app.net.last_job("kvalign:tv_fav")()
        app.on_net("data", "kvalign:tv_fav", d)
        assert app.kv_confirm and app.kv_confirm["k"] == "tv_fav"
        app.key(pygame.K_ESCAPE)               # 全局 Esc → 裁决而非关工具
        assert app.kv_confirm is None and app.tv_fav == ["tvY"]
        assert app.tool == "set"               # 保持在设置页 (未跳工具网格)
        assert app.cfg["kv_owner"] == "u"
        app.tool = None
        # 网络失败 (st=0): 不裁决不首推 (开机 WiFi 未就绪时, 旧码会把陈旧
        # 本地推上去盖掉云端别的端的新收藏); None 结果不落缓存 → 可重试
        netmod.http_raw = lambda url, **k: (0, b"")
        assert app.net.last_job("kvsync:radio_fav")() is None
        n_puts = len([t for t, _ in app.net.jobs if t.startswith("kvput:")])
        app.on_net("error", "kvsync:radio_fav", None)   # worker 对 None 真实投递
        assert len([t for t, _ in app.net.jobs
                    if t.startswith("kvput:")]) == n_puts
        # 404 (云端真没存过) + 本地非空 → 仍首推 (kv 首推缺口修复不回退)
        netmod.http_raw = lambda url, **k: (404, b'{"err": "no such key"}')
        app.on_net("data", "kvsync:watch",
                   app.net.last_job("kvsync:watch")())
        assert app.net.last_job("kvput:watch") is not None
        # ── 换号隔离 (2026-11-21): 对齐静默跟随新号, 404/失败清空, 零回推
        app.cfg["cloud_user"] = "bob"
        app.cfg["kv_owner"] = "alice"          # 文件还归 A 号 (对齐未跑)
        app.kv_switch = True
        app.kv_queue = ["radio_fav", "watch", "tv_fav"]
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"ok": 1, "v": ["bcloud1"], "updated": now + 300}).encode())
        app.on_net("data", "kvalign:radio_fav",
                   app.net.last_job("kvalign:radio_fav")())
        assert app.kv_confirm is None and app.radio_fav == ["bcloud1"]
        assert app.cfg["kv_owner"] == "bob"    # 落位 → 归新号
        netmod.http_raw = lambda url, **k: (404, b'{"err": "no such key"}')
        app.on_net("data", "kvalign:watch",
                   app.net.last_job("kvalign:watch")())
        assert app.watch == {"items": []}      # 404 → 清空 (旧数据归旧号)
        app.on_net("error", "kvalign:tv_fav", None)
        assert app.tv_fav == []                # 网络失败也清
        assert app.kv_switch is False          # 队列排空复位
        # 残留保护: 归属≠当前号 (对齐没跑完/重启窗口) → 404/补推都不克隆
        app.cfg["kv_owner"] = "alice"
        n_puts = len([t for t, _ in app.net.jobs if t.startswith("kvput:")])
        json.dump(["staleA"], open(fr, "w"))
        os.utime(fr, (now + 400, now + 400))
        netmod.http_raw = lambda url, **k: (404, b'{"err": "no such key"}')
        app.on_net("data", "kvsync:radio_fav",
                   app.net.last_job("kvsync:radio_fav")())
        assert len([t for t, _ in app.net.jobs
                    if t.startswith("kvput:")]) == n_puts    # 404 不首推
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"ok": 1, "v": ["bobradio"], "updated": now}).encode())
        app.on_net("data", "kvsync:radio_fav",
                   app.net.last_job("kvsync:radio_fav")())
        assert app.radio_fav == ["bobradio"]   # 本地"更新"也不补推, 跟随云端
        # 重启窗口: 归属≠当前号 → 开机补对齐 (kvalign 而非 kvsync)
        json.dump({"cloud_tok": "t" * 24, "cloud_user": "bob",
                   "kv_owner": "alice"}, open(dash.SETTINGS_FILE, "w"))
        app2 = dash.App(FakeNet())
        assert [t for t, _ in app2.net.jobs
                if t.startswith("kvalign:")] == ["kvalign:radio_fav"]
        assert not [t for t, _ in app2.net.jobs
                    if t.startswith("kvsync:")]   # 补对齐而非 LWW 拉取
        # 本地空列表 mtime 较新 + 云端非空 → 不补推 (陈旧空列表盖云端 =
        # 2026-11-20 收藏清零事故), 跟随云端救回
        json.dump([], open(fr, "w"))
        os.utime(fr, (now + 200, now + 200))    # 本地明显"更新"
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"ok": 1, "v": ["cloud1", "cloud2"], "updated": now}).encode())
        n_puts = len([t for t, _ in app.net.jobs if t.startswith("kvput:")])
        app.on_net("data", "kvsync:radio_fav",
                   app.net.last_job("kvsync:radio_fav")())
        assert len([t for t, _ in app.net.jobs
                    if t.startswith("kvput:")]) == n_puts    # 没有补推
        assert app.radio_fav == ["cloud1", "cloud2"]         # 跟随云端
        # watch 同规则 (字典类): 本地 items 空 + 云端非空 → 跟随云端
        json.dump({"items": []}, open(fw, "w"))
        os.utime(fw, (now + 200, now + 200))
        netmod.http_raw = lambda url, **k: (200, json.dumps(
            {"ok": 1, "v": {"items": [{"code": "sh600519"}]},
             "updated": now}).encode())
        n_puts = len([t for t, _ in app.net.jobs if t.startswith("kvput:")])
        app.on_net("data", "kvsync:watch",
                   app.net.last_job("kvsync:watch")())
        assert len([t for t, _ in app.net.jobs
                    if t.startswith("kvput:")]) == n_puts
        assert app.watch["items"][0]["code"] == "sh600519"
        assert dash.App._kv_nonempty([]) is False
        assert dash.App._kv_nonempty(["x"]) is True
        assert dash.App._kv_nonempty({"items": []}) is False
        assert dash.App._kv_nonempty({"items": [{"code": "x"}]}) is True
        assert dash.App._kv_nonempty([{"id": "ai"}]) is True   # 新闻源表 (dict 列表)
        # news_feeds: kv_apply 云端覆盖 → 内存+文件+生效源表, 不回推;
        # 云端空 → 内置种子兜底 (不允许零源, 清零事故同款哲学)
        n_puts = len([t for t, _ in app.net.jobs if t.startswith("kvput:")])
        app.kv_apply("news_feeds", [
            {"id": "ai", "name": "AI 新闻", "url": "https://x/qbitai", "on": False},
            {"id": "c_1234", "name": "例站", "url": "https://example.com/f",
             "on": True}])
        assert [f["id"] for f in app.news_feeds][:2] == ["ai", "c_1234"]
        assert len(app.news_feeds) == 11   # 云端旧表缺内置 → 自动补齐 9 源
        assert app.news_feeds[0]["on"] is False
        assert app.news_srcs()[0] == ("c_1234", "例站")   # ai 隐藏, 自定义居首
        assert "ai" not in [i for i, _n in app.news_srcs()]
        assert news_rss._ACTIVE["c_1234"] == {"name": "例站",
                                              "url": "https://example.com/f"}
        assert "chinanews" in news_rss._ACTIVE
        assert json.load(open(fnf))[1]["id"] == "c_1234"   # 文件已落
        assert len([t for t, _ in app.net.jobs
                    if t.startswith("kvput:")]) == n_puts   # apply 不回推
        app.kv_apply("news_feeds", [])     # 云端空/清空 → 种子兜底
        assert len(app.news_feeds) == 10 and app.news_srcs()[0][0] == "ai"
        # 进页兜底拉取 kv_pull: 登录态入队
        n_jobs = len(app.net.jobs)
        app.kv_pull("radio_fav")
        assert app.net.jobs[-1][0] == "kvsync:radio_fav"
        assert len(app.net.jobs) == n_jobs + 1
        app.kv_pull("watch")               # 行情页兜底拉取 (登录即云同步)
        assert app.net.jobs[-1][0] == "kvsync:watch"
        app.kv_pull("news_feeds")          # 新闻页兜底拉取
        assert app.net.jobs[-1][0] == "kvsync:news_feeds"
        assert app._fin_sync_tag() == "云✓"    # 登录即云
        # kv_apply 光标钳回: 收藏组开着时云端列表变短 → rsel/tv_sel 收缩
        import radio_stations
        import tv_channels
        ks = [s["key"] for s in radio_stations.STATIONS][:2]
        ns = [c[0] for c in tv_channels.CHANNELS][:2]
        app.radio_grp = 0
        app.rsel = 5
        app.tv_grp = 0
        app.tv_sel = 4
        os.utime(fr, (now - 100, now - 100))   # 本地明显旧 → 云端裁决胜出
        os.utime(ftv, (now - 100, now - 100))  # (换号块写过 ftv, mtime 拨回)
        app.on_net("data", "kvsync:radio_fav",
                   {"ok": 1, "st": 200, "v": ks, "updated": now})
        assert app.radio_fav == ks
        assert app.rsel <= 1, app.rsel
        app.on_net("data", "kvsync:tv_fav",
                   {"ok": 1, "st": 200, "v": ns, "updated": now})
        assert app.tv_sel <= 1, app.tv_sel
        # 未登录: 不入队不推 (kv_pull 同)
        app2 = dash.App(FakeNet())
        app2.cfg["cloud_tok"] = ""
        n2 = len(app2.net.jobs)
        app2.kv_pull("radio_fav")
        assert len(app2.net.jobs) == n2     # 静默跳过
        app2.radio_fav_save()
        assert app2.net.last_job("kvput:radio_fav") is None
        # 显式变更 = 归属落位: kv_push 时残留旧号 owner → 落位当前号
        # (开机静默补推/首推仍被归属闸拦, 见上残留保护段)
        app.cfg["kv_owner"] = "alice"
        app.cfg["cloud_user"] = "bob"
        app.radio_fav_save()
        assert app.cfg["kv_owner"] == "bob"
        assert app.net.last_job("kvput:radio_fav") is not None
    finally:
        netmod.http_raw = orig_raw
    print("kv_sync OK (boot-pull/lww-apply/lww-push/change-push/"
          "align-confirm/modal-swallow/enter-upload/esc-overwrite/"
          "no-push-on-apply/not-logged-in/netfail-no-push/404-first-push/"
          "page-pull/clamp)")


def test_news_settings():
    """新闻源设置视图 (S): 种子/内置补齐/显隐切换(变更即推)/两相加源
    (标题→地址)/E 编辑自定义源/删自定义(dialog)/云端覆盖钳回/尾部截断/
    渲染冒烟."""
    import dash
    import news_rss
    pygame.init()
    pygame.display.set_mode((1, 1))
    canvas = pygame.Surface((dash.W, dash.H))
    tmp = tempfile.mkdtemp(prefix="oneapp")
    fnf = os.path.join(tmp, "news_feeds.json")
    dash.NEWS_FEEDS_FILE = fnf
    dash.KV_FILES.update({"news_feeds": fnf})
    dash.SETTINGS_FILE = os.path.join(tmp, "settings.json")
    orig_raw = netmod.http_raw
    try:
        # 冷启动: 缺文件 → 内置 8 源种子落盘 + 抓取层生效表同步
        app = dash.App(FakeNet())
        assert len(app.news_feeds) == 10 and json.load(open(fnf))
        assert [i for i, _n in app.news_srcs()] == \
            [f["id"] for f in news_rss.defaults()]
        assert "chinanews" in news_rss._ACTIVE     # 内置中新网
        # 老版本 7 源列表 → 内置补齐 (缺的内置追加, 显隐态不动, 即落盘)
        old = [f for f in app.news_feeds if f["id"] != "chinanews"]
        json.dump(old, open(fnf, "w"))
        app2 = dash.App(FakeNet())
        assert len(app2.news_feeds) == 10 and \
            app2.news_feeds[-1]["id"] == "chinanews"   # 只补缺的那条
        assert json.load(open(fnf))[-1]["id"] == "chinanews"
        # 尾部截断 (输入框保尾段前缀 …): 长文保尾, 短文原样
        long_url = "https://www.chinanews.com.cn/rss/scroll-news.xml"
        t = dash.tail_text(long_url, 15, 100)
        assert t.startswith("…") and long_url.endswith(t[1:]), t
        assert dash.textw(t, 15) <= 100, dash.textw(t, 15)   # 真放下, 不超框
        assert dash.tail_text("abc.com", 15, 100) == "abc.com"
        assert dash.tail_text("", 15, 100) == ""
        # S 进设置 (关详情) · 渲染冒烟 · Esc 退
        app.page = 4
        app.news_detail = None
        app.key(pygame.K_s)
        assert app.news_set and app.news_detail is None
        app.draw(canvas)
        app.key(pygame.K_ESCAPE)
        assert not app.news_set
        # ↑↓ 移光标 · Enter 显/隐: 文件+生效表+kv 推+OSD 已上云
        app.key(pygame.K_s)
        app.key(pygame.K_DOWN)
        assert app.ns_sel == 1
        n_puts = len([t for t, _ in app.net.jobs if t.startswith("kvput:")])
        app.cfg["cloud_tok"] = "t" * 24            # 登录态
        app.key(pygame.K_RETURN)                   # 隐藏 ithome
        f1 = app.news_feeds[1]
        assert f1["id"] == "ithome" and f1["on"] is False
        assert len(app.news_srcs()) == 9 and \
            "ithome" not in [i for i, _n in app.news_srcs()]
        assert "ithome" not in news_rss._ACTIVE
        assert json.load(open(fnf))[1]["on"] is False
        assert app.net.last_job("kvput:news_feeds") is not None
        assert len([t for t, _ in app.net.jobs
                    if t.startswith("kvput:")]) == n_puts + 1
        assert "已隐藏" in app.osd[0] and "已上云" in app.osd[0]
        app.key(pygame.K_RETURN)                   # 再按恢复显示
        assert f1["on"] is True and len(app.news_srcs()) == 10
        # 全隐藏 → 新闻列表/主页跑马灯空态 (渲染冒烟), 再恢复
        for f in app.news_feeds:
            f["on"] = False
        app.news_set = False
        assert app.news_items() == [] and app._news_items() == []
        app.draw(canvas)                           # 列表态: 源已全部隐藏
        for f in app.news_feeds:
            f["on"] = True
        # 两相加源 (地址先行): N → 地址 (EN, sym+n 实发 K_SLASH+'/' 是
        # 字符绝不劫持) → Enter → 标题 (拼音默认, / 切中英) → Enter 保存
        app.key(pygame.K_s)
        app.key(pygame.K_n)
        assert app.ns_input == 2 and app.buf == "" \
            and app.ns_edit_id is None
        app.draw(canvas)                           # 地址相渲染冒烟
        app.buf = "rss.example.com/feed"
        app.key(pygame.K_SLASH, "/")
        assert app.buf.endswith("feed/"), app.buf   # '/' 直输入地址
        app.key(pygame.K_RETURN)
        assert app.ns_input == 1 and app.ns_url.endswith("feed/") \
            and app.buf == "" and app.ime_mode is True   # 标题相拼音默认
        app.key(pygame.K_SLASH)                    # 标题相: '/' 仍是中英切换
        assert app.ime_mode is False
        app.buf = "例站"
        app.key(pygame.K_SLASH)
        app.draw(canvas)                           # 标题相渲染冒烟
        app.key(pygame.K_RETURN)
        assert app.ns_input == 0 and app.ns_url == ""
        nf = app.news_feeds[-1]
        assert nf["name"] == "例站" and nf["on"] is True
        assert nf["url"] == "https://rss.example.com/feed/"
        assert nf["id"].startswith("c_")
        assert app.ns_sel == len(app.news_feeds) - 1
        assert app.ns_scroll == max(0, app.ns_sel - 6)   # 窗口跟随
        # 标题空 → 域名兜底
        app.key(pygame.K_n)
        app.buf = "a.example.com/f"
        app.key(pygame.K_RETURN)                   # 地址 → 标题相
        app.key(pygame.K_RETURN)                   # 标题空直接存
        assert app.news_feeds[-1]["name"] == "a.example.com"
        assert len(app.news_feeds) == 12           # 10 内置 + 2 手加
        # E 编辑自定义源: 地址/标题均可改, id/on 不变
        app.ns_sel = len(app.news_feeds) - 2       # 例站
        fid0 = app.news_feeds[app.ns_sel]["id"]
        old_url = app.news_feeds[app.ns_sel]["url"]
        app.key(pygame.K_e)
        assert app.ns_input == 2 and app.ns_edit_id == fid0 \
            and app.buf == old_url
        app.buf = "rss2.example.com/feed"
        app.key(pygame.K_RETURN)                   # 地址 → 标题相 (预填旧名)
        assert app.ns_input == 1 and app.buf == "例站"
        app.buf = "例站二"
        app.key(pygame.K_RETURN)
        nf = app.news_feeds[app.ns_sel]
        assert nf["name"] == "例站二" \
            and nf["url"] == "https://rss2.example.com/feed"
        assert nf["id"] == fid0 and nf["on"] is True
        assert "已改" in app.osd[0]
        # E 内置源 → 拒绝
        app.ns_sel = 0
        app.key(pygame.K_e)
        assert app.ns_input == 0 and "不可编辑" in app.osd[0]
        # 编辑重址撞他源 → 拒绝且不改
        app.ns_sel = len(app.news_feeds) - 1       # a.example.com
        app.key(pygame.K_e)
        app.buf = "rss2.example.com/feed"          # 撞 例站二
        app.key(pygame.K_RETURN)                   # 地址 → 标题相
        app.key(pygame.K_RETURN)                   # 标题不动, 提交
        assert app.ns_input == 0 and "已有源" in app.osd[0]
        assert app.news_feeds[-1]["url"] == "https://a.example.com/f"
        # 地址无效 → 拒绝
        app.key(pygame.K_n)
        app.buf = "not a url"
        app.key(pygame.K_RETURN)                   # 地址 → 标题相
        app.key(pygame.K_RETURN)                   # 提交 → 拒
        assert app.ns_input == 0 and "地址无效" in app.osd[0]
        assert len(app.news_feeds) == 12
        # 上限: 临时调小再恢复
        real_max = dash.NEWS_FEEDS_MAX
        dash.NEWS_FEEDS_MAX = 13
        app.key(pygame.K_n)
        app.buf = "b.example.com/f"
        app.key(pygame.K_RETURN)                   # 地址 → 标题相
        app.key(pygame.K_RETURN)                   # 第 13 个, 收下
        app.key(pygame.K_n)
        app.buf = "c.example.com/f"
        app.key(pygame.K_RETURN)
        app.key(pygame.K_RETURN)                   # 第 14 个, 拒
        dash.NEWS_FEEDS_MAX = real_max
        assert len(app.news_feeds) == 13 and "最多" in app.osd[0]
        # 相中途 Esc = 整链作废 (标题/暂存/编辑目标全清)
        app.key(pygame.K_n)
        app.buf = "x"
        app.key(pygame.K_RETURN)
        app.key(pygame.K_ESCAPE)                   # 标题相 Esc → 整链作废
        assert app.ns_input == 0 and app.ns_url == "" \
            and app.ns_edit_id is None and app.buf == ""
        assert len(app.news_feeds) == 13
        # 删自定义: D → dialog → Enter 真删; 内置源 D 无效果
        app.ns_sel = len(app.news_feeds) - 1
        app.key(pygame.K_d)
        assert app.dialog and app.dialog[0] == "delnfeed"
        app.draw(canvas)                           # 确认框渲染冒烟
        app.key(pygame.K_RETURN)
        assert len(app.news_feeds) == 12 and app.dialog is None
        app.ns_sel = 0                             # 内置 ai → D 无 dialog
        app.key(pygame.K_d)
        assert app.dialog is None and len(app.news_feeds) == 12
        # 云端覆盖 (kv_apply) → 光标/滚动钳回 + 输入中收掉
        app.ns_sel = app.ns_scroll = 5
        app.key(pygame.K_n)
        app.buf = "typing"
        app.kv_apply("news_feeds", news_rss.defaults())
        assert len(app.news_feeds) == 10
        assert app.ns_input == 0 and app.buf == ""
        assert app.ns_sel == 0 and app.ns_scroll == 0
        assert set(i for i, _n in app.news_srcs()) == set(news_rss.FEEDS)
        app.draw(canvas)
        app.key(pygame.K_ESCAPE)
        assert not app.news_set
        # 底栏提示分左右两段, 合计不溢出屏宽
        lw = dash.textw("P 翻页 · 1-7 打开 · ←→ 源", 15)
        rw = dash.textw("S 设置", 15)
        assert lw + rw <= dash.W - 12, (lw, rw)
        lw = dash.textw("↑↓ 选 · Enter 显/隐", 13)
        rw = dash.textw("N加 · E改 · D删 · Esc 退", 13)
        assert lw + rw <= dash.W - 12, (lw, rw)
    finally:
        netmod.http_raw = orig_raw
    print("news_settings OK (seed/merge-builtin/tail-text/s-toggle/push/"
          "add-2phase/add-domain-fallback/edit-custom/edit-builtin-guard/"
          "edit-dup-url/add-invalid/add-max/esc-chain/del-custom/"
          "del-builtin-guard/apply-clamp/render)")


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
    # 主页底栏 8 直开邮件 (2026-11-16 定版: 对调后 8=邮件; 网格不再列)
    app.page = 0
    app.key(pygame.K_8)
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
    assert app.mail_unread_n["user@163.com"] == 3  # STATUS 求和 (绘制拼 "未读 3")
    # (folders 层再进入会以 STATUS 求和覆盖, 见后段)
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
    app.on_net("data", "mail_all:user@163.com:unread",
               {"ok": True, "total": 2, "n_unseen": 1, "n_flagged": 1,
                                                    "msgs": [
        {"uid": "11", "subject": "你好", "from": "a@b.com",
         "date": "11-14 09:00", "preview": "内容", "seen": False,
         "flagged": False, "fld": "INBOX", "fldn": "INBOX"},
        {"uid": "10", "subject": "旧的", "from": "c@d.com",
         "date": "11-13 08:00", "preview": "", "seen": True,
         "flagged": True, "fld": "Bob", "fldn": "Bob"}]})
    assert app.mail_total == 2 and len(app.mail_msgs) == 2
    assert app.mail_got                     # 列表已落地 (区分空/加载中)
    assert app.mail_unread_n["user@163.com"] == 1  # 聚合回填纯未读数
    assert app.mail_flag_n["user@163.com"] == 1    # 红旗数 (绘制拼 "红旗 1")
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
    app.mail_sel = 1
    app.key(pygame.K_6)                       # 列表直接标已读 (聚合)
    assert ("job", "mail_act:Bob:10:mark_read") in app.net.got
    app.mail_msgs[1]["seen"] = True           # 乐观已落 (旗标在, 行保留)
    app.mail_sel = 0
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
        assert r["total"] == 3, r                       # 仅未读: INBOX 1 + Bob 2 (已发送旗标不进未读视图)
        assert (r.get("n_unseen"), r.get("n_flagged")) == (3, 0), r
        msgs = r["msgs"]
        assert [m["uid"] for m in msgs] == ["3", "9", "5"], msgs       # Date 倒序 (仅未读)
        assert [m["fldn"] for m in msgs] == ["INBOX", "Bob", "Bob"]
        assert not msgs[0]["flagged"] and not msgs[0]["seen"]   # INBOX 新未读
        assert msgs[1]["flagged"] and not msgs[1]["seen"]       # Bob 旗标件
        assert msgs[2]["seen"] and not msgs[2]["flagged"]
        assert all(m["preview"].startswith("hello") for m in msgs)
        assert "INBOX" in selected                # INBOX 纳入聚合 (默认视图=聚合)
        assert "Junk" not in selected and "Drafts" not in selected
        r2 = mailmod.fetch_all({"email": "t@163.com", "auth": "k"},
                               "flagged", 30)
        assert r2["ok"] and [m["uid"] for m in r2["msgs"]] == ["2", "9"]
        assert (r2.get("n_unseen"), r2.get("n_flagged")) == (0, 2), r2
        r3 = mailmod.fetch_all({"email": "t@163.com", "auth": "k"},
                               "unread", 30)   # UI 别名: 仅未读单键
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
    dlt = fetchers.dlt_fetch()
    assert dlt["ok"] and len(dlt["latest"]["blues"]) == 2
    print("  dlt latest", dlt["latest"]["code"],
          dlt["latest"]["reds"], "+", dlt["latest"]["blues"])
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

    # ── 台表: 90 条实测源 (2026-11-21 设备侧体检重排, 死源剔除),
    #    4 组齐全, 无空名/坏协议/短时效 token; ≥20 台多路备胎 ──
    assert len(tv_channels.CHANNELS) == 90
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
    assert sum(1 for c in tv_channels.CHANNELS if len(c[2]) > 1) >= 20

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
    # UA_BY_HOST 机制: 注入假 host 验证 -user_agent (aptv 转发源已
    # 全线 404 下线, 表清空留占位)
    tv_channels.UA_BY_HOST["ua.test.host"] = "Test-UA"
    a_ua = app.tv_argv("http://ua.test.host:88/live.php?id=CCTV1", False, True)
    assert a_ua[a_ua.index("-user_agent") + 1] == "Test-UA"
    assert "ua.test.host" in a_ua[a_ua.index("-i") + 1]
    tv_channels.UA_BY_HOST.clear()
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
        app.tv_key(pygame.K_5)                # 播放中 5 = 取消收藏 → 确认框
        assert app.dialog and app.dialog[0] == "untvfav"
        assert app.tv_fav == ["CCTV1", wl[0][0]]   # 未确认前不动
        app.key(pygame.K_RETURN)              # Enter 确认移除
        assert app.tv_fav == ["CCTV1"]
        app.tv_stop()
        app.tv_grp, app.tv_sel = 0, 0
        app.draw(canvas)                      # 单条收藏组不崩
        app.tv_key(pygame.K_5)                # 组内取消 → 确认框 → 空收藏
        assert app.dialog and app.dialog[0] == "untvfav"
        app.key(pygame.K_RETURN)
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


def test_sched():
    """日程卡 (只读): 无登录态不拉取/进卡拉取落盘/排序渲染/无管理动作/主页卡冒烟."""
    import dash
    pygame.init()
    pygame.display.set_mode((1, 1))
    canvas = pygame.Surface((dash.W, dash.H))
    tmp = tempfile.mkdtemp(prefix="oneapp")
    dash.SETTINGS_FILE = os.path.join(tmp, "settings.json")
    dash.IDEAS_FILE = os.path.join(tmp, "ideas.json")
    dash.SCHED_CACHE = os.path.join(tmp, "sched.json")
    app = dash.App(FakeNet())
    # 未登录: 渲染冒烟, 4 刷新不入队 (无 token)
    app.tool = "sched"
    app.draw(canvas)
    app.key(pygame.K_4)
    assert not app.net.jobs
    # 想法云同 token → 拉取落盘
    app.cfg["cloud_url"] = "https://notes.apisz.top"
    app.cfg["cloud_tok"] = "b" * 24
    app.sched_refresh(False)
    assert app.net.last_job("schedget") is not None
    orig_raw = netmod.http_raw
    calls = []
    payload = {"now": 100.0,
               "reminds": [
                   {"id": "a" * 12, "text": "milk", "due": 1800000000.0,
                    "done": 0},
                   {"id": "c" * 12, "text": "old", "due": 100.0, "done": 0}],
               "trips": [{"id": "e" * 12, "text": "trip", "due": 1800003600.0,
                          "tend": 1800036000.0, "loc": "hq"}]}
    netmod.http_raw = lambda url, **k: (
        calls.append((k.get("method", "GET"), url))
        or (200, json.dumps(payload).encode()))
    d = app.net.last_job("schedget")()
    assert d["ok"] == 1 and len(d["reminds"]) == 2 and len(d["trips"]) == 1
    app.on_net("data", "schedget", d)
    assert json.load(open(dash.SCHED_CACHE))["reminds"][0]["id"] == "a" * 12
    items = app._sched_items()
    assert [x["id"][0] for x in items] == ["c", "a", "e"]   # 时间升序
    app.draw(canvas)                                        # 渲染冒烟
    # 只读: Enter/7 均无管理动作 (零新 job, 无 dialog, 数据不变)
    n_jobs = len(app.net.jobs)
    app.key(pygame.K_RETURN)
    app.key(pygame.K_7)
    app.key(pygame.K_UP)
    app.key(pygame.K_DOWN)
    assert len(app.net.jobs) == n_jobs
    assert app.dialog is None
    assert [x["id"][0] for x in app._sched_items()] == ["c", "a", "e"]
    # 主页右列「提醒/行程」卡: 开关默认开 + 渲染冒烟
    app.cfg["cards"]["sched"] = True
    app.tool = None
    app.page = 0
    app.draw(canvas)
    netmod.http_raw = orig_raw
    print("sched OK  (fetch/cache/sort/readonly/home-card)")


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
    test_settings_app()
    test_cloud()
    test_kv_sync()
    test_news_settings()
    test_sched()
    test_reg()
    test_login()
    test_mail_keys()
    test_mail_all()
    test_mail_sess()
    test_driver_sym()
    test_mail_compose()
    if os.environ.get("RUN_NET"):
        test_net_live()
        test_mail_live()
    print("ALL PASS")
