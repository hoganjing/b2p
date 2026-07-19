#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit.py —— 把「正文必须纯中文 + 去 OCR 噪点」变成可验证门禁。

放置位置：与输出目录（*.md / *.tts.txt）同处，或任意位置用参数指向输出目录。
通用、无书专用硬编码：专有名词白名单 / 源语言从「每本书的 brief」读取，默认宽松。

用法：
    python audit.py <输出目录>                # 审计该目录的 *.md + *.tts.txt
    python audit.py . --book potent-self      # 加载 briefs/potent-self.yaml 白名单
    python audit.py . --briefs references/briefs --book potent-self
    python audit.py . --white Nora Feldenkrais   # 也可直接命令行追加白名单
    python audit.py . --faithful                 # 忠实模式：strayEN 不计入门禁（仅门禁 NOISE）
    python audit.py . --no-gate                  # 只汇报、不返回非零退出码

门禁：发现 noise>0 或 strayEN>0 时，以退出码 1 结束（可接 CI / 提交前钩子）。
      忠实模式（--faithful，或 brief 的 source_lang: zh）下，strayEN 仅作提示、
      不计入门禁失败——因为中文源书的作者有意英文混排应原样保留，只抓真 OCR 噪点（NOISE）。

依赖：仅标准库；白名单 yaml 若 pyyaml 缺失会用极简解析兜底。
"""
import re, glob, os, sys, argparse

# 通用 OCR 噪点（与具体书无关）：扫描/版面残留、图书馆水印、图占位符等。
# 每本书的「专有名词白名单」请在 briefs/<slug>.yaml 的 white: 里维护，不要写死在此。
NOISE_DEFAULT = [
    r"0777", r"3129900", r"\bAPR \d", r"Public Library", r"Digitized by",
    r"FIRST EDITION", r"# CHAPTER", r"continued on back flap", r"Jacket design",
    r"ALSO BY", r"119847", r"<div", r"z-lib", r"archive\.org", r"wikipedia",
]

CJK = re.compile(r"[一-鿿]")
LABEL = re.compile(r"^（[^）]*）\s*$", re.M)        # 整行括号标签
PAREN = re.compile(r"（[^）]*[A-Za-z][^）]*）")       # 含拉丁字母的括号注释


def _load_brief(args):
    """从 briefs/<slug>.yaml 或命令行载入白名单与源语言。

    返回 (white:set, source_lang:str|None)。source_lang=='zh' 表示中文源书，
    审计时 strayEN 降权（只门禁 NOISE）。
    """
    white = set()
    source_lang = None
    if args.white:
        white.update(args.white)
    if args.book:
        candidates = [
            os.path.join(args.briefs, f"{args.book}.yaml"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "references", "briefs", f"{args.book}.yaml"),
        ]
        path = next((p for p in candidates if os.path.exists(p)), None)
        if not path:
            print(f"! 未找到 briefs 配置：{args.book}（在 {candidates}）", file=sys.stderr)
        else:
            raw = open(path, encoding="utf-8").read()
            try:
                import yaml
                data = yaml.safe_load(raw) or {}
                white.update(data.get("white", []) or [])
                source_lang = data.get("source_lang")
            except Exception:
                # 极简兜底：抓 `white:` 下的 - 项 与 `source_lang:` 行
                in_block = False
                for line in raw.splitlines():
                    s = line.strip()
                    if s.startswith("white:"):
                        in_block = True; continue
                    if s.startswith("source_lang:"):
                        source_lang = s.split(":", 1)[1].strip()
                    if in_block:
                        if s.startswith("- "):
                            white.add(s[2:].strip())
                        elif s and not s.startswith("#") and ":" in s and not s.startswith("-"):
                            in_block = False
    return white, source_lang


def audit_file(path, noise_pats, white):
    t = open(path, encoding="utf-8").read()
    noise = [p for p in noise_pats if re.search(p, t, re.I)]
    work = PAREN.sub("", LABEL.sub("", t))           # 剥标签行 + 括号注释后再找裸外文
    stray = sorted({w for w in re.findall(r"[A-Za-z]{2,}", work) if w not in white})
    return t, noise, stray


def main():
    ap = argparse.ArgumentParser(description="纯中文 + OCR 噪点门禁")
    ap.add_argument("dir", nargs="?", default=".", help="输出目录（含 *.md / *.tts.txt）")
    ap.add_argument("--book", help="briefs/<slug>.yaml 的书名 slug，载入白名单与源语言")
    ap.add_argument("--briefs", default="briefs", help="briefs 目录（默认 ./briefs）")
    ap.add_argument("--white", nargs="*", help="额外白名单词（如 Nora Feldenkrais）")
    ap.add_argument("--extra-noise", nargs="*", default=[], help="额外噪点正则")
    ap.add_argument("--faithful", action="store_true",
                    help="忠实模式：不将 strayEN 计入门禁（仅门禁 NOISE）。用于中文源书，作者有意英文保留。")
    ap.add_argument("--md-only", action="store_true")
    ap.add_argument("--tts-only", action="store_true")
    ap.add_argument("--no-gate", action="store_true", help="只汇报，不以非零退出码结束")
    args = ap.parse_args()

    white, source_lang = _load_brief(args)
    # 忠实模式 = 显式 --faithful，或 brief 声明 source_lang: zh（中文源书）
    faithful = args.faithful or (source_lang == "zh")
    noise_pats = NOISE_DEFAULT + args.extra_noise

    patterns = ["*.md"]
    if not args.md_only:
        patterns.append("*.tts.txt") if not args.tts_only else None
    if args.tts_only:
        patterns = ["*.tts.txt"]

    files = []
    for pat in patterns:
        files += sorted(glob.glob(os.path.join(args.dir, pat)))
    if not files:
        print(f"在 {args.dir} 未找到匹配文件（patterns={patterns}）")
        return 0

    mode = "faithful/zh（strayEN 仅提示）" if faithful else "strict（strayEN 计入门禁）"
    print(f"[{mode}]  source_lang={source_lang or '未声明(默认 en)'}")
    print(f"{'file':42} {'cjk':>5} {'noise':>5} {'strayEN':>7}   white={sorted(white)}")
    bad = False
    for f in files:
        t, noise, stray = audit_file(f, noise_pats, white)
        print(f"{os.path.basename(f):42} {len(CJK.findall(t)):>5} {len(noise):>5} {len(stray):>7}")
        if noise:
            print("   NOISE:", noise); bad = True
        if stray:
            print("   STRAY:", stray)
            if faithful:
                print("   (faithful/zh 模式：strayEN 不计入门禁，仅作提示)")
            else:
                bad = True
    if bad and not args.no_gate:
        print("\n❌ 门禁未过：noise=0"
              + (" 且 strayEN=0" if not faithful else "（strayEN 在忠实模式下不计入）")
              + " 才允许交付。", file=sys.stderr)
        return 1
    print("\n✅ 门禁通过（noise=0"
          + ("；strayEN 在忠实模式下不计" if faithful else "，strayEN=0")
          + "）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
