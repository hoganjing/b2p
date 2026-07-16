# Book → Podcast —— 详细工作流与踩坑清单

本文件是 `book-to-podcast` skill 的详细补充。SKILL.md 讲"做什么"，这里讲"怎么做 + 所有会咬人的细节"。

---

## 阶段〇：PDF 摄取与图像理解（输入是 PDF 而非现成 OCR markdown 时必读）

本技能的"阶段一"默认输入已经是 OCR 好的 markdown。但真实场景用户常丢来一个 PDF——分两种，处理路径完全不同。**这一层做不好，后面写稿再漂亮也是建立在残缺/错误的原文上。**

### 0.1 先判定 PDF 类型
- **文字层 PDF**：用 PyMuPDF 能直接抽出文本（`page.get_text()` 非空、无明显乱码）→ 走 0.2。
- **扫描 / 图片型 PDF**：抽出来是空白或残缺，每一页本质是一张图 → 走 0.3。

### 0.2 文字层 PDF：直接抽文本
```python
import fitz
doc = fitz.open("book.pdf")
for i, page in enumerate(doc):
    txt = page.get_text()          # 整页文本
    # 按章节标题正则切分，落盘为 per-chapter 草稿
```
抽出的文本仍可能带页码、页眉、OCR 噪点，进阶段一前先按 §1.4.1 的审计脚本过一遍噪点。

### 0.3 扫描 PDF：渲染成图 + 逐页视觉读取
文字层为空时，唯一可靠路径是**把每一页渲染成 PNG，再逐页"看"着读**——既要转录正文，也要理解图里的曲线 / 结构。

```python
import fitz
doc = fitz.open("book.pdf")
for i, page in enumerate(doc):
    pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))   # zoom=1.5 提清晰度
    pix.save(f"_probe/p{1000+i}.png")
```
- **逐页视觉读取**：用 Read 工具（或视觉子代理）打开每张 PNG，产出"该页正文转写 + 图中内容理解"，再拼接成章节草稿。
- **章节页码映射**：先摸清目录，记下"第 N 章 ≈ 第 a–b 页"，把读取任务按章分包，避免一次塞太多页。

### 0.4 上下文长度陷阱（必看，否则会静默失败）
- 一页高清 PNG 的视觉 token 很大，**一次性读 20+ 页会触发模型 `400 input length too long`**。
- 对策：超大章（>19 页）**拆成两段 / 两个子代理各读一半**；并行启动子代理用**小批量（≤2 个）**，避免同时拉起过多导致静默失败（无输出、无进程）。
- 子代理的"已完成"回执不可信——必须用脚本核验真实写入 / 字数。

### 0.5 图片与图表如何处理（核心）
扫描 PDF 里的图（曲线图、机制图、电镜照片）是**信息，不是噪点**。`style_brief.md` 第 5 条把 `<div> 图片块` 当噪点删——那是指 OCR 工具留下的**图占位符**；正确做法是**回到那一页的渲染图，把图"读"出来并写成口语化描述**，而不是删掉占位符了事。
- **定位图**：在渲染页里找到图（图号如"图3-2"），单独视觉读取该区域。
- **理解图**：图题、坐标轴、图例、关键曲线 / 结构、与正文论点的关系。
- **写进口播稿**：把图写成自然语言口语描述，例如"我们书里图3-2 这张机制图讲的是……横轴是时间，纵轴是……实线代表……虚线代表……"——让听众"听得到图"。

### 0.6 在音频里表现图的写法约定（避免被 TTS 剥掉）
- `make_tts.py` 会**整行剥掉**形如 `（……）` 的标签行。图表描述若写成独占一行的 `（图示：图3-2 机制）` 会被直接删掉、读不出图。
- 正确写法：**图表描述作为正文散文直接写**（会被读出）；如需标注图号，用**行内** `（图3-2）` 而非独占一行的标签。行内括号保留，整行标签才剥。
- 图表描述必须**纯中文**（遵循"正文纯中文、外文只进括号注释"硬规则）；图里出现的英文缩写如 ATP 写成 `ATP（腺苷三磷酸）`。

