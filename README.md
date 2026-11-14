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
| 电台 | 20 台内置直连流 (中/英), 12 条频谱 VU | qingting.fm / cnr.cn / BBC / NPR… |
| 行情 | 自选股+指数+贵金属, 分时/250日K | 腾讯 qt.gtimg.cn |
| 新闻 | 7 个中文 RSS 源 | IT之家 / 少数派 / Solidot… |
| 工具 | 汇率 / 单位换算 / 万年历 / 双色球 / 计时器 / 音乐 | open.er-api.com / 中彩官网 / QQ·网易直连 |

音乐 app: 本地曲库 (mp3/flac/wav) + QQ 音乐 / 网易云**直连协议**
(musrc.py 自实现 weapi/eapi, 扫码绑定, 歌词走 LRCLIB+网易兜底), 输出仅本机。

## 运行

```bash
# 设备 (Debian, 需 pygame/ffmpeg/cryptography/segno/qqmusic-api)
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
