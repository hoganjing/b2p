#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ╔════════════════════════════════════════════════════════════════════╗
# ║ STATUS: LEGACY / 单 API 快速版                                      ║
# ║ 这是 Fish Audio 单提供商的独立脚本，作为「快速通道」保留。            ║
# ║ 主推的多 provider 引擎是 scripts/tts_engine/（入口 tts_run.py）。    ║
# ║ ⚠ 不要与本技能的 tts_engine 对同一批章节同时跑，否则 .mp3 会写冲突。 ║
# ╚════════════════════════════════════════════════════════════════════╝
"""
Fish Audio S2.1 Pro 免费 TTS —— 单文件生成脚本（httpx 传输层，v4）。

相比旧版(stdlib 手写 socket)的优势：
- httpx 的 iter_bytes() 自动解码 HTTP chunked 传输，写盘即纯 mp3，
  绝不会像旧版那样把 XXXX\r\n 分块标记混进文件（旧版 Bug A 的整类坑被消除）。
- 代理一行搞定：proxy="socks5://127.0.0.1:10808"。

健壮性层（v4 增强）：
1) 长文切块：单次 TTS 请求超过 CHUNK_CHARS 字时，按句末切分，逐块合成再拼接。
   小块连接更短，从根本上规避“长连接被 SOCKS5 代理/上游静默掐死（半开连接、不报错不传数据）”的坑。
2) 每块独立重试 + 本地缓存续跑：每块写出 audio/_chunks/<name>.<i>.mp3，
   进程被杀/会话回收后重跑会自动复用已完成的块，不重复花钱。
3) 完整性校验：写完校验 字节/字 ≥ MIN_BPC 且开头是合法 mp3 帧；不达标则整集覆盖重跑。
4) 断点续跑：已存在且通过完整性校验的最终文件直接跳过。
5) 不使用 os.remove / os.replace —— 本机 Python 被装了“安全删除”钩子（写入回收站），
   沙箱回收站不可用会导致任何删除操作抛 OSError 进而崩溃。改为“直接写最终文件、覆盖式重试”，
   半成品会被下一次重试用 'wb' 截断覆盖，无需删除。
6) atexit 崩溃上报：只要 Python 解释器自己退出（含未捕获异常）就写一行 `■ 脚本退出`，
   没有这行即说明进程被外力 SIGKILL（用于诊断“会话空闲回收后台任务”）。
7) 防御性 dechunk：若响应意外仍是分块格式，自动解码兜底。
8) 合理超时：read=120s（两字节间隔超过 120s 即判挂死并触发重试，避免旧版 read=1200s 干等 20 分钟）。

推荐用法：单进程顺序前台跑（把整批章节作为参数一次传入，脚本串行生成）。
"""
import os, sys, re, time, atexit, traceback

try:
    import httpx
except ImportError:
    sys.stderr.write("本脚本需要 httpx，请先安装：pip install httpx\n")
    sys.exit(3)

BASE = os.path.dirname(os.path.abspath(__file__))

# ---- 可调参数 ----
MIN_BPC     = 2500    # 字节/字 下限（健康基线 ~2900，截断集明显低于此）
MAX_RETRY   = 5       # 单块最大重试次数（免费档偶发截断，给足重试余量）
CHUNK_CHARS = 1500    # 单块最大字数；超过则按句末切分，避免长连接被代理掐死