### 0.7 与阶段一的衔接
渲染 + 视觉读取产出的章节草稿，直接进入阶段一的写稿流程（套 `style_brief.md` 结构、跑噪点 / 裸外文审计、`make_tts` 出 `.tts.txt`）。差异仅在于：阶段〇帮你**从 PDF 拿到干净、含图表理解的章节文本**，阶段一负责**把它改写成广播级口播稿**。

---

## 阶段一：OCR 书 → 分章播客讲稿

### 1.1 输入与结构识别
- 源通常是 PaddleOCR / 其他 OCR 产出的 markdown，体积小、几乎无乱码但带格式噪点（`####`、脚注、页码、残缺词、长大写英文、z-lib/archive 痕迹）。
- 先识别书的结构：正文章节 + 非章节内容（编者按 Editor's Note、作者序言 Preface、引言 Introduction、书间插曲如 *A CLEARER PICTURE*）。
  - 决策模板：把"编者按+序言+引言"合成第〇集·导读；把书间插曲做成一个独立"间奏"短篇，插在对应章节之间。
- 用 Glob/Grep 摸清章节标题规律，不要凭空猜。

### 1.2 风格手册（必读 references/style_brief.md）
- 每集结构固定：`标题行（无#号）` → 空行 → `（开场白）` → `（正文）` → `（主播延伸 · 板块名）` → `（收尾与下集预告）`。
- 所有 `（……）` 都是**独立成行**的括号标签，会被 TTS 脚本整行剥掉；正文内也可用 `（小节标题）` 做分段。
- 质量硬规则（用户死命令）：
  1. 原文绝不压缩/简化，每个论点、例子、类比都要讲透。
  2. 发挥是"叠加"不是"替换"；主播延伸是额外加在原文之后。
  3. 主播延伸必须锚定原论点，禁止引入书外理论或编造事实。
  4. 立足全书视角，必要时呼应前文。
  5. 去原书格式噪点（OCR 修正），交稿前跑噪点自查（见 1.4.1）。
  6. **正文必须纯中文，禁止裸露任何外文**；术语/人名/书名/外语原话一律写成"中文（原文）"括号注释，让外文只落在 `（…）` 里。交稿前跑"正文裸外文"自查（见 1.4.1）。
- 篇幅：正常章 4500–5500 中文字（讲透即可，超了不强行砍）；**短章诚实做短集（1500–2500），绝不灌水**。

### 1.3 生成节奏（sample-first）
1. 先写 2–3 集样稿（含导读），给用户审风格/口播感/主播延伸锚定方式。
2. 确认后全量产出。长项目可用多个 sub-agent 并行写各章 `.md`（给每个 agent 完整 style_brief + 明确单章字数预算 + 短章清单）。
3. **重要**：sub-agent 可能返回空回执但文件实际已写入，也可能把"每章 X 字"误读成"几章总和"。**必须用脚本核验真实中文字数，不要信回执**。

### 1.4 双文件 + TTS 预处理
- 每章产出一对：`第N章·译名.md`（人/主播看，含括号标签与主播延伸）+ 干净 `第N章·译名.tts.txt`（喂 TTS）。
- 运行 `scripts/make_tts.py`（与文件同目录）：剥掉整行括号标签、折叠空行，生成 `.tts.txt`，打印中文字数。
- 校验：`.tts.txt` 中不得残留任何 `（…）` 标签或"主播延伸"字样。

### 1.4.1 交稿前自查：OCR 噪点 + 正文裸外文（必跑）
每次交付 `.md` 前，对输出目录跑一遍下面的审计脚本，确认 **noise=0、strayEN=0**（专有名词白名单除外）。这是把"正文必须纯中文 + 去 OCR 噪点"这两条硬规则**变成可验证的门禁**，别只靠肉眼。

根因提醒：历史上正文漏外文，是因为旧规则写的是"英文术语保留"——那会诱导模型把英文摆进正文。现规则已改为"外文只进 `（…）` 括号注释"，但仍**必须**用脚本兜底核验。

