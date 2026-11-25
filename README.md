# ONEAPP — CardputerZero 一体化工具箱

M5Stack CardputerZero (320×170) 的 pygame 工具箱 app。**零本地服务依赖**:
全部数据源为公网 API/RSS, 设备直接联网即可运行 (家庭 WiFi / 手机热点均可)。

从家庭版 [zero-dash](https://github.com/dbrhspyjkf-code/zero-dash) 收敛而来:
去掉家庭服务器 (hermes/收音机中继/HA/邮件/照片/油管中继) 依赖, 数据源全部改接公网。

## 功能

| 页面 | 内容 | 数据源 |
|---|---|---|
| 主页 | 时钟+农历 / 天气 / 每日一句 / 新闻跑马灯 | Open-Meteo / hitokoto / RSS×7 |
| 想法 | 键盘速记 (拼音 IME 本地词表), 本地存储 | — |
| 电台 | 20 台内置直连流 (中/英), 12 条频谱 VU, 输出 O 键循环 (本机 / 局域网 AirPlay 音箱) | qingting.fm / cnr.cn / BBC / NPR… |
| 行情 | 自选股+指数+贵金属, 分时/250日K | 腾讯 qt.gtimg.cn |
| 新闻 | 中文 RSS 源 (`S` 源设置: 显/隐 · 加/改/删自定义源 (标题+地址, 地址输入保尾段显示), kv 云同步) | 中新网 / IT之家 / 少数派 / Solidot / 自定义 RSS… |
| 工具 | 汇率 / 单位换算 / 万年历 / 彩票(双色球/大乐透) / 计时器 / 音乐 / 电视 / 日程 / 设置 | open.er-api.com / 中彩官网 / 500 彩票网 / QQ·网易直连 / 公共直播源 |

音乐 app: 本地曲库 (mp3/flac/wav) + QQ 音乐 / 网易云**直连协议**
(musrc.py 自实现 weapi/eapi, 扫码绑定, 歌词走 LRCLIB+网易兜底);
输出 7 键循环 本机→AirPlay (与电台各自独立记忆 `mu_out.json`)。

电台/音乐 AirPlay 输出: O (电台) / 7 (音乐) 键循环 本机→局域网 AirPlay 音箱
(mDNS 自动发现,
pyatv RAOP 直推, 台名进音箱元数据)。AirPlay 播放中 Fn+S/D/A 音量±/静音
直达当前音箱 (同 RTSP 会话 SET_PARAMETER, 音量记忆 `raop_vol.json`;
未播放时回落本机并在 OSD 注明 "本机")。设备需 `pip install --user
--break-system-packages pyatv==0.18.0 srptools==1.0.1`;
缺库/无设备时自动回落本机播放。

电视 app (工具第 7 卡): **95 个实测可用公共直播频道** (★收藏页签 5 键收/取消) (央视/卫视/地方/国际
四分组, 源自官方 cardputer-zero-iptv 的 public-live.m3u 全量体检后取稳定无
短时效 token 的 95 条, 见 `tv_channels.py`)。单 ffmpeg 进程直推面板: HLS →
`fps=12,scale=320:170` → rgb24 管道 + 音频同进程 pulse (播放链移植其
panel.py 方案); 有 bcm2835-codec-decode 时自动 H.264 硬解 (h264_v4l2m2m),
不出画自动回退软解。每频道多路镜像, 失败自动换下一路, 全尽才报无法播放. 键位: 列表 `←→` 分组 (★收藏在最前) `↑↓` 选台 `Enter` 播 `5` 收藏;
播放中 `↑↓`(F/X) 换台、`Esc` 回列表; 断流/起播超时自动回列表报文案
(公共源单台失效属正常, 换台即恢复)。上次分组/光标记忆 `tv_last.json`,
诊断轨迹 `/tmp/oneapp_tv.log`。

日程 app (工具第 8 卡): **提醒 + 行程, 本地优先** — `N` 新建 / `E` 编辑 /
`D`(或 `7`) 删除 (确认框) / `Enter` 完成提醒 / `4` 刷新 / `↑↓`(F/X) 选。
改动先写本地待同步队列 (`sched_out.json`), 登录且在线后按序上云 (断网或
未登录照常可用, 之后自动补传); 与车机 car-dash 经云端互通。数据两类:
本机/车机**自建**条目可增删改; iPhone/Mac 提交器快照推来的**来源**条目 —
删除/完成 = 云端隐藏 (重推不复活, 直到来源端自己丢弃), 修改 = 另存一条
自建条目并隐藏原件 (来源端原条目不变)。表单: 类型 / 内容 (拼音 IME,
`/` 中英) / 日期·时间 (`←→` 步进: 日期 ±1 天 · 时间 ±5 分; 或数字直输
`MMDD` / `HHMM`, 时间 1-2 位 = 整点) / 行程再加 结束·地点。

## 运行

```bash
# 设备 (Debian, 需 pygame/ffmpeg/cryptography/segno/qqmusic-api/pyatv)
python3 dash.py            # 直写 /dev/fb_lcd + evdev
# Mac 预览
python3 dash.py --window
# 无头测试 (离线)
python3 test_dash.py       # 全部逻辑测试
RUN_NET=1 python3 -c "import test_dash; test_dash.test_net_live()"  # 在线链路
```

键位: `1-5` 切页 · `Z/C/F/X`(丝印←→↑↓) 翻页 · `t` 换主题(主页) ·
`Fn+S/D` 音量± · 新闻页 `S` 源设置 · 工具·日程 `N/E/D` 新建/编辑/删除 ·
主页长按 `Esc` 1s 退出。

## 数据状态

- 想法/自选股/主题/音乐会话/新闻源表 (`news_feeds.json`): `~/.local/share/oneapp/`
- 音乐凭据 (QQ musickey / 网易 MUSIC_U): 同目录, 0600 权限
- Oneapp 账户 kv 云同步 (noteserver `/v1/kv`, 按账号): 电台收藏 / 自选股 /
  电视收藏 / **新闻源表** (显隐+自定义源, 本地变更即推 · 开机/进页 LWW 拉取)
- 日程 (提醒/行程): 云端缓存 `sched.json` + 待同步操作队列 `sched_out.json`
  (逐条操作, 带归属账号; noteserver `/v1/sched-item|sched-del|sched-done`)

## License

MIT
