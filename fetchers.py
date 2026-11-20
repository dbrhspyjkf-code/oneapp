# -*- coding: utf-8 -*-
"""fetchers — 公网数据源取数层 (供 Net.job 在 worker 线程调用).

每个 fetcher 都是零参函数: 成功返回 dict, 失败返回 None (Net 不缓存失败,
下一轮 TTL 到期自动重试). 输出形状与原家庭后端端点一致, UI 层零改动.

数据源 (2026-11-14 家庭网络实测全部可达):
  fx  — open.er-api.com/v6/latest/USD   (免 key, 1h TTL)
  ssq — www.cwl.gov.cn 中彩官网开奖 API  (600s TTL + 磁盘快照兜底)
  ipo — datacenter-web.eastmoney.com 新股申购 (1800s TTL)
"""

import json
import os
import time
import urllib.request
from datetime import datetime

_CACHE_DIR = os.path.join(os.path.expanduser("~/.local/share/oneapp"), "cache")
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
       " (KHTML, like Gecko) Chrome/120.0 Safari/537.36 ONEAPP/1.0")


_OPENER_DIRECT = urllib.request.build_opener(
    urllib.request.ProxyHandler({}))       # 绕过系统代理 (设备无代理; 开发机
                                           #   代理出口会污染 IP 定位)


def _get_json(url, headers=None, timeout=12):
    hdrs = {"User-Agent": _UA}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(1048576).decode("utf-8", "replace"))


def _get_json_direct(url, headers=None, timeout=12):
    """同 _get_json 但强制无代理 (IP 定位/天气: 必须用设备真实出口 IP;
    open-meteo 直连可达, 走开发机代理反而失败)."""
    hdrs = {"User-Agent": _UA}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with _OPENER_DIRECT.open(req, timeout=timeout) as r:
        return json.loads(r.read(1048576).decode("utf-8", "replace"))


def _snap_read(name):
    try:
        with open(os.path.join(_CACHE_DIR, name)) as f:
            return json.load(f)
    except Exception:
        return None


def _snap_write(name, obj):
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(os.path.join(_CACHE_DIR, name), "w") as f:
            json.dump(obj, f, ensure_ascii=False)
    except Exception:
        pass


# ── 汇率 (open.er-api.com, 每 1 USD 兑换表) ────────────────────────────────
_WANTED = ["USD", "CNY", "EUR", "JPY", "GBP", "HKD", "KRW", "AUD"]
_FALLBACK = {
    "USD": 1.00, "CNY": 7.25, "EUR": 0.92, "JPY": 157.0,
    "GBP": 0.79, "HKD": 7.81, "KRW": 1380.0, "AUD": 1.51,
}


def fx_fetch():
    """→ {"ok": True, "base": "USD", "rates": {...}, "updated": unix, "source": ...}"""
    try:
        j = _get_json("https://open.er-api.com/v6/latest/USD", timeout=10)
        if str(j.get("result")).lower() not in ("ok", "success"):
            raise ValueError("upstream %s" % j.get("result"))
        rates = {k: float(j["rates"][k]) for k in _WANTED if k in j.get("rates", {})}
        if "CNY" not in rates:
            raise ValueError("no CNY")
        out = {"ok": True, "base": "USD", "rates": rates,
               "updated": int(j.get("time_last_update_unix") or 0),
               "source": "open.er-api.com"}
        _snap_write("fx.json", out)
        return out
    except Exception:
        snap = _snap_read("fx.json")
        if snap:
            return snap
        return {"ok": True, "base": "USD", "rates": dict(_FALLBACK),
                "updated": 0, "source": "fallback"}


# ── 每日一句 (hitokoto 一言, 大陆直连; 3600s TTL) ──────────────────────────
def hitokoto_fetch():
    """→ {"ok": True, "text": str, "from": str}"""
    try:
        j = _get_json("https://v1.hitokoto.cn/?c=i&c=k&c=e", timeout=6)
        t = (j.get("hitokoto") or "").strip()
        if t:
            out = {"ok": True, "text": t[:60], "from": (j.get("from") or "")[:16]}
            _snap_write("hitokoto.json", out)
            return out
    except Exception:
        pass
    return _snap_read("hitokoto.json") or {
        "ok": True, "text": "机不可失,时不再来。", "from": "旧唐书"}


