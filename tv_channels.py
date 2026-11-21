# -*- coding: utf-8 -*-
"""tv_channels — 内置电视直播源 (公网 HLS 直连, 无中继).

每频道多路镜像: 播放失败自动换下一路 (dash.py 频道级容灾),
全部失败才报"无法播放". 2026-11-21 设备家宽全量体检 (208 URL 并行
探测 + 死源 ffmpeg 抓帧验台标) 后重排: 主路 = 当日实测最快活源,
老快照 (2026-11-14 public-live.m3u) 里已劣化/阵亡的整主机剔除 —
124.228.160.40 (央视主路) 连拒 / aptv 101.35.240.114 全线 404
(UA_BY_HOST 随之清空留占位) / 153.0.171.163 404 / 0472.org 404 /
139.227.* 超时. 新主路靠 txiptv 标准编号跨活主机拼接 (0001_1=CCTV1
… 0013_1=CCTV13, 每路已抓帧核验台标非同编号错台). 兜底 = 王小二聚合
(173.208.212.130, 探针慢但真机 ffmpeg ~5s 出画). 无任何活源频道已裁:
内蒙古/江西/湖北卫视, 乐至综合, 山东教育; CCTV5+/北京/安徽 仅剩王小二
单路 (今日拒连属抖动, 留位观察). 公共直播源单台失效属正常, 换台即恢复.
"""

UA_BY_HOST = {}                       # aptv 转发源全线 404 下线; 留空占位

GROUPS = ["央视", "卫视", "地方", "国际"]

# 央视 (19)
CHANNELS = [
    ("CCTV1", "央视", [
        "http://124.90.45.52:9901/tsfile/live/0001_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://221.226.51.220:50081/newlive/live/hls/1/live.m3u8",
        "http://119.39.9.8:9901/tsfile/live/0001_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://173.208.212.130:8181/720p/cctv1.m3u8",
    ]),
    ("CCTV2", "央视", [
        "http://124.90.45.52:9901/tsfile/live/0002_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://119.39.9.8:9901/tsfile/live/0002_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://36.136.38.87:9901/tsfile/live/0002_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://173.208.212.130:8181/720p/cctv2.m3u8",
    ]),
    ("CCTV3", "央视", [
        "http://119.39.9.8:9901/tsfile/live/0003_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://113.57.140.161:10081/newlive/live/hls/3/live.m3u8",
        "http://173.208.212.130:8181/720p/cctv3.m3u8",
    ]),
    ("CCTV4", "央视", [
        "http://124.90.45.52:9901/tsfile/live/0004_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://36.136.38.87:9901/tsfile/live/0004_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://119.39.9.8:9901/tsfile/live/0004_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://173.208.212.130:8181/720p/cctv4.m3u8",
    ]),
    ("CCTV5", "央视", [
        "http://119.39.9.8:9901/tsfile/live/0005_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://221.226.51.220:50081/newlive/live/hls/5/live.m3u8",
    ]),
    ("CCTV5+", "央视", [
        "http://173.208.212.130:8181/720p/cctv5p.m3u8",
    ]),
    ("CCTV6", "央视", [
        "http://124.90.45.52:9901/tsfile/live/1017_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://173.208.212.130:8181/720p/cctv6.m3u8",
    ]),
    ("CCTV7", "央视", [
        "http://1.24.39.180:9003/hls/7/index.m3u8",
        "http://173.208.212.130:8181/720p/cctv7.m3u8",
    ]),
    ("CCTV8", "央视", [
        "http://119.39.9.8:9901/tsfile/live/0008_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://173.208.212.130:8181/720p/cctv8.m3u8",
    ]),
    ("CCTV9", "央视", [
        "http://119.39.9.8:9901/tsfile/live/0009_1.m3u8?key=txiptv&playlive=0&authid=0",
        "http://173.208.212.130:8181/720p/cctv9.m3u8",
    ]),
    ("CCTV11", "央视", [
        "http://119.39.9.8:9901/tsfile/live/0011_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://124.90.45.52:9901/tsfile/live/0011_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://36.136.38.87:9901/tsfile/live/0011_1.m3u8?key=txiptv&playlive=1&authid=0",
    ]),
    ("CCTV12", "央视", [
        "http://74.91.26.218:82/live/cctv12hd.m3u8",
        "http://124.90.45.52:9901/tsfile/live/0012_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://36.136.38.87:9901/tsfile/live/0012_1.m3u8?key=txiptv&playlive=1&authid=0",
    ]),
    ("CCTV13", "央视", [
        "https://event.pull.hebtv.com/jishi/cp1.m3u8",
        "http://124.90.45.52:9901/tsfile/live/0013_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://119.39.9.8:9901/tsfile/live/0013_1.m3u8?key=txiptv&playlive=1&authid=0",
    ]),
    ("CCTV14", "央视", [
        "https://event.pull.hebtv.com/jishi/cp2.m3u8",
        "http://173.208.212.130:8181/720p/cctv14.m3u8",
    ]),
    ("CCTV15", "央视", [
        "http://36.136.38.87:9901/tsfile/live/0015_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://173.208.212.130:8181/720p/cctv15.m3u8",
    ]),
    ("CCTV16", "央视", [
        "http://58.56.162.102:4466/newlive/live/hls/17/live.m3u8",
        "http://173.208.212.130:8181/720p/cctv16.m3u8",
    ]),
    ("CGTN法语", "央视", [
        "http://espanol-liveali.cgtn.com/hls/LSveOGBaBw41Ea7ukkVAUdKQ220802LSTexu6xAuFH8VZNBLE1ZNEa220802cd.m3u8",
    ]),
    ("CGTN英语", "央视", [
        "http://english-livetx.cgtn.com/hls/yypdyyctzb_hd.m3u8",
        "https://english-livebkali.cgtn.com/live/encgtn_0.m3u8",
    ]),
    ("CGTN记录", "央视", ["https://english-livetx.cgtn.com/hls/yypdjlctzb_hd.m3u8"]),
]