```python
# audit.py —— 放输出目录，用托管 python 跑：
# C:\Users\j\.workbuddy\binaries\python\versions\3.13.12\python.exe audit.py
import re, glob, os
BASE = os.path.dirname(os.path.abspath(__file__))
files = sorted(glob.glob(os.path.join(BASE, "*.md")))
NOISE = [r"0777", r"3129900", r"APR \d", r"Public Library", r"Digitized by",
         r"FIRST EDITION", r"# CHAPTER", r"continued on back flap",
         r"Jacket design", r"ALSO BY", r"119847", r"<div"]
# 专有名词白名单：确无通用中文译名、允许在正文以原文出现的名字/地名
WHITE = {"Nora","Feldenkrais","Moshe","Mosh","Broca","Piaget","Kirshner",
         "Jackson","Bahnhofstrasse"}
cjk = re.compile(r"[一-鿿]")
label = re.compile(r"^（[^）]*）\s*$", re.M)          # 整行括号标签
paren = re.compile(r"（[^）]*[A-Za-z][^）]*）")         # 含拉丁字母的括号注释
print(f"{'file':42} {'cjk':>5} {'noise':>5} {'strayEN':>7}")
for f in files:
    t = open(f, encoding="utf-8").read()
    noise = [p for p in NOISE if re.search(p, t, re.I)]
    work = paren.sub("", label.sub("", t))            # 剥掉标签行 + 括号注释后再找裸外文
    stray = sorted({w for w in re.findall(r"[A-Za-z]{2,}", work) if w not in WHITE})
    print(f"{os.path.basename(f):42} {len(cjk.findall(t)):>5} {len(noise):>5} {len(stray):>7}")
    if noise: print("   NOISE:", noise)
    if stray: print("   STRAY:", stray)
```
- **strayEN 命中处理**：逐个判断——是术语/原话→改成"中文（原文）"括号注释；是专有名词→加进 `WHITE`；是漏译→直接译成中文。改完重跑到 0。
- `.tts.txt` 也应一并过（把 glob 改成 `*.tts.txt`），确保剥标签后正文仍纯中文。

### 1.5 长章压缩（用户要求 ≤30 分钟时）
- 先把完整版备份到 `_backup_full_v1/`，便于还原。
- 压缩铁律：作者**每一个独立论点必须保留**，只删冗余同义复述/枝节例子/过渡；
  **不引入书外理论**。目标 ≤6000 中文字（≈24–29 分钟 @200 字/分）。
- 压缩后再次用 make_tts.py 出 `.tts.txt`，复核零标签残留 + 字数 ≤6000。

---

## 阶段二：Fish Audio TTS 生成

### 2.1 前置
- 脚本 `scripts/fish_tts.py` 用 **httpx 传输层**：`pip install "httpx[socks]"`（SOCKS 支持是可选依赖，必须带 `[socks]` 否则报 `socksio` 未安装）。
- 同目录需有 `.env`：
  ```
  FISH_API_KEY=你的key
  FISH_VOICE_ID=你的音色id
  FISH_SOCKS=127.0.0.1:10808
  ```
- 出网被墙时必须走本地 SOCKS5 代理；**Bash 必须 `dangerouslyDisableSandbox: true`** 才能连代理（沙箱直连 api.fish.audio 会 WinError 10060 超时）。
- 端点：`POST https://api.fish.audio/v1/tts`，头 `Authorization: Bearer <KEY>` + `Model: s2.1-pro-free`，体 `{"text":..., "reference_id":VOICE_ID, "format":"mp3", "mp3_bitrate":128}`。
- 运行（本环境用托管 venv）：`C:\Users\j\.workbuddy\binaries\python\envs\default\Scripts\python.exe fish_tts.py 章节名...`；用户本机若已装 httpx 直接 `python fish_tts.py`。

