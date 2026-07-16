---
name: book-to-podcast
description: Transform an OCR'd book or any long text into broadcast-ready Chinese podcast scripts (per-chapter .md plus clean .tts.txt dual files) and generate TTS audio via a multi-API abstraction (Fish Audio S2.1 Pro through SOCKS5 proxy, Microsoft Edge-TTS free, or Xiaomi mimo TTS). Use when the user wants to turn a book/PDF/text into a podcast, audiobook, or spoken-word audio — especially when handling OCR noise, faithful adaptation without simplification, chapter compression, dual-file TTS prep, or the TTS pipeline (chunked decoding, proxy routing, multi-provider switching, sandbox pitfalls).
agent_created: true
---

# Book → Podcast Pipeline

Turn a long text (typically an OCR'd book markdown) into a spoken-word podcast in two phases. Both phases are deterministic and script-assisted; the judgment work is in Phase 1 (writing faithful, broadcast-quality scripts).

## When to use
- "把这本书做成播客 / 有声书 / 讲稿"
- "扫描的 PDF / OCR 稿转成口播稿"
- "生成 TTS 语音"（尤其 Fish Audio + SOCKS5 代理场景）
- Any task combining faithful text adaptation with audio synthesis.

## Phase 1 — Scripts (the human-judgment part)
Goal: one `.md` per chapter, broadcast style, faithful to source, plus a clean `.tts.txt`.

1. **If the source is a raw PDF (text-layer or scanned), do PDF ingestion + image understanding first** — see `references/workflow.md` 阶段〇 (render scanned pages → visual page-read → extract chart content into oral descriptions). Skip this only if you already have clean OCR markdown.
2. Read `references/style_brief.md` for the exact structure, quality rules, and length targets. Adapt the brief per book (title, author, sample chapters) but keep the rules.
2. **Sample-first**: write 2–3 sample chapters, get user sign-off on voice/structure before full generation.
3. Generate per-chapter `.md` (use sub-agents for bulk, but verify real output by char-count, not by receipt).
4. **Hard rule (user mandate)**: agent additions (exercises, examples) are *additive only* — never compress or simplify the source to make room. Label added blocks with `（主播延伸 · …）` and anchor each to a specific source argument. Short chapters stay honestly short; never pad.
5. Run `scripts/make_tts.py` from the output dir to strip bracket-labels and emit `*.tts.txt`.
6. For over-long chapters, compress to ≤30 min *without losing any argument* — keep every distinct point, cut only redundancy; back up the full version first (see `references/workflow.md`).

## Phase 2 — TTS audio (Fish Audio S2.1 Pro)
Goal: one `.mp3` per `.tts.txt`, pure voice, natural speed.

1. Place `scripts/fish_tts.py` + a `.env` (keys below) + the `*.tts.txt` files in one directory; run from that directory.
2. `.env` must contain: `FISH_API_KEY=...`, `FISH_VOICE_ID=...`, `FISH_SOCKS=127.0.0.1:10808`.
3. Run `python fish_tts.py` (no args = all `*.tts.txt`; or pass chapter names for batching). It handles SOCKS5 via the httpx proxy, auto chunked decoding, and — crucially — **auto-splits long text into ≤1500-字 chunks** so a sustained connection never gets dropped by the proxy; each chunk has per-chunk retry + backoff, and a chunk that keeps failing is recursively halved (sub-split self-heal) so one bad segment can't kill a whole episode. It also does integrity check, auto-retry, and resume (cached chunks live in `audio/_chunks/`). Requires `pip install "httpx[socks]"`. See `references/workflow.md` for the critical pitfalls (sandbox bans `os.remove`, proxy/crash observability, background-path quirk, long-lived-stream stalls — fixed via httpx `read=120s` timeout + input chunking + retry, NOT "keep session active").
4. Verify each mp3: bytes/char ≥ ~2500, valid MP3 frame header, and `dechunk` leaves size unchanged (proves no chunk markers leaked in).

### Phase 2b — 多 API 抽象引擎（tts_engine，推荐）

把"用哪个 TTS"抽象成可配置的多 provider，使用细节与生成逻辑解耦。**新增一个不同调用方式的 API，只需写一个 provider 子类 + 在 `apis.yaml` 加一条 profile，编排器一行都不用改。**

目录：`scripts/tts_engine/`（包）+ `scripts/tts_run.py`（入口）+ `scripts/apis.yaml`（配置）。

- **结构（三层解耦）**：
  - 配置层 `apis.yaml`：多 profile + `default`。
  - 选择层：CLI `--api <name>` > 环境变量 `TTS_PROVIDER` > `default`。
  - API 无关编排器 `tts_engine/engine.py`：`split_text`(按句 ≤1500 字) → 逐块 `provider.synth_chunk(text)->bytes` → 重试 + 对半切自愈 → `provider.is_valid` 校验 → 断点续跑(`provider.ext` 决定扩展名) → 写音频。致命错误(`TTSFatalError`，如 4xx 参数/认证/内容审核)立即放弃整块、不重试也不对半切；可重试错误(5xx/网络)走指数退避。
  - 统一接口 `TTSProvider.synth_chunk(text)->bytes`，各 provider（`fish_audio.py` / `edge_tts.py` / `mimo.py` / `mimo_voicedesign.py` / `mimo_voiceclone.py`）内聚自己的鉴权头、请求体、流式/代理等细节。
- **内置 provider**：
  - `fish_audio`：SOCKS5 代理 + `api-key`/voice；**模型名必须放 HTTP 头 `model`**（放 body 会被回退到付费模型返回 402）。
  - `edge_tts`：微软免费、无需 key；`voice: zh-CN-XiaoxiaoNeural` 等。
  - `mimo`：小米 `mimo-v2.5-tts`，**`api-key` 鉴权**（已配 key），endpoint `https://api.xiaomimimo.com/v1/chat/completions`；合成文本放 `role:assistant`，`audio.format` 支持 `mp3`/`wav`/`pcm16`，**默认 mp3** 交付 `.mp3`。支持采样超参 `temperature`(模型默认0.6)/`top_p`(默认0.95)，`apis.yaml` 已默认调低至 `0.3`/`0.5` 以减少跨切块韵律方差（实测 temp=0 仍非确定性，故仅作改善、非锁死）。provider 内置按官方错误码分类的指数退避重试（429/500/502/503/504 及网络异常退避重试、读 `Retry-After` 头；4xx 立即判死不重试）。
  - `mimo_voicedesign`：小米**语音设计**模式 `mimo-v2.5-tts-voicedesign`，复用 mimo 的 endpoint/鉴权/响应；**无固定音色名、不用 `audio.voice`**，音色由 `apis.yaml` 的 `voice_design`（自然语言描述性别/年龄/音色/语气/语速）决定，写入 `messages[user].content`（`user` 不进语音），合成文本放 `assistant`。**该模型 API 本身只出 `wav`/`pcm16`**（请求 mp3 会被忽略、照样回 wav）；为统一交付 mp3，provider 在拿到 wav 后用本地 ffmpeg（`imageio-ffmpeg` 自带二进制）转码为 mp3，**默认 `mp3` 交付 `.mp3`**（与全书其余章节一致）。同样支持 `temperature`/`top_p`，`apis.yaml` 已默认调低至 `0.3`/`0.5`。适合想要「自定义声线」而非预设音色的场景。**注意：voice_design 是非确定性采样——同一描述词每次生成音色/韵律都略有不同，不适合直接切分长文本（每段像换了人/语气）。**
  - `mimo_voiceclone`：小米**音色复刻（voice clone）**模式 `mimo-v2.5-tts-voiceclone`，是 voice_design 之后**钉死声线的正确方式**。复用 mimo 的 endpoint/鉴权/响应；**不传声线描述**，改用 `apis.yaml` 的 `anchor`（一段参考音频路径，mp3/wav、<10MB）以带 MIME 前缀的 Base64 写入 `audio.voice`，合成文本放 `assistant`。**全书所有切块共用同一段锚点音频 → 说话人身份（音色）跨章节一致**，彻底解决 voice_design 切分漂移问题。同样 API 只出 `wav`/`pcm16`，provider 本地 ffmpeg 转码为 `mp3` 交付 `.mp3`。**标准链路**：① 先用 `mimo_voicedesign` 铸一段锚点（`format:wav`）落盘为 `anchor/anchor_voice.mp3`；② 全书跑批改用 `--api mimo-v2.5-tts-voiceclone`。**本技能已打包两段预铸锚点（声线资产，与具体项目无关，任何播客可复用）**：`anchor/anchor_voice.mp3`（**默认声线**，用户 approved 的 vd_7 综合版样音）、`anchor/anchor_alt_ch01.mp3`（**备选声线**，取自《脑科学》ch01 前 60 秒）。开箱即用、无需重新铸造；换声线时用 `mimo-v2.5-tts-voiceclone`（默认）或 `mimo-v2.5-tts-voiceclone-alt`（备选）切换，也可用 `--anchor <path>` 指向任意新锚点。
- **用法**（从 `scripts/` 目录运行，需 `pyyaml`+`edge-tts`+`aiohttp_socks`，Fish/mimo 还需 `httpx[socks]`；**voice design 出 mp3 还需 `imageio-ffmpeg`**（自带 ffmpeg 转码），且 `dangerouslyDisableSandbox:true` 才能联网/代理）：
  - `python tts_run.py` —— 用 default profile 跑全部 `*.tts.txt`
  - `python tts_run.py --api edge_tts` —— 临时切到免费微软音
  - `python tts_run.py --api mimo 15-细胞的社会联系` —— 单章 + 指定 provider
  - `python tts_run.py --api mimo-v2.5-tts-voicedesign` —— 语音设计模式（自定义声线，交付 .mp3）
  - `python tts_run.py --api mimo-v2.5-tts-voicedesign --voice "一位五十多岁的女教授，声音温润笃定，语速舒缓"` —— 临时改音色设计描述
  - `python tts_run.py --api mimo-v2.5-tts-voiceclone` —— 音色复刻模式·默认声线（vd_7，用锚点音频钉死声线，全书一致，交付 .mp3）
  - `python tts_run.py --api mimo-v2.5-tts-voiceclone-alt` —— 音色复刻模式·备选声线（《脑科学》ch01 声线）
  - `python tts_run.py --api mimo-v2.5-tts-voiceclone --anchor path/to/xxx.mp3` —— 临时指定任意锚点
  - `python tts_run.py --list` —— 列出可用 profile
- **注意**：`fish_tts.py` 仍是单 API 的 SOCKS5 流式脚本（背景批量任务用），与 `tts_engine` 二选一即可；两者产出同名文件，勿对同一批章节同时跑以免写冲突。

## Critical reminders
- **Dependency**: `fish_tts.py` needs `httpx` **with SOCKS support** — install via `pip install "httpx[socks]"` (plain `httpx` alone raises `socksio not installed`).
- **Never call `os.remove`/`os.unlink`** in this environment — WorkBuddy's Python wraps deletion in a "safe-delete" shim that moves to a (unavailable) recycle bin and raises `OSError`. Overwrite files with `'wb'` instead.
- Fish Audio returns **chunked** HTTP; httpx auto-decodes it (a defensive `dechunk` is also built in), so mp3s come out clean.
- Bash needs `dangerouslyDisableSandbox: true` to reach the local SOCKS5 proxy.
- **Run TTS as a single sequential foreground process** (pass all chapters at once). The real failure mode is NOT "session idle kills the process" (proven false) nor "session idle suspends egress" (proven false for short connections). It is a **long-lived streaming connection being dropped/stalled by the proxy or upstream** — a short request just fails and retries, but a 30s–2min TTS stream can silently stall (no EOF, no error, no exit). Mitigation: the script sets an httpx `read` timeout + `MAX_RETRY` auto-retry, so a stalled stream raises and re-runs; keep `read` timeout modest (≤120s) so stalls surface fast. Single-process foreground is still recommended for simplicity.
- **Watchdog for parallel/background runs**: `scripts/watchdog.py` tails `gen*.log` (progress) + the `<log>.pid` file (process liveness) and flags the silent failure mode — a stalled stream that never exits. It prints `运行中 / 完成 / 失败 / ⚠ 卡死(进程在但无进展) / ⚠ 异常退出(无完成标记)`. Run `python watchdog.py --once` for a snapshot, or `python watchdog.py --interval 15` to poll; use `--stale 180` to tune the stall threshold. See `references/workflow.md` §2.6.