def load_env():
    env = {}
    p = os.path.join(BASE, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

ENV = load_env()
KEY      = ENV.get("FISH_API_KEY", "")
VOICE_ID = ENV.get("FISH_VOICE_ID", "")
SOCKS    = ENV.get("FISH_SOCKS", "127.0.0.1:10808")
API_URL  = "https://api.fish.audio/v1/tts"

LOG_PATH = os.path.join(BASE, "audio", os.environ.get("FISH_LOG", "gen.log"))
CHUNK_DIR = os.path.join(BASE, "audio", "_chunks")

def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except Exception:
        pass

# ---- 文本切块 ----
def split_text(text, max_chars=CHUNK_CHARS):
    """按句末标点切分；单句超长则硬切。返回块列表（不含空串）。"""
    raw = re.split(r'(?<=[。！？；…\n])', text)
    chunks, cur = [], ""
    for p in raw:
        if not p.strip():
            continue
        if len(cur) + len(p) <= max_chars:
            cur += p
        else:
            if cur:
                chunks.append(cur)
                cur = ""
            if len(p) > max_chars:            # 超长单句，硬切
                for i in range(0, len(p), max_chars):
                    chunks.append(p[i:i + max_chars])
            else:
                cur = p
    if cur:
        chunks.append(cur)
    return chunks

# ---- 防御性分块解码（仅在响应意外仍是 chunked 时启用）----
def is_chunked(raw):
    return bool(re.match(rb'^[0-9a-fA-F]+\r\n', raw))

def dechunk(data):
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        crlf = data.find(b"\r\n", i)
        if crlf == -1:
            break
        try:
            size = int(data[i:crlf], 16)
        except ValueError:
            break
        i = crlf + 2
        if size == 0:
            break
        out += data[i:i + size]
        i += size
        if data[i:i + 2] == b"\r\n":
            i += 2
    return bytes(out)

def head_valid(path):
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except Exception:
        return False
    if len(head) < 2:
        return False
    return (head[0] == 0xFF and (head[1] & 0xE0) == 0xE0) or head[:3] == b"ID3"

def is_valid_mp3(path, text_len):
    """完整性校验：存在、够大、开头是合法 mp3 帧、字节/字达标。"""
    if not os.path.exists(path):
        return False
    sz = os.path.getsize(path)
    if sz < 2000:
        return False
    if not head_valid(path):
        return False
    if text_len:
        if sz / text_len < MIN_BPC:
            return False
    return True

BACKOFF          = 4.0    # 普通失败重试间退避秒数
RATE_BACKOFF     = 20.0   # 命中 429 限流时退避更久（免费档随时可能限流）
INTER_DELAY      = 1.5    # 块/集之间的小间隔，对免费档温柔一点
MAX_SPLIT_DEPTH  = 3      # 单块连败后最多再对半切几次（递归自愈）
MIN_SPLIT        = 200    # 切到低于此字数不再切，直接放弃该块

def fetch_chunk(text):
    """经 SOCKS5 代理调用 Fish Audio，返回干净的 mp3 字节；失败抛异常。"""
    payload = {
        "text": text,
        "reference_id": VOICE_ID,
        "format": "mp3",
        "mp3_bitrate": 128,
        "latency": "normal",     # 文档：normal = 最稳定输出（批量生成不要 balanced 的快首包）
        "chunk_length": 300,     # 服务端批处理粒度上限，长文更高效
    }
    headers = {
        "Authorization": f"Bearer {KEY}",
        "model": "s2.1-pro-free",
    }
    # 连接 10s，收流单字节间隔 120s（卡死超 120s 即判超时重试，不再干等 20 分钟），写/池 30s
    timeout = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=30.0)
    proxy = f"socks5://{SOCKS}"
    with httpx.Client(proxy=proxy, timeout=timeout, verify=True) as client:
        with client.stream("POST", API_URL, json=payload, headers=headers) as resp:
            resp.raise_for_status()           # 非 200 直接抛（httpx 自动解码分块）
            data = b""
            for chunk in resp.iter_bytes():
                data += chunk
    # 防御：若意外仍是分块格式则解码兜底
    if is_chunked(data):
        data = dechunk(data)
        log("  [注意] 响应仍是分块格式，已自动解码")
    if data[:1] == b"{":
        raise RuntimeError("返回 JSON（可能配额/参数错误）: %s" % data[:400].decode("utf-8", "ignore"))
    if len(data) < 2000 or not (data[:1] == b"\xff" or data[:3] == b"ID3"):
        raise RuntimeError("返回内容非合法 mp3（%d bytes）" % len(data))
    return data

def synth(text, tag=""):
    """带重试+退避的单次合成；全部失败则抛最后一个异常。429 限流退避更久。"""
    last_err = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            return fetch_chunk(text)
        except Exception as e:
            last_err = e
            is_429 = getattr(getattr(e, "response", None), "status_code", None) == 429
            log("  ✗ %s第%d次 失败%s: %r" % (tag, attempt, "（429限流）" if is_429 else "", e))
            if attempt < MAX_RETRY:
                time.sleep(RATE_BACKOFF if is_429 else BACKOFF)
            continue
    raise last_err