# ── IP 归属地 (天气定位; 1800s TTL) ────────────────────────────────────────
def wx_locate():
    """出口 IP → {"lat","lon","city","prov","src"}; 全源失败返回 None (UI 回落深圳).
    ① ip-api.com (免费 http, 中文省市+经纬度一步到位; 2026-11-14 家宽实测通)
    ② api.ip.sb/geoip (备用; 英文省市, prov 走别名表归一)
    热点/CGNAT 下归属地=运营商出口, 省市级精度, 对天气/电台本地组够用."""
    try:
        j = _get_json_direct(
            "http://ip-api.com/json/?lang=zh-CN&fields=status,city,regionName,lat,lon",
            timeout=4)
        if str(j.get("status")).lower() == "success" \
                and j.get("lat") is not None:
            out = {"lat": float(j["lat"]), "lon": float(j["lon"]),
                   "city": (j.get("city") or "").strip() or "定位",
                   "prov": (j.get("regionName") or "").strip(),
                   "src": "ip-api"}
            _snap_write("wx_loc.json", out)
            return out
    except Exception:
        pass
    try:
        j = _get_json_direct("https://api.ip.sb/geoip", timeout=4)
        if j.get("latitude") is not None:
            out = {"lat": float(j["latitude"]), "lon": float(j["longitude"]),
                   "city": (j.get("city") or "").strip() or "定位",
                   "prov": (j.get("region") or "").strip(),
                   "src": "ip.sb"}
            _snap_write("wx_loc.json", out)
            return out
    except Exception:
        pass
    snap = _snap_read("wx_loc.json")           # 断网: 沿用上次归属地
    return snap


# ── 东方财富妙想 Skills 自选股同步 (MX_APIKEY) ─────────────────────────────
_EM_SEL_URL = "https://mkapi2.dfcfs.com/finskillshub/api/claw/self-select/get"


def em_watchlist_parse(j):
    """妙想 mx_selfselect 响应 → {"ok", "items":[{code,name}], "err"?}
    (纯函数便于离线测试). code 统一补 sh/sz/bj 前缀."""
    try:
        if str(j.get("code")) not in ("0", "200") \
                and j.get("status") not in (0, "0", "success", 200):
            return {"ok": False,
                    "err": str(j.get("message") or j.get("status"))[:30]}
    except Exception:
        return {"ok": False, "err": "bad resp"}
    items = []
    allr = (j.get("data") or {}).get("allResults") or {}
    blocks = allr if isinstance(allr, list) else [allr]   # dict/list 双形态
    for block in blocks:
        if not isinstance(block, dict):
            continue
        res = block.get("result") or {}
        for row in res.get("dataList") or []:
            code = str(row.get("SECURITY_CODE") or "").strip().lower()
            if not code:
                continue
            if code[:2] not in ("sh", "sz", "bj"):
                mkt = str(row.get("MARKET_SHORT_NAME") or "").strip().lower()
                if mkt in ("sh", "sz", "bj"):
                    code = mkt + code
                elif code[:1].isdigit():
                    code = ("sh" if code[0] in "569"
                            else ("bj" if code[0] in "48" else "sz")) + code
                else:
                    continue                     # 港美股等跳过
            items.append({"code": code,
                          "name": str(row.get("SECURITY_SHORT_NAME") or "")})
    if not items:
        return {"ok": False, "err": "empty · 去东财App确认"}
    return {"ok": True, "items": items}


def em_watchlist_fetch(apikey):
    """拉取通行证账户自选股 (POST, apikey 放 header). → parse 结果.

    接口异步: 首响可能只有 SELECT_STOCK 查询容器 (无 allResults, 实测),
    间隔 1.2s 重试至多 3 次."""
    if not apikey:
        return {"ok": False, "err": "未配置 API-KEY"}
    try:
        for attempt in range(3):
            req = urllib.request.Request(
                _EM_SEL_URL, data=b"{}", method="POST",
                headers={"apikey": apikey, "Content-Type": "application/json",
                         "User-Agent": _UA})
            with _OPENER_DIRECT.open(req, timeout=12) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            if (j.get("data") or {}).get("allResults") is not None \
                    or attempt == 2:
                return em_watchlist_parse(j)
            time.sleep(1.2)
    except Exception as e:
        return {"ok": False, "err": repr(e)[:40]}
    return {"ok": False, "err": "no results"}