# 卫视 (26; 内蒙古/江西/湖北 无活源已裁)
CHANNELS += [
    ("东方卫视", "卫视", [
        "http://bp-resource-dfl.bestv.cn/155/3/video.m3u8",
        "http://173.208.212.130:8181/720p/dfws.m3u8",
    ]),
    ("东方卫视4K", "卫视", ["http://bp-resource-dfl.bestv.cn/148/3/video.m3u8"]),
    ("云南卫视", "卫视", ["http://119.39.9.8:9901/tsfile/live/0119_1.m3u8?key=txiptv&playlive=0&authid=0"]),
    ("兵团卫视", "卫视", ["http://218.13.170.98:9901/tsfile/live/0115_1.m3u8?key=txiptv&playlive=1&authid=0"]),
    ("北京卫视", "卫视", [
        "http://173.208.212.130:8181/720p/bjws.m3u8",
    ]),
    ("吉林卫视", "卫视", ["http://49.87.154.246:85/tsfile/live/0116_1.m3u8?key=txiptv"]),
    ("四川卫视", "卫视", ["http://113.25.252.226:9901/tsfile/live/1031_1.m3u8?key=txiptv&playlive=1&authid=0"]),
    ("天津卫视", "卫视", [
        "http://36.136.38.87:9901/tsfile/live/0135_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://173.208.212.130:8181/720p/tjws.m3u8",
    ]),
    ("安徽卫视", "卫视", [
        "http://173.208.212.130:8181/720p/ahws.m3u8",
    ]),
    ("山东卫视", "卫视", [
        "http://l.cztvcloud.com/channels/lantian/SXshengzhou1/720p.m3u8",
        "http://173.208.212.130:8181/720p/sdws.m3u8",
    ]),
    ("山西卫视", "卫视", ["http://36.136.38.87:9901/tsfile/live/0118_1.m3u8?key=txiptv&playlive=1&authid=0"]),
    ("广东卫视", "卫视", [
        "http://113.57.140.161:10081/newlive/live/hls/34/live.m3u8",
        "http://173.208.212.130:8181/720p/gdws.m3u8",
    ]),
    ("广西卫视", "卫视", ["http://36.136.38.87:9901/tsfile/live/0113_1.m3u8?key=txiptv&playlive=1&authid=0"]),
    ("新疆卫视", "卫视", ["http://59.39.89.130:60901/tsfile/live/0125_17.m3u8?key=txiptv&playlive=1&authid=0"]),
    ("江苏卫视", "卫视", [
        "https://stream2.jlntv.cn/sytv/sd/live.m3u8",
        "http://173.208.212.130:8181/720p/jsws.m3u8",
    ]),
    ("河北卫视", "卫视", ["http://event.pull.hebtv.com/jishi/weishi_tingyun.m3u8"]),
    ("浙江卫视", "卫视", [
        "http://ali-m-l.cztv.com/channels/lantian/channel001/1080p.m3u8",
        "http://173.208.212.130:8181/720p/zjws.m3u8",
    ]),
    ("深圳卫视", "卫视", [
        "http://36.136.38.87:9901/tsfile/live/0126_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://173.208.212.130:8181/720p/szws.m3u8",
    ]),
    ("湖南卫视", "卫视", [
        "http://124.90.45.52:9901/tsfile/live/0128_1.m3u8?key=txiptv&playlive=1&authid=0",
        "http://173.208.212.130:8181/720p/hnws.m3u8",
    ]),
    ("甘肃卫视", "卫视", ["http://120.238.84.45:9901/tsfile/live/1079_1.m3u8?key=txiptv&playlive=1&authid=0"]),
    ("贵州卫视", "卫视", [
        "http://124.90.45.52:9901/tsfile/live/0120_1.m3u8?key=txiptv&playlive=1&authid=0",
    ]),
    ("辽宁卫视", "卫视", ["http://124.90.45.52:9901/tsfile/live/0121_1.m3u8?key=txiptv&playlive=1&authid=0"]),
    ("重庆卫视", "卫视", [
        "http://113.25.252.226:9901/tsfile/live/1030_1.m3u8?key=txiptv&playlive=1&authid=0",
    ]),
    ("陕西卫视", "卫视", [
        "http://36.136.38.87:9901/tsfile/live/0136_1.m3u8?key=txiptv&playlive=1&authid=0",
    ]),
    ("青海卫视", "卫视", ["http://124.90.45.52:9901/tsfile/live/0140_1.m3u8?key=txiptv&playlive=1&authid=0"]),
    ("黑龙江卫视", "卫视", ["http://l.cztvcloud.com/channels/lantian/SXyuyao3/720p.m3u8"]),
]

