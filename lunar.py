# -*- coding: utf-8 -*-
"""农历 — 逐字移植 car-dash car.js (源头为设备 lunar_calendar.h), 1900-2100.

自测基准: 1900-01-31 / 2024-02-10 / 2025-01-29 / 2026-02-17 均为正月初一.
"""

# 每年农历信息: bits 0-3 闰月月份(0 无闰), 0x10000 大月标记, 0x8000..0x8 月大小
LUNAR_INFO = [
0x04bd8, 0x04ae0, 0x0a570, 0x054d5, 0x0d260, 0x0d950, 0x16554, 0x056a0, 0x09ad0, 0x055d2,
0x04ae0, 0x0a5b6, 0x0a4d0, 0x0d250, 0x1d255, 0x0b540, 0x0d6a0, 0x0ada2, 0x095b0, 0x14977,
0x04970, 0x0a4b0, 0x0b4b5, 0x06a50, 0x06d40, 0x1ab54, 0x02b60, 0x09570, 0x052f2, 0x04970,
0x06566, 0x0d4a0, 0x0ea50, 0x16a95, 0x05ad0, 0x02b60, 0x186e3, 0x092e0, 0x1c8d7, 0x0c950,
0x0d4a0, 0x1d8a6, 0x0b550, 0x056a0, 0x1a5b4, 0x025d0, 0x092d0, 0x0d2b2, 0x0a950, 0x0b557,
0x06ca0, 0x0b550, 0x15355, 0x04da0, 0x0a5b0, 0x14573, 0x052b0, 0x0a9a8, 0x0e950, 0x06aa0,
0x0aea6, 0x0ab50, 0x04b60, 0x0aae4, 0x0a570, 0x05260, 0x0f263, 0x0d950, 0x05b57, 0x056a0,
0x096d0, 0x04dd5, 0x04ad0, 0x0a4d0, 0x0d4d4, 0x0d250, 0x0d558, 0x0b540, 0x0b6a0, 0x195a6,
0x095b0, 0x049b0, 0x0a974, 0x0a4b0, 0x0b27a, 0x06a50, 0x06d40, 0x0af46, 0x0ab60, 0x09570,
0x04af5, 0x04970, 0x064b0, 0x074a3, 0x0ea50, 0x06b58, 0x05ac0, 0x0ab60, 0x096d5, 0x092e0,
0x0c960, 0x0d954, 0x0d4a0, 0x0da50, 0x07552, 0x056a0, 0x0abb7, 0x025d0, 0x092d0, 0x0cab5,
0x0a950, 0x0b4a0, 0x0baa4, 0x0ad50, 0x055d9, 0x04ba0, 0x0a5b0, 0x15176, 0x052b0, 0x0a930,
0x07954, 0x06aa0, 0x0ad50, 0x05b52, 0x04b60, 0x0a6e6, 0x0a4e0, 0x0d260, 0x0ea65, 0x0d530,
0x05aa0, 0x076a3, 0x096d0, 0x04afb, 0x04ad0, 0x0a4d0, 0x1d0b6, 0x0d250, 0x0d520, 0x0dd45,
0x0b5a0, 0x056d0, 0x055b2, 0x049b0, 0x0a577, 0x0a4b0, 0x0aa50, 0x1b255, 0x06d20, 0x0ada0,
0x14b63, 0x09370, 0x049f8, 0x04970, 0x064b0, 0x168a6, 0x0ea50, 0x06b20, 0x1a6c4, 0x0aae0,
0x092e0, 0x0d2e3, 0x0c960, 0x0d557, 0x0d4a0, 0x0da50, 0x05d55, 0x056a0, 0x0a6d0, 0x055d4,
0x052d0, 0x0a9b8, 0x0a950, 0x0b4a0, 0x0b6a6, 0x0ad50, 0x055a0, 0x0aba4, 0x0a5b0, 0x052b0,
0x0b273, 0x06930, 0x07337, 0x06aa0, 0x0ad50, 0x14b55, 0x04b60, 0x0a570, 0x054e4, 0x0d260,
0x0e968, 0x0d520, 0x0daa0, 0x16aa6, 0x056d0, 0x04ae0, 0x0a9d4, 0x0a4d0, 0x0d150, 0x0f252,
0x0d520,
]

LM_CHARS = ["正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "腊"]
LD_CHARS = ["初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
            "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
            "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十"]
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def days_from_civil(y, m, d):
    """Howard Hinnant days_from_civil: 自 0000-03-01 起的连续纪日."""
    y -= 1 if m <= 2 else 0
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def leap_month_of(ly):
    return LUNAR_INFO[ly - 1900] & 0xF


def leap_days_of(ly):
    lm = leap_month_of(ly)
    return (30 if (LUNAR_INFO[ly - 1900] & 0x10000) else 29) if lm else 0


def month_days_of(ly, m):
    return 30 if (LUNAR_INFO[ly - 1900] & (0x10000 >> m)) else 29


def year_days_of(ly):
    s = 348
    bit = 0x8000
    while bit > 0x8:
        if LUNAR_INFO[ly - 1900] & bit:
            s += 1
        bit >>= 1
    return s + leap_days_of(ly)


def solar_to_lunar(y, m, d):
    """公历 → (农历年, 月, 日, 是否闰月) 或 None."""
    if y < 1900 or y > 2100:
        return None
    rest = days_from_civil(y, m, d) - days_from_civil(1900, 1, 31)
    if rest < 0:
        return None
    ly = 1900
    while True:
        yd = year_days_of(ly)
        if rest < yd:
            break
        rest -= yd
        ly += 1
        if ly > 2100:
            return None
    leap = leap_month_of(ly)
    lm, is_leap = 1, False
    for ord_ in range(1, 13):
        md = month_days_of(ly, ord_)
        if rest < md:
            lm, is_leap = ord_, False
            break
        rest -= md
        if leap == ord_:
            ld = leap_days_of(ly)
            if rest < ld:
                lm, is_leap = ord_, True
                break
            rest -= ld
        if ord_ == 12:
            return None
    return ly, lm, rest + 1, is_leap


def lunar_text(y, m, d):
    ld = solar_to_lunar(y, m, d)
    if not ld:
        return ""
    return ("闰" if ld[3] else "") + LM_CHARS[ld[1] - 1] + "月" + LD_CHARS[ld[2] - 1]


def weekday_zh(y, m, d):
    import datetime
    return WEEKDAYS[datetime.date(y, m, d).weekday()]
