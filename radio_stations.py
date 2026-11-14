# -*- coding: utf-8 -*-
"""radio_stations — 内置电台表 (直连流, 无中继).

20 台 = 家庭版 radio-relay 同源精选 (fip 需代理已剔除), 全部为公开流 URL:
qingting.fm / cnr.cn / bbcmedia / streamguys / tunein / wnyc / wbez / kexp / rp.
国际台 2026-10-25/11-02 家宽 ffmpeg 实测直连可达; 设备在同一网络等价可用.
"""

STATIONS = [
    # 中文
    {"key": "szjt", "name": "深圳交通广播", "url": "http://ls.qingting.fm/live/1272.m3u8", "freq": 1062},
    {"key": "szxw", "name": "深圳新闻广播", "url": "http://ls.qingting.fm/live/1270.m3u8", "freq": 898},
    {"key": "szfy", "name": "深圳飞扬971", "url": "http://satellitepull.cnr.cn/live/wxszfy971/playlist.m3u8", "freq": 971},
    {"key": "szsh", "name": "深圳生活广播", "url": "http://ls.qingting.fm/live/1273.m3u8", "freq": 942},
    {"key": "zgzs", "name": "央广中国之声", "url": "http://ngcdn001.cnr.cn/live/zgzs/index.m3u8", "freq": 0},
    {"key": "jjzs", "name": "央广经济之声", "url": "http://ngcdn002.cnr.cn/live/jjzs/index.m3u8", "freq": 0},
    {"key": "gdyy", "name": "广东音乐FM99.3", "url": "http://ls.qingting.fm/live/1260.m3u8", "freq": 993},
    {"key": "gdyz", "name": "羊城交通FM105.2", "url": "http://ls.qingting.fm/live/1262.m3u8", "freq": 1052},
    {"key": "gdxw", "name": "广东新闻FM91.4", "url": "http://ls.qingting.fm/live/1254.m3u8", "freq": 914},
    {"key": "zjjs", "name": "珠江经济FM97.4", "url": "http://ls.qingting.fm/live/1259.m3u8", "freq": 974},
    {"key": "szzy", "name": "深圳综艺广播", "url": "http://ls.qingting.fm/live/1271.m3u8", "freq": 0},
    # 英文
    {"key": "bbcws", "name": "BBC World Service", "url": "http://stream.live.vc.bbcmedia.co.uk/bbc_world_service", "freq": 0},
    {"key": "npr", "name": "NPR News", "url": "https://npr-ice.streamguys1.com/live.mp3", "freq": 0},
    {"key": "cnn", "name": "CNN 新闻", "url": "https://tunein.cdnstream1.com/2868_96.mp3", "freq": 0},
    {"key": "cnni", "name": "CNN 国际台", "url": "https://tunein.cdnstream1.com/3519_96.aac", "freq": 0},
    {"key": "msnbc", "name": "MSNBC 新闻", "url": "https://tunein.cdnstream1.com/3511_96.mp3", "freq": 0},
    {"key": "wnyc", "name": "WNYC 纽约公共", "url": "https://fm939.wnyc.org/wnycfm", "freq": 0},
    {"key": "wbez", "name": "WBEZ 芝加哥公共", "url": "https://stream.wbez.org/wbez128.mp3", "freq": 0},
    {"key": "kexp", "name": "KEXP 独立音乐", "url": "https://kexp-mp3-128.streamguys1.com/kexp128.mp3", "freq": 0},
    {"key": "rp", "name": "Radio Paradise", "url": "https://stream.radioparadise.com/mp3-128", "freq": 0},
]
