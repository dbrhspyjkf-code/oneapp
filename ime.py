"""本地拼音输入引擎 (词表查 + 联想; 数据 ime.dat.gz 懒加载).

表由 research/gen_ime.py 生成 (jieba 词典 + pypinyin 注音):
  chars : 音节(无声调) → 常用字 (按字频, ≤32)
  words : 拼音连写(无声调) → 常用词 (按词频, ≤6, 2-4 字)
  assoc : 字 → 以该字开头的常用词 (联想, ≤8)
无需分词: 词表键 = 拼音连写, 直接以整串查; 前缀匹配供打词中途建议.
"""
import gzip
import json
import os

_D = None                                 # {"chars":…, "words":…, "assoc":…}


def _load():
    global _D
    if _D is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "ime.dat.gz")
        with gzip.open(p, "rb") as f:
            _D = json.load(f)
    return _D


def candidates(buf):
    """拼音串 → 候选列表 (精确词 + 单字 + 前缀词, ≤32).
    单字排在截断线内优先 — 罕见字 (如 蔡) 不被高频词挤掉."""
    if not buf:
        return []
    d = _load()
    out = list(d["words"].get(buf, []))       # 精确词 (≤6)
    out.extend(d["chars"].get(buf, []))       # 单字 (≤32, 按字频)
    if len(buf) >= 2:                         # 前缀词建议 (打词中途, ≤8)
        pre = []
        for k, ws in d["words"].items():
            if k != buf and k.startswith(buf):
                pre.extend(ws[:2])
        out.extend(pre[:8])
    return out[:32]


def assoc(char):
    """联想: 以 char 开头的常用词."""
    return _load()["assoc"].get(char, [])
