#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API 无关的生成编排器。
- 切句分块（≤ max_chars）
- 逐块调用 provider.synth_chunk
- 失败重试；仍失败则对半切自愈
- 完整性校验（MP3 帧头）
- 断点续跑（已存在的 mp3 自动跳过）
上层只依赖 TTSProvider.synth_chunk，不关心具体 API。

错误处理约定（与 base.TTSFatalError / TTSRetryableError 配合）：
- TTSFatalError（4xx 参数/认证/内容错误）：不重试、不对半切自愈，直接上抛，
  由 generate_chapter 捕获后跳过整章并打印明确错误。
- TTSRetryableError（退避重试已耗尽）：同上，整块放弃，不再对半切。
"""
import os
import re
import time
from .providers.base import TTSFatalError, TTSRetryableError

SENT_SPLIT = re.compile(r"(?<=[。！？；.!?;])")
MAX_CHARS = 1500


def split_text(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = SENT_SPLIT.split(text)
    chunks, cur = [], ""
    for p in parts:
        if not p.strip():
            continue
        if len(cur) + len(p) <= max_chars:
            cur += p
        else:
            if cur:
                chunks.append(cur)
            # 单句超长，硬切
            while len(p) > max_chars:
                chunks.append(p[:max_chars])
                p = p[max_chars:]
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


def _is_mp3(b: bytes) -> bool:
    if not b:
        return False
    return (b[0] == 0xFF and (b[1] & 0xE0) == 0xE0) or b[:3] == b"ID3"


def _synth_one(provider, chunk: str, retries: int = 3) -> bytes:
    last = None
    for _ in range(retries):
        try:
            b = provider.synth_chunk(chunk)
            if b and provider.is_valid(b):
                return b
            last = "invalid audio header"
        except (TTSFatalError, TTSRetryableError):
            raise  # 致命/已耗尽退避：立即上抛，不重复重试
        except Exception as e:  # noqa: BLE001
            last = e
    return b""


def synth_chunk_heal(provider, chunk: str, depth: int = 0) -> bytes:
    """合成一块；失败则对半切递归自愈。"""
    if not chunk.strip():
        return b""
    try:
        data = _synth_one(provider, chunk)
    except (TTSFatalError, TTSRetryableError):
        raise  # 致命/已耗尽：不再对半切自愈（切半也救不了 4xx / 持续 5xx）
    if data:
        return data
    if len(chunk) <= 12 or depth > 4:
        return b""  # 放弃此块
    mid = len(chunk) // 2
    return synth_chunk_heal(provider, chunk[:mid], depth + 1) + \
        synth_chunk_heal(provider, chunk[mid:], depth + 1)


def generate_chapter(provider, tts_path: str, out_path: str, max_chars: int = MAX_CHARS) -> bool:
    """生成单章。已存在且有效则跳过（断点续跑）。返回是否新生成。"""
    if os.path.exists(out_path) and os.path.getsize(out_path) > 2000:
        print(f"  [skip] 已存在: {os.path.basename(out_path)}")
        return False
    text = open(tts_path, "r", encoding="utf-8").read()
    chunks = split_text(text, max_chars)
    audio = b""
    dropped = 0
    for i, ch in enumerate(chunks, 1):
        ts = time.strftime("%H:%M:%S")
        print(f"    [{ts}] 请求 chunk {i}/{len(chunks)} ({len(ch)}字)...", flush=True)
        try:
            b = synth_chunk_heal(provider, ch)
        except (TTSFatalError, TTSRetryableError) as e:
            print(f"  [FATAL] {os.path.basename(tts_path)} 第{i}块致命错误，跳过本章: {e}")
            return False
        if b:
            audio += b
        else:
            dropped += 1
        print(f"    chunk {i}/{len(chunks)} -> {len(b)} bytes"
              + ("  [DROPPED]" if not b else ""), flush=True)
        time.sleep(2)  # 块间温和间隔，降低 mimo 限流概率
    if not audio:
        print(f"  [FAIL] {os.path.basename(tts_path)} 无音频产出")
        return False
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(audio)
    cjk = len(re.findall(r"[一-鿿]", text))
    ratio = os.path.getsize(out_path) / max(cjk, 1)
    print(f"  [ok] {os.path.basename(out_path)} {os.path.getsize(out_path)}B "
          f"| 字 {cjk} | 字节/字 {ratio:.0f}"
          + (f" | 丢弃块 {dropped}" if dropped else ""))
    return True