_EM_MANAGE_URL = _EM_SEL_URL.replace("/get", "/manage")


def em_manage_parse(j):
    """妙想 manage 响应 → {"ok": bool, "msg"?/"err"?} (纯函数便于离线测试).
    实测同步返回: status/code=0, data 是确认文案 ("…已为您将1个标的(贵州茅台)添加自选…")."""
    try:
        if str(j.get("code")) not in ("0", "200") \
                and j.get("status") not in (0, "0", "success", 200):
            return {"ok": False,
                    "err": str(j.get("message") or j.get("status"))[:30]}
    except Exception:
        return {"ok": False, "err": "bad resp"}
    return {"ok": True, "msg": str(j.get("data") or "")[:48]}


def em_watch_manage(apikey, code, add=True):
    """单只自选股增/删推送东财通行证账户 (自然语言 manage 接口, apikey
    放 header; 裸 6 位代码实测可解析, 北交所云端自动归一 430xxx→920xxx).
    同步 ~1.6s 返回 → em_manage_parse 结果."""
    if not apikey:
        return {"ok": False, "err": "未配置 API-KEY"}
    bare = str(code or "")[-6:]
    q = ("把%s添加到我的自选股列表" if add
         else "把%s从我的自选股列表删除") % bare
    try:
        req = urllib.request.Request(
            _EM_MANAGE_URL,
            data=json.dumps({"query": q}).encode("utf-8"), method="POST",
            headers={"apikey": apikey, "Content-Type": "application/json",
                     "User-Agent": _UA})
        with _OPENER_DIRECT.open(req, timeout=20) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        return em_manage_parse(j)
    except Exception as e:
        return {"ok": False, "err": repr(e)[:40]}


# ── 双色球 (中彩官网; 参考 hermes _ssq.py 同源移植) ─────────────────────────
_SSQ_URL = ("https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/"
            "findDrawNotice?name=ssq&issueCount=100")
_SSQ_HEADERS = {
    "Referer": "https://www.cwl.gov.cn/ygkj/wqkjgg/ssq/",
    "Accept": "application/json",
}


def _ssq_parse(payload):
    rows = payload.get("result") or []
    issues = []
    for r in rows:
        code = (r.get("code") or "").strip()
        red_s = (r.get("red") or "").strip()
        blue_s = (r.get("blue") or "").strip()
        if not code or not red_s or not blue_s:
            continue
        reds = [int(x) for x in red_s.split(",") if x.strip().isdigit()]
        if len(reds) != 6 or not blue_s.isdigit():
            continue
        grades = []
        for g in r.get("prizegrades") or []:
            try:
                level = int(g.get("type") or 0)
            except (TypeError, ValueError):
                continue
            if not 1 <= level <= 6:
                continue
            try:
                money = int(g.get("typemoney") or 0)
            except (TypeError, ValueError):
                money = 0
            try:
                num = int(g.get("typenum") or 0)
            except (TypeError, ValueError):
                num = 0
            grades.append({"level": level, "money": money, "num": num})
        grades.sort(key=lambda x: x["level"])
        try:
            sales = int(r.get("sales") or 0)
        except (TypeError, ValueError):
            sales = 0
        try:
            pool = int(r.get("poolmoney") or 0)
        except (TypeError, ValueError):
            pool = 0
        issues.append({"code": code, "date": (r.get("date") or "")[:10],
                       "week": r.get("week") or "", "reds": reds,
                       "blue": int(blue_s), "sales": sales, "pool": pool,
                       "grades": grades})
    if not issues:
        return None
    return {"ok": True, "latest": issues[0], "issues": issues, "count": len(issues)}


def ssq_fetch():
    out = None
    try:
        out = _ssq_parse(_get_json(_SSQ_URL, headers=_SSQ_HEADERS, timeout=12))
        if out:
            _snap_write("ssq.json", out)
    except Exception:
        pass
    return out or _snap_read("ssq.json")