### 2.2 脚本能力（v4，httpx 版）
- **分块解码（防御）**：Fish 返回 `Transfer-Encoding: chunked`，httpx 的 `iter_bytes()` **已自动解码**，写出即纯 mp3；脚本另含 `dechunk()` 兜底，仅当响应意外仍是分块格式时才触发（正常不会）。
- **完整性校验** `is_valid_mp3()`：字节/字 ≥ 2500 **且** 开头是合法 MPEG 帧（`0xFF & 0xE0==0xE0`）或 ID3。不达标覆盖重试（最多 5 次）。
- **断点续跑**：已存在且校验通过则跳过。
- **超时防护**：`httpx.Timeout(connect=10, read=120, write=30, pool=30)`；长连接被代理/上游静默掐断时，120s 无新字节即抛异常 → 被 `MAX_RETRY` 捕获重试，不会无限卡死。
- **死亡可观测**：`atexit` 写退出码 + 顶层 try 记 traceback；配 30s 心跳，任何死法都有交代。
- **逐组合日志**：环境变量 `FISH_LOG=gen.g1.log` 指定日志文件，多组并行不交错。
- **PID 文件**：启动时写 `<日志>.pid`（内容为自身 PID），退出时 `atexit` 尽力删除（失败忽略）；供看门狗交叉验证进程是否真活。
- **零 os.remove**：直接 `'wb'` 写最终文件，校验不过下次重试再覆盖（天然自愈），避开沙箱安全删除钩子。

### 2.3 致命平台坑（务必牢记）
1. **沙箱禁用 `os.remove` / `os.unlink`**：WorkBuddy 的 Python（`sitecustomize.py`）给删除操作装了"安全删除"钩子——先移回收站，而本机沙箱回收站不可用 → **任何删除必抛 `OSError: [safe-delete][SAFE_DELETE_FAIL_CLOSED]` 崩脚本**。本环境写任何脚本都不要调用 `os.remove`，用 `'wb'` 覆盖替代。
2. **分块响应**：v4 用 httpx 已自动解码，正常不会漏标记；但终检仍建议**用 `dechunk` 比对体积**确认无残留（历史教训：早期手写 socket 版漏解码导致全废）。
3. **后台任务路径坑**：`run_in_background` 的 Bash 里，脚本参数用绝对 `/c/Users/.../fish_tts.py` 会被当相对路径拼成 `c:\c\Users\...` → python 找不到文件、瞬间退出。解决：先 `cd` 进目录再用相对名 `fish_tts.py`（python.exe 绝对路径本身可用）。
4. **进程计数假阴性**：`wmic … grep fish_tts` 在带 `FISH_LOG=…` 前缀的命令行下可能匹配为 0。以 `audio/gen.gN.log` 实际写入 + 字节流增长为存活判据。
5. **长连接流才是真凶**（机制已用对照实验两次订正）：后台**进程本身**与会话解耦，空闲杀不掉（实证：A/B/C 纯心跳脚本跑 50+ 分钟、经历多次空闲仍全活、无 SIGNAL/EXIT）。"会话空闲→网络出口挂起"也已被 **diag_net 实验证伪**（空闲 5 分钟里经 127.0.0.1:10808 的短连接一直通，11/12 成功、1 次 SSL 瞬断自愈）。真正死因是 **TTS 这种 30s~2min 的流式长连接**被代理/上游静默掐断——短请求只失败重试，长连接流则卡死（无 EOF、无报错、不退进程），日志心跳（写在读取循环里）骤停、无 `■ 脚本退出` 行、无 traceback，形似"假死"。**不要把"并行数"或"会话空闲"当元凶**——变量是"长连接是否被代理/上游掐断"，靠 httpx 超时 + 重试兜底。

