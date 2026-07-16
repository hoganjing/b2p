# book-to-podcast (portable skill)

从 WorkBuddy 用户技能目录提取出的可移植副本。把整个 `book-to-podcast-skill/`
文件夹放到目标机器的技能目录即可使用：

- 用户级：`~/.workbuddy/skills/book-to-podcast/`（所有项目可用）
- 项目级：`<workspace>/.workbuddy/skills/book-to-podcast/`（仅该项目）

> `~` 在 Windows 上是 `C:\Users\<你的用户名>\.workbuddy`。
> 文件夹名必须是 `book-to-podcast`（与 SKILL.md 里的 name 一致）。

## 目录结构
```
book-to-podcast/
├── SKILL.md                 # 技能主入口（流程、约束、用法）
├── references/
│   ├── style_brief.md       # 口播稿风格手册（按书改编）
│   └── workflow.md          # 端到端工作流 & 坑位清单
└── scripts/
    ├── apis.yaml            # ⚠ 已清空密钥，需回填（见下）
    ├── fish_tts.py          # 单 API 的 Fish Audio SOCKS5 流式脚本
    ├── make_tts.py          # 从 .md 生成干净 .tts.txt
    ├── tts_run.py           # 多 API 抽象引擎入口
    ├── watchdog.py          # 后台/并行任务卡死看门狗
    ├── tts_engine/          # provider 抽象包（fish/edge/mimo/voicedesign/voiceclone）
    └── anchor/              # 两段预铸锚点音频（mp3，声线资产，跨项目可复用）
        ├── anchor_voice.mp3
        └── anchor_alt_ch01.mp3
```

## ⚠ 复原 API 密钥（重要）
原 `apis.yaml` 里的真实密钥已被剔除，改为占位符。**在目标机器上二选一：**
1. 直接编辑 `scripts/apis.yaml`，把 `YOUR_FISH_AUDIO_API_KEY` /
   `YOUR_MIMO_API_KEY` 换成你的真实 key；
2. 或运行时不填 key，改用环境变量 / CLI：
   - Fish：`FISH_API_KEY` / `FISH_VOICE_ID` / `FISH_SOCKS`
   - Mimo：环境变量 `TTS_PROVIDER=mimo` + 在 apis.yaml 对应 profile 填 key

选择优先级：CLI `--api` > 环境变量 `TTS_PROVIDER` > apis.yaml 的 `default`。

## 依赖安装
从 `scripts/` 目录运行：
```
pip install pyyaml edge-tts "httpx[socks]" aiohttp_socks imageio-ffmpeg
```
> - Fish / Mimo 联网走本地 SOCKS5 代理时，Bash 需 `dangerouslyDisableSandbox: true`。
> - voice design / voice clone 模式出 mp3 需 `imageio-ffmpeg`（自带 ffmpeg 转码）。

## 用法速查
```
python tts_run.py                                            # 默认 profile 跑全部 *.tts.txt
python tts_run.py --api edge_tts                            # 微软免费音
python tts_run.py --api mimo-v2.5-tts-voiceclone            # 音色复刻·默认声线(钉死)
python tts_run.py --api mimo-v2.5-tts-voiceclone --anchor path/to/xxx.mp3
python tts_run.py --list                                    # 列出可用 profile
```

## 与源技能的差异（本副本已修正）
- 密钥已清空（安全）。
- SKILL.md 锚点文件名由 `*.wav` 修正为真实的 `*.mp3`（apis.yaml 与磁盘文件本就是 mp3）。
- style_brief.md 里的示例路径从绝对路径改为 `<项目目录>/...` 占位，便于迁移。