# 地方 (38; 乐至综合/山东教育 无活源已裁)
CHANNELS += [
    ("七台河新闻综合", "地方", ["http://live.qthnews.org.cn:1935/live/live1/800k/tzwj_video.m3u8"]),
    ("东莞新闻综合", "地方", ["https://stream.sun0769.com/dgrtv1/mp4tv1_800/index.m3u8"]),
    ("之江纪录", "地方", ["https://ali-m-l.cztv.com/channels/lantian/channel012/1080p.m3u8"]),
    ("云和新闻综合", "地方", ["http://l.cztvcloud.com/channels/lantian/SXyunhe1/720p.m3u8"]),
    ("哈尔滨娱乐", "地方", ["http://stream.hrbtv.net/ylpd/sd/live.m3u8?zheild"]),
    ("哈尔滨影视", "地方", ["http://stream.hrbtv.net/yspd/sd/live.m3u8?zheild"]),
    ("哈尔滨新闻综合", "地方", ["http://stream.hrbtv.net/xwzh/sd/live.m3u8?zheild"]),
    ("哈尔滨生活", "地方", ["http://stream.hrbtv.net/shpd/sd/live.m3u8?zheild"]),
    ("哈尔滨都市咨询", "地方", ["http://stream.hrbtv.net/zxpd/sd/live.m3u8?zheild"]),
    ("文成新闻综合", "地方", ["http://l.cztvcloud.com/channels/lantian/SXwencheng1/720p.m3u8"]),
    ("昌黎综合", "地方", ["https://jwcdnqx.hebyun.com.cn/live/clzhpd/1500k/tzwj_video.m3u8"]),
    ("江夏新闻综合", "地方", ["http://59.175.226.142:280/gb28181/xwzh.m3u8?zhubd"]),
    ("河北4K", "地方", ["https://event.pull.hebtv.com:443/live/live101.m3u8"]),
    ("浙江国际", "地方", ["https://ali-m-l.cztv.com/channels/lantian/channel010/1080p.m3u8"]),
    ("浙江少儿", "地方", ["https://ali-m-l.cztv.com/channels/lantian/channel008/1080p.m3u8"]),
    ("浙江教科", "地方", ["https://ali-m-l.cztv.com/channels/lantian/channel004/1080p.m3u8"]),
    ("浙江新闻", "地方", ["https://ali-m-l.cztv.com/channels/lantian/channel007/1080p.m3u8"]),
    ("浙江民生", "地方", ["https://ali-m-l.cztv.com/channels/lantian/channel006/1080p.m3u8"]),
    ("浙江经济", "地方", ["https://ali-m-l.cztv.com/channels/lantian/channel003/1080p.m3u8"]),
    ("浙江钱江", "地方", ["https://ali-m-l.cztv.com/channels/lantian/channel002/1080p.m3u8"]),
    ("苏州4K", "地方", [
        "https://live-auth.51kandianshi.com/szgd/csztv4k_hd.m3u8",
        "https://tylive.kan0512.com/norecord/csztv4k_4k.m3u8",
    ]),
    ("苏州文化生活", "地方", ["https://live-auth.51kandianshi.com/szgd/csztv3.m3u8"]),
    ("苏州新闻综合", "地方", ["https://live-auth.51kandianshi.com/szgd/csztv1.m3u8"]),
    ("苏州生活资讯", "地方", ["https://live-auth.51kandianshi.com/szgd/csztv5.m3u8"]),
    ("苏州社会经济", "地方", ["https://live-auth.51kandianshi.com/szgd/csztv2.m3u8"]),
    ("荥经综合", "地方", ["https://m3u8channel-yj.yunxya.com/nmip-media/channellive/channel101140/playlist.m3u8"]),
    ("萧山综合", "地方", ["http://l.cztvcloud.com/channels/lantian/SXxiaoshan1/720p.m3u8"]),
    ("象山新闻综合", "地方", ["http://l.cztvcloud.com/channels/lantian/SXxiangshan1/720p.m3u8"]),
    ("邢台生活", "地方", ["https://jwcdnqx.hebyun.com.cn/live/xtcsshpd/1500k/tzwj_video.m3u8"]),
    ("邢台综合", "地方", ["http://jwplay.hebyun.com.cn/live/xtsrmtzs/1500k/tzwj_video.m3u8"]),
    ("邯郸公共频道", "地方", ["https://jwcdnqx.hebyun.com.cn/live/hdgg/1500k/tzwj_video.m3u8"]),
    ("邯郸科技教育", "地方", ["https://jwcdnqx.hebyun.com.cn/live/hdkj/1500k/tzwj_video.m3u8"]),
    ("金川乡村", "地方", ["http://live.jinchuanrmt.com:90/live/jcxc.m3u8?zsicd"]),
    ("金川新闻综合", "地方", ["http://live.jinchuanrmt.com:90/live/zhxw.m3u8?zsicd"]),
    ("雅安新闻综合", "地方", ["https://play.yunxya.com/channellive/xinwen.m3u8"]),
    ("青川综合", "地方", ["http://qcfile.qcrmt.com/nmip-media/channellive/channel100933/playlist.m3u8"]),
    ("青田电视台", "地方", ["http://l.cztvcloud.com/channels/lantian/SXqingtian1/720p.m3u8"]),
    ("龙泉新闻综合", "地方", ["http://l.cztvcloud.com/channels/lantian/SXlongquan1/720p.m3u8"]),
]

# 国际 (7)
CHANNELS += [
    ("Arirang", "国际", ["https://amdlive-ch01-ctnd-com.akamaized.net/arirang_1ch/smil:arirang_1ch.smil/chunklist_b3256000_sleng.m3u8"]),
    ("CNA", "国际", ["https://d2e1asnsl7br7b.cloudfront.net/7782e205e72f43aeb4a48ec97f66ebbe/index_5.m3u8"]),
    ("RT Doc-Русский（俄语纪录）", "国际", ["https://rt-doc.rttv.com/dvr/rtdru/rtdru1080.m3u8"]),
    ("RT Français（法语）", "国际", ["https://rt-fra.rttv.com/dvr/rtfrance/playlist_4500Kb.m3u8"]),
    ("Trace Sports", "国际", ["https://lightning-tracesport-samsungau.amagi.tv/playlist1080p.m3u8"]),
    ("Wild Earth", "国际", ["https://wildearth-plex.amagi.tv/masterR1080p.m3u8"]),
    ("WionNews", "国际", ["https://d7x8z4yuq42qn.cloudfront.net/index_7.m3u8"]),
]