### 2.4 运行策略与配额
- **最稳跑法：单进程顺序前台跑**。把整批章节作为参数一次传给 `fish_tts.py`（如 `fish_tts.py 00-导读… 01-人的能力… … 19-有出路吗`），脚本串行生成 + 自动重试 + 断点续跑，一个前台进程从头跑到尾最不易被打断。
- **若拆后台多进程并行**：进程空闲不会被回收（已证伪"空闲回收"），但 TTS 是 30s~2min 的流式长连接，代理/上游可能静默掐断长连接——短请求只失败重试，长连接会卡死。兜底靠 **httpx 超时 + MAX_RETRY 自动重连**（见 2.1，建议 read 超时 ≤120s 让卡死快速抛错），不要把希望寄托在"保持会话活跃"上（"会话空闲挂起出口"已被 diag_net 证伪）。实测 3 路并发在活跃会话下稳定；5 路同毫秒建连曾偶发丢 1（SSL 竞态），串行开连不再竞态。
- 免费档额度有限且会过期，尽量一次跑完，避免反复整体重来。
- 跑完做逐集终检（2.2 校验 + dechunk 体积比对）；失败集用同脚本低并发补跑（断点续跑自动跳过已完成）。

### 2.6 看门狗监控（进程成活 + 日志变化，以日志为准）

后台任务只要退出（正常/异常/被 kill）harness 都会发结束通知；**唯独「卡死在阻塞 socket、永不退出」才沉默**——你干等半天以为它在跑。配套 `watchdog.py` 用「日志多久没新进展」抓出这种挂死，并用 PID 文件交叉验证进程是否真活。

判定（日志为主，进程为辅）：
- 日志含 `■ 脚本退出（退出码 0）` → `完成`
- 含 `■ 脚本退出（退出码 N≠0）` → `失败(退出码N)`
- 有 PID 文件但进程已死、且无完成标记 → `⚠ 异常退出(无完成标记)`
- 进程在、但日志 > `--stale` 秒无变化 → `⚠ 卡死(进程在但无进展)`
- 无 PID/进程未知、日志 > `--stale` 秒无变化 → `⚠ 卡死/已结束无标记`
- 其余 → `运行中`

用法（脚本放项目 `podcast/` 目录，日志默认在同级 `audio/`）：
```bash
# 单次快照
python watchdog.py --once
# 循环监控（每 15s 刷新，Ctrl+C 退出），卡死阈值设为 180s
python watchdog.py --interval 15 --stale 180
# 指定目录/通配
python watchdog.py --dir audio --pattern "gen*.log" --once
```
表头：`日志 | PID活/死 | 最后活动时长 | 状态 | 最后进度行`。3 路并行跑 TTS 时开着它，一眼看清谁在跑、谁挂了。

### 2.7 多 API 抽象引擎（tts_engine，推荐）

把「用哪个 TTS」抽象成可配置多 provider，使用细节与生成逻辑解耦。文件在 `scripts/tts_engine/` + `scripts/tts_run.py` + `scripts/apis.yaml`（详见 SKILL.md「Phase 2b」）。