# ── 双色球拍照兑奖 (ZERO 单机: 摄像头 rpicam-jpeg + GLM-4V-Flash 直连 OCR +
#    客户端判奖; 参考 hermes _ssq.py judge_bets / TAB5 app_lottery 同源) ─────
SSQ_OCR_MODEL = "glm-4v-flash"          # 免费视觉模型 (下线可换 glm-4v-plus)
_SSQ_OCR_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
_SSQ_OCR_PROMPT = (
    "这是双色球彩票票面照片。识别: 1) 开奖期号(20开头的7位数字, 无则空串); "
    "2) 每一注的单式号码(6个红球1-33 + 1个蓝球1-16)。复式票按每一行单式拆出。"
    "只输出 JSON, 不要解释: {\"issue\":\"...\",\"bets\":[{\"reds\":[1,2,3,4,5,6],"
    "\"blue\":8}]}")


def ssq_capture_photo(out_path, timeout=12):
    """摄像头单拍 (rpicam-jpeg one-shot). 成功返回文件大小, 失败 None.
    IMX219 CSI (2026-11-19 实测: 1280x720 q70 ~89KB 数秒出片)."""
    import subprocess
    try:
        subprocess.run(
            ["rpicam-jpeg", "-o", out_path, "--width", "1280", "--height",
             "720", "--quality", "70", "--nopreview"],
            timeout=timeout,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return os.path.getsize(out_path)
    except Exception:
        return None


def _ssq_bet_ok(b):
    """单式注形状校验: reds 去重后正好 6 个且全在 1-33, blue 1-16."""
    if not isinstance(b, dict):
        return None
    reds = b.get("reds") or []
    blue = b.get("blue")
    if not isinstance(reds, list) or not isinstance(blue, int):
        if isinstance(blue, str) and blue.strip().isdigit():
            blue = int(blue)
        else:
            return None
    clean = []
    for r in reds:
        try:
            r = int(r)
        except (TypeError, ValueError):
            return None
        if not 1 <= r <= 33 or r in clean:
            return None
        clean.append(r)
    if len(clean) != 6 or not 1 <= blue <= 16:
        return None
    return sorted(clean), blue


def ssq_ocr_scan(jpeg_bytes, key, timeout=40):
    """票面照片 → GLM-4V-Flash → 校验后的注单.
    → {ok, issue, bets:[{reds,blue}], invalid} / {ok:False, err}."""
    import base64
    body = json.dumps({
        "model": SSQ_OCR_MODEL,
        "temperature": 0.1,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url":
             "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes)
             .decode("ascii")}},
            {"type": "text", "text": _SSQ_OCR_PROMPT}]}],
    }).encode("utf-8")
    req = urllib.request.Request(
        _SSQ_OCR_URL, data=body,
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read(4 * 1024 * 1024).decode("utf-8", "replace"))
    except Exception as e:
        return {"ok": False, "err": str(e)[:60]}
    try:
        txt = (j["choices"][0]["message"]["content"] or "").strip()
        i, k2 = txt.find("{"), txt.rfind("}")
        txt = txt[i:k2 + 1] if 0 <= i < k2 else ""
        o = json.loads(txt)
    except Exception:
        return {"ok": False, "err": "响应解析失败"}
    bets, invalid = [], 0
    for b in o.get("bets") or []:
        pb = _ssq_bet_ok(b)
        if pb:
            bets.append({"reds": pb[0], "blue": pb[1]})
        else:
            invalid += 1
    issue = str(o.get("issue") or "").strip()
    if not (issue.isdigit() and len(issue) == 7):
        issue = ""
    return {"ok": True, "issue": issue, "bets": bets, "invalid": invalid}


def ssq_judge(bets, issue):
    """识别注单 vs 开奖期 → 逐注判奖 (hermes _ssq.py judge_bets 同源).
    只判形状完整的单式注; prize=None=未中/形状不齐不判. 奖金取 issue
    grades[level-1].money (一二等奖单注奖金每期浮动)."""
    grades = {g.get("level"): g.get("money") or 0
              for g in issue.get("grades") or []}
    wr = set(issue.get("reds") or [])
    wb = issue.get("blue")
    out = []
    for b in bets or []:
        reds, blue = b.get("reds") or [], b.get("blue")
        if len(reds) != 6 or not isinstance(blue, int):
            out.append({"reds": reds, "blue": blue, "prize": None,
                        "red_hit": 0, "blue_hit": 0, "money": 0})
            continue
        rh = len(wr & set(reds))
        bh = 1 if blue == wb else 0
        if rh == 6:
            level = 1 if bh else 2
        elif rh == 5:
            level = 3 if bh else 4
        elif rh == 4:
            level = 4 if bh else 5
        elif rh == 3:
            level = 5 if bh else 0
        else:
            level = 6 if bh else 0
        out.append({"reds": sorted(reds), "blue": blue, "red_hit": rh,
                    "blue_hit": bh, "prize": "%d等奖" % level if level else None,
                    "money": grades.get(level, 0) if level else 0})
    return out


