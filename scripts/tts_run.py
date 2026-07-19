#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
入口：多 API TTS 生成器。
用法：
  python tts_run.py                 # 用 default profile 跑全部章节
  python tts_run.py --api edge_tts  # 临时切到 edge_tts
  python tts_run.py 15-细胞的社会联系   # 单章
  python tts_run.py --list          # 列出 apis.yaml 里的 profile
依赖：tts_engine/ 包、apis.yaml
"""
import argparse
import glob
import os
import sys
import time
import concurrent.futures as cf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from tts_engine import config as cfgmod
from tts_engine.providers import get_provider
from tts_engine.engine import generate_chapter

# 输入/输出默认都基于「当前工作目录」，与书稿目录一致，避免读/写锁死在脚本目录
WORK_DIR = os.getcwd()
AUDIO_DIR = os.path.join(os.getcwd(), "audio")  # 默认 CWD/audio，避免产物写进技能目录


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("chapters", nargs="*", help="章节名（不含扩展名），省略=全部")
    ap.add_argument("--api", help="覆盖 apis.yaml 的 default，选某个 profile")
    ap.add_argument("--voice", help="覆盖 profile 里的音色（edge_tts/mimo 用 voice，fish_audio 用 voice_id，mimo_voicedesign 用 voice_design）")
    ap.add_argument("--anchor", help="覆盖 profile 里的参考音频路径（仅 mimo_voiceclone 用）")
    ap.add_argument("--temperature", type=float, help="覆盖采样温度（mimo / mimo_voicedesign）")
    ap.add_argument("--top_p", type=float, help="覆盖核采样阈值（mimo / mimo_voicedesign）")
    ap.add_argument("--config", default=os.path.join(HERE, "apis.yaml"))
    ap.add_argument("--out-dir", help="输出音频目录（默认 audio/），指定后生成到该目录、不覆盖旧输出")
    ap.add_argument("--list", action="store_true", help="列出可用 profile")
    ap.add_argument("--max-chars", type=int, default=1500, help="分块上限（字），默认 1500")
    ap.add_argument("--parallel", type=int, default=1, help="并行章节数，默认 1（串行）")
    args = ap.parse_args()

    cfg = cfgmod.load_apis(args.config)
    if args.list:
        print("可用 profile：")
        for k in cfg.get("apis", {}):
            print(f"  - {k}  (type={cfg['apis'][k].get('type')})")
        print(f"default = {cfg.get('default')}")
        return

    ptype, pname, profile = cfgmod.resolve(args.config, args.api)
    # anchor 路径若相对，则相对 apis.yaml 所在目录解析（voiceclone 用）
    if profile.get("anchor") and not os.path.isabs(profile["anchor"]):
        profile["anchor"] = os.path.join(os.path.dirname(os.path.abspath(args.config)), profile["anchor"])
    # 音色覆盖：统一用 --voice 指定，自动映射到 provider 的字段
    # （edge_tts/mimo 用 voice，fish_audio 用 voice_id，mimo_voicedesign 用 voice_design）
    if args.voice:
        for key in ("voice", "voice_id", "voice_design"):
            if key in profile:
                profile[key] = args.voice
                break
    if args.temperature is not None:
        profile["temperature"] = args.temperature
    if args.top_p is not None:
        profile["top_p"] = args.top_p
    if args.anchor:
        profile["anchor"] = args.anchor
    extra = []
    if args.voice:
        extra.append(f"voice={args.voice}")
    if args.temperature is not None:
        extra.append(f"temp={args.temperature}")
    if args.top_p is not None:
        extra.append(f"top_p={args.top_p}")
    print(f"使用 provider: {ptype}  profile: {pname}" + (f"  {' '.join(extra)}" if extra else ""))
    provider = get_provider(ptype, profile)

    out_dir = os.path.abspath(args.out_dir) if args.out_dir else AUDIO_DIR
    os.makedirs(out_dir, exist_ok=True)
    print(f"输出目录: {out_dir}")

    if args.chapters:
        tts_files = [os.path.join(WORK_DIR, f"{c}.tts.txt") for c in args.chapters]
    else:
        tts_files = sorted(glob.glob(os.path.join(WORK_DIR, "*.tts.txt")))

    max_chars = args.max_chars
    parallel = max(1, args.parallel)
    print(f"分块上限: {max_chars} 字 | 并行章节数: {parallel}", flush=True)

    ext = provider.ext
    total = len(tts_files)
    t0 = time.time()
    done = 0

    def run_one(idx, tf):
        # 已完成则跳过（断点续跑），不建 provider
        if not os.path.exists(tf):
            print(f"[warn] 缺文件: {tf}", flush=True)
            return False
        base = os.path.splitext(os.path.basename(tf))[0]  # "15-细胞.tts"
        out = os.path.join(out_dir, base + ext)
        if os.path.exists(out) and os.path.getsize(out) > 2000:
            print(f"  [skip] 已存在: {base}{ext}", flush=True)
            return False
        ts = time.strftime("%H:%M:%S")
        elapsed = int(time.time() - t0)
        print(f"\n[{ts}] => ({idx}/{total}) {base}  (已用时 {elapsed//60}m{elapsed%60}s)", flush=True)
        # 每章独立 provider，并行安全；串行也无害
        p = get_provider(ptype, profile)
        try:
            return generate_chapter(p, tf, out, max_chars=max_chars)
        finally:
            p.close()

    if parallel == 1:
        for idx, tf in enumerate(tts_files, 1):
            if not os.path.exists(tf):
                print(f"[warn] 缺文件: {tf}", flush=True)
                continue
            base = os.path.splitext(os.path.basename(tf))[0]
            if base.endswith(".tts"):
                base = base[: -len(".tts")]
            out = os.path.join(out_dir, base + ext)
            if os.path.exists(out) and os.path.getsize(out) > 2000:
                print(f"  [skip] 已存在: {base}{ext}", flush=True)
                continue
            ts = time.strftime("%H:%M:%S")
            elapsed = int(time.time() - t0)
            print(f"\n[{ts}] => ({idx}/{total}) {base}  (已用时 {elapsed//60}m{elapsed%60}s)", flush=True)
            if generate_chapter(provider, tf, out, max_chars=max_chars):
                done += 1
        provider.close()
    else:
        provider.close()  # 串行的那个不再需要，并行用每章独立 provider
        with cf.ThreadPoolExecutor(max_workers=parallel) as ex:
            futs = [ex.submit(run_one, idx, tf) for idx, tf in enumerate(tts_files, 1)]
            for fut in cf.as_completed(futs):
                try:
                    if fut.result():
                        done += 1
                except Exception as e:  # noqa: BLE001
                    print(f"  [ERROR] 章节异常: {e}", flush=True)

    total_elapsed = int(time.time() - t0)
    print(f"\n[{time.strftime('%H:%M:%S')}] 完成。新生成 {done} 章，总用时 "
          f"{total_elapsed//60}m{total_elapsed%60}s，输出目录 {out_dir}")


if __name__ == "__main__":
    main()