核心要点：
- **三层解耦**：`apis.yaml`(多 profile+default) → 选择(`--api`/`TTS_PROVIDER`/`default`) → `engine.py`(切句/重试/对半切自愈/校验/续跑) → 统一接口 `TTSProvider.synth_chunk(text)->bytes` → 各 provider 内聚调用细节。
- **新增 API** = 写一个 `providers/<name>.py` 子类（实现 `synth_chunk` + 可选 `is_valid`/`ext`/`close`）+ 在 `apis.yaml` 的 `apis:` 下加一条 `type` 匹配的 profile；编排器零改动。
- **内置 provider 的坑**（已写进代码，记录备查）：
  - `fish_audio`：模型名必须放 HTTP 头 `model`（不是 body），否则被回退付费模型 → `402 Payment Required`。
  - `edge_tts`：免费免 key，异步接口在 `synth_chunk` 内 `asyncio.run` 包一层即可同步调用。
  - `mimo`：endpoint `https://api.xiaomimimo.com/v1/chat/completions`（不是 `mimo.xiaomi.com`）；鉴权头 `api-key`（不是 `Authorization: Bearer`）；**合成文本必须放 `role:assistant` 的 content**（`user` 仅放风格指令、不进语音）；`audio.format` 支持 `mp3`/`wav`/`pcm16`，**默认 mp3**，交付 `.mp3`（无需 ffmpeg）。
  - `mimo_voicedesign`（语音设计模式，`mimo-v2.5-tts-voicedesign`）：与预设音色共用 endpoint/鉴权/响应解析，但**没有固定音色名、不用 `audio.voice`**；音色由 `apis.yaml` 的 `voice_design` 字段（自然语言描述：性别/年龄/音色/语气/语速）写入 `messages[user].content` 决定，且 user 内容**不进语音**；合成文本仍放 `assistant`。**该模型 API 本身只出 `wav`/`pcm16`**（请求 mp3 被忽略、照样回 wav）；为统一交付 mp3，provider 拿到 wav 后用本地 ffmpeg（`imageio-ffmpeg` 自带二进制）转码为 mp3，**默认 `mp3` 交付 `.mp3`**（pcm16 为裸流交付 `.pcm`）。可选 `optimize: true` 让模型润色文本（默认关，保证逐字照读）。支持采样超参 `temperature`(默认0.6)/`top_p`(默认0.95)，已默认调低至 `0.3`/`0.5`。**关键限制**：voice_design 是非确定性采样，同一描述词每次生成音色/韵律都不同，直接切分长文本会导致各块像换了人——**不适合做长内容分块**。
  - `mimo_voiceclone`（音色复刻模式，`mimo-v2.5-tts-voiceclone`）：**钉死声线的正确方式**。不传声线描述，改用 `apis.yaml` 的 `anchor`（参考音频路径 mp3/wav、Base64 <10MB）以带 MIME 前缀的 Base64 写入 `audio.voice`，合成文本放 `assistant`。API 只出 `wav`/`pcm16`，provider 转码为 `mp3` 交付 `.mp3`。**全书所有切块共用同一段锚点音频 → 说话人身份（音色）跨章节一致**，解决 voice_design 切分漂移。文档未提 temperature/top_p（克隆靠参考音频钉身份）。**标准链路**：① 先用 `mimo_voicedesign`（`format:wav`）铸一段锚点落盘 `anchor/anchor_voice.wav`；② 全书跑批改 `--api mimo-v2.5-tts-voiceclone`。实测同一锚点连生成 3 次音色一致、仅韵律自然起伏，mp3 合法。**技能已打包两段预铸锚点（声线资产，与项目无关、可跨播客复用）：`anchor/anchor_voice.wav`（默认声线 vd_7）、`anchor/anchor_alt_ch01.wav`（备选声线，取自《脑科学》ch01 前 60 秒），开箱即用**。
- **输出扩展名**由 `provider.ext` 决定（`fish_audio`/`edge_tts`→`.mp3`，`mimo` 按 `format`→`.mp3`/`.wav`，`mimo_voicedesign`/`mimo_voiceclone`→`.wav`/`.pcm`/`.mp3`）；校验用 `provider.is_valid`（mp3 帧头 / wav 的 RIFF 头 / pcm16 非空）。
- 依赖：`pyyaml` + `edge-tts` + `aiohttp_socks`；Fish/mimo 还需 `httpx[socks]`；**`mimo_voicedesign` 出 mp3 还需 `imageio-ffmpeg`**（自带 ffmpeg 做 wav→mp3 转码）。运行需 `dangerouslyDisableSandbox:true`（联网/代理）。

### 2.5 监控与验证命令（参考）
```bash
# 看某组合成进度
tail -f audio/gen.g1.log
# 各 mp3 字节/字比值（健康基线≈2900，<2500 疑似截断）
python - <<'PY'
import os,glob
for t in sorted(glob.glob("*.tts.txt")):
    stem=t[:-8]; mp3s=glob.glob(f"audio/{stem}*.mp3")
    if not mp3s: print(stem,"无mp3"); continue
    n=len(open(t,encoding="utf-8").read().strip())
    sz=os.path.getsize(mp3s[0]); print(f"{stem}: {n}字 {sz/1024/1024:.2f}MB {sz/n:.0f}B/字")
PY
```