# ── 新股申购 (东方财富 RPTA_APP_IPOAPPLY, 只留申购日 ≥ 今日, 按日升序) ──────
_IPO_URL = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
            "?reportName=RPTA_APP_IPOAPPLY"
            "&columns=SECURITY_NAME_ABBR,SECURITY_CODE,APPLY_DATE,ISSUE_PRICE"
            "&pageSize=30&sortColumns=APPLY_DATE&sortTypes=-1"
            "&source=SECURITIES&client=WEB")
_IPO_HEADERS = {"Referer": "https://data.eastmoney.com/",
                "Accept": "application/json"}


def ipo_fetch():
    try:
        j = _get_json(_IPO_URL, headers=_IPO_HEADERS, timeout=12)
        rows = (j.get("result") or {}).get("data") or []
        today = datetime.now().date()
        items = []
        for r in rows:
            apply_s = (r.get("APPLY_DATE") or "")[:10]
            if not apply_s:
                continue
            try:
                apply_d = datetime.strptime(apply_s, "%Y-%m-%d").date()
            except ValueError:
                continue
            if apply_d < today:                 # 已截止的不要
                continue
            price = r.get("ISSUE_PRICE")
            items.append({"name": r.get("SECURITY_NAME_ABBR") or "",
                          "code": r.get("SECURITY_CODE") or "",
                          "date": apply_d.strftime("%m-%d"),
                          "price": "%.2f" % float(price) if price else "--",
                          "today": apply_d == today})
        if not items:
            return {"ok": True, "items": [], "count": 0}
        items.sort(key=lambda x: x["date"])
        out = {"ok": True, "items": items[:5], "count": len(items)}
        _snap_write("ipo.json", out)
        return out
    except Exception:
        snap = _snap_read("ipo.json")
        if snap:
            return snap
        return {"ok": True, "items": [], "count": 0}


def glm_usage(key):
    """GLM Coding Plan 配额 (Bearer key). 端点同官方 glm-plan-usage 插件:
    GET /api/monitor/usage/quota/limit.
    → {ok, p5, pw, r5, rw, t5} 5h/周 用量% + 余量 + 5h 窗重置时刻 (epoch ms)."""
    url = "https://open.bigmodel.cn/api/monitor/usage/quota/limit"
    try:
        req = urllib.request.Request(
            url, headers={"Authorization": "Bearer " + key,
                          "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        if not (j.get("success") or j.get("code") == 200):
            return {"ok": False, "err": str(j.get("msg", ""))[:40]}
        data = j.get("data") or {}
        items = data.get("quota_limits") or data.get("limits") or []
        p5 = pw = r5 = rw = t5 = None
        for it in items:
            u, pc = it.get("unit"), it.get("percentage")
            if u == 3 and pc is not None:
                p5 = pc                     # unit3 = 5h token 配额
                r5 = it.get("remaining")
                t5 = it.get("nextResetTime")
            if u == 6 and pc is not None:
                pw = pc                     # unit6 = 周 token 配额
                rw = it.get("remaining")
        return {"ok": True, "p5": p5, "pw": pw,
                "r5": r5, "rw": rw, "t5": t5}
    except Exception as e:
        return {"ok": False, "err": str(e)[:60]}


def ds_balance(key):
    """DeepSeek 余额 (官方 user/balance, Bearer key). → {ok, cny}."""
    url = "https://api.deepseek.com/user/balance"
    try:
        req = urllib.request.Request(
            url, headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=8) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        cny = ""
        for b in j.get("balances") or j.get("balance_infos") or []:
            if b.get("currency") == "CNY":
                cny = b.get("total_balance", "")
        return {"ok": True, "cny": cny}
    except Exception as e:
        return {"ok": False, "err": str(e)[:60]}