def synth_resilient(text, depth=0, tag=""):
    """合成一段文本；连败则对半切递归重试，保证总能往前走。返回 mp3 字节。"""
    try:
        return synth(text, tag=tag)
    except Exception:
        if depth >= MAX_SPLIT_DEPTH or len(text) <= MIN_SPLIT:
            raise
        # 在靠近中点处找句末切分点
        mid = len(text) // 2
        cut = -1
        for j in range(mid, max(0, mid - 200), -1):
            if text[j] in "。！？；…\n":
                cut = j + 1
                break
        if cut <= 0:
            cut = mid
        log("  ↳ %s连败，对半切（深度 %d）重试" % (tag, depth + 1))
        left = synth_resilient(text[:cut], depth + 1, tag)
        right = synth_resilient(text[cut:], depth + 1, tag)
        return left + right

def gen_one(name):
    tts_path = os.path.join(BASE, name + ".tts.txt")
    if not os.path.exists(tts_path):
        log("[跳过] 找不到 %s" % tts_path)
        return False
    text = open(tts_path, encoding="utf-8").read().strip()
    out_path = os.path.join(BASE, "audio", name + ".mp3")

    # 断点续跑：已存在且通过完整性校验则跳过
    if is_valid_mp3(out_path, len(text)):
        log("[已存在且有效] %s (%d bytes)，跳过" % (name, os.path.getsize(out_path)))
        return True

    chunks = split_text(text)
    os.makedirs(CHUNK_DIR, exist_ok=True)
    log("▶ 开始生成 [%s]  文本 %d 字，分 %d 块 ..." % (name, len(text), len(chunks)))

    full = bytearray()
    for i, c in enumerate(chunks, 1):
        cf = os.path.join(CHUNK_DIR, "%s.%d.mp3" % (name, i))
        tag = "块 %d/%d " % (i, len(chunks))
        # 块级续跑：本地缓存且头部合法则复用，不重复花钱
        if os.path.exists(cf) and os.path.getsize(cf) >= 2000 and head_valid(cf):
            full += open(cf, "rb").read()
            log("  ↺ 块 %d/%d 复用本地缓存 (%d bytes)" % (i, len(chunks), os.path.getsize(cf)))
            continue
        try:
            body = synth_resilient(c, tag=tag)
        except Exception as e:
            log("  ✗✗ %s 第 %d 块经多次重试+切分仍失败（末因：%r）" % (name, i, e))
            return False
        with open(cf, "wb") as f:
            f.write(body)              # 写块缓存（覆盖式）
        full += body
        log("  ✓ 块 %d/%d 收 %d bytes" % (i, len(chunks), len(body)))
        if i < len(chunks):
            time.sleep(INTER_DELAY)   # 块间小间隔，对免费档温柔一点

    # 写出整集合成结果（'wb' 截断覆盖，无需删除旧文件）
    with open(out_path, "wb") as f:
        f.write(full)
    if is_valid_mp3(out_path, len(text)):
        bpc = os.path.getsize(out_path) / len(text)
        log("  ✓ 写出 %s  (%d bytes, %.0f 字节/字)" %
            (out_path, os.path.getsize(out_path), bpc))
        return True
    else:
        sz = os.path.getsize(out_path)
        bpc = sz / len(text) if len(text) else 0
        last_err = "完整性未过（字节/字=%.0f < %d），将覆盖重试" % (bpc, MIN_BPC)
        log("  ✗✗ %s %s" % (name, last_err))
        return False

if __name__ == "__main__":
    _EXIT_CODE = [0]
    def _on_exit():
        try:
            log("■ 脚本退出（退出码 %s）" % _EXIT_CODE[0])
        except Exception:
            pass
    atexit.register(_on_exit)

    try:
        os.makedirs(os.path.join(BASE, "audio"), exist_ok=True)
        targets = sys.argv[1:]
        if targets:
            for target in targets:
                ok = gen_one(target)
                log("结果: %s -> %s" % (target, "成功" if ok else "失败"))
                if not ok:
                    _EXIT_CODE[0] = 1
        else:
            n = 0
            for f in sorted(os.listdir(BASE)):
                if f.endswith(".tts.txt"):
                    if gen_one(f[:-8]):
                        n += 1
            log("全部完成：成功 %d 个" % n)
    except Exception:
        _EXIT_CODE[0] = 2
        log("✗✗ 脚本异常崩溃:\n" + traceback.format_exc())
        raise
