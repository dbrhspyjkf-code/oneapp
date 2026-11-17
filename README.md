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
| 新闻 | 7 个中文 RSS 源 | IT之家 / 少数派 / Solidot… |
| 工具 | 汇率 / 单位换算 / 万年历 / 双色球 / 计时器 / 音乐 / 电视 / 日程 / 设置 / 门户 | open.er-api.com / 中彩官网 / QQ·网易直连 / 公共直播源 / captive 门户 |

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

门户 app (工具第 10 卡, 0 键直开): 酒店等 captive WiFi 的**免浏览器认证** —
自动探针 (`generate_204`, 国内可达优先) 判定是否被门户拦截, 302 跳转/DNS 劫持
都认; 拉门户页解析 `<form>` (hidden token 全收, GBK 自适应, https 自签证书不验),
三层键盘 + 拼音 IME 填房号/账号, POST 提交后**自动复探**裁决是否放行并 OSD 报告;
无表单门户 (token 在 URL) 自动 GET 一次即过。认证值按门户 host 记忆
`portal.json` (0600), 下次免重填。协议层 `portal.py` 纯标准库。

电视 app (工具第 7 卡): **95 个实测可用公共直播频道** (★收藏页签 5 键收/取消) (央视/卫视/地方/国际
四分组, 源自官方 cardputer-zero-iptv 的 public-live.m3u 全量体检后取稳定无
短时效 token 的 95 条, 见 `tv_channels.py`)。单 ffmpeg 进程直推面板: HLS →
`fps=12,scale=320:170` → rgb24 管道 + 音频同进程 pulse (播放链移植其
panel.py 方案); 有 bcm2835-codec-decode 时自动 H.264 硬解 (h264_v4l2m2m),
不出画自动回退软解。每频道多路镜像, 失败自动换下一路, 全尽才报无法播放. 键位: 列表 `←→` 分组 (★收藏在最前) `↑↓` 选台 `Enter` 播 `5` 收藏;
播放中 `↑↓`(F/X) 换台、`Esc` 回列表; 断流/起播超时自动回列表报文案
(公共源单台失效属正常, 换台即恢复)。上次分组/光标记忆 `tv_last.json`,
诊断轨迹 `/tmp/oneapp_tv.log`。

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
`Fn+S/D` 音量± · 主页长按 `Esc` 1s 退出。

## 数据状态

- 想法/自选股/主题/音乐会话: `~/.local/share/oneapp/`
- 音乐凭据 (QQ musickey / 网易 MUSIC_U): 同目录, 0600 权限

## License

MIT
