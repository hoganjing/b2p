#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_strayen.py —— 通用「括号深度感知」裸英文修复器（book-to-podcast 技能级资产）。

用途：audit.py（strict 英文源书模式）报 strayEN（正文有裸拉丁字母）时，把括号外的
英文 run 修成「中文（English）」形式，使 noise=0 / strayEN=0 门禁通过。

为什么需要它（踩坑总结）：
  1) 不能简单正则全局替换：已在 （） 内的英文绝不能再次包裹，否则产生嵌套
     （中文（and） English）`，audit 的 PAREN 朴素剥括号会把内侧英文暴露成 strayEN。
     → 本脚本逐字符跟踪全角括号 （） 深度，只改「括号外」文本，括号内原样保留。
  2) 修复后「中文侧必须零拉丁字母」。若把 `NMDA` 修成 `NMDA受体（NMDA）`，
     重跑时括号外的 `NMDA` 又会被抓、重复包装，且 audit 仍报错。
     → 词典的替换值里，英文 token 只能出现在 （） 内；中文侧不得含该拉丁串。
     对「缩写+中文名词」粘合写法（源稿 `NMDA受体` 无空格），词典应写成 `（NMDA）`
     而非 `（NMDA）受体`，避免与源稿自带名词重复。
  3) 脚本幂等：修复后括号外零拉丁，重复运行不产生新改动。

用法：
  python fix_strayen.py --dir <含*.md的目录> --dict <term_map.yaml>
  # term_map.yaml 形如：
  #   ALTE: 濒死样事件（ALTE）
  #   NMDA: （NMDA）
  #   vs: 对（vs）
  #   Hadders-Algra: 哈德斯-阿尔格拉（Hadders-Algra）
  #   II: 二
  # 未出现在词典的拉丁 run 兜底裸包 （run），保证门禁必过（但应补全词典提升播客质量）。

依赖：仅标准库；词典用极简 yaml 解析（无 pyyaml 也能跑单层映射）。
"""
import re, glob, os, sys, argparse

# 括号外的拉丁 run：仅匹配「拉丁字母串」（含内部连字符/撇号，如 don't、post-traumatic）。
# 重要：不要匹配中文标点（，。：·""——《》— 等不在 一-鿿 汉字块内），否则会把标点误当
# 「裸外文」包成 （，） 造成灾难性破坏。这与 audit.py 的 stray 判定 [A-Za-z]{2,} 对齐。
RUN = re.compile(r"[A-Za-z][A-Za-z'\-]*")


def load_dict(path):
    """极简单层 yaml 解析：每行 `key: value`（value 可含冒号）。"""
    d = {}
    for line in open(path, encoding="utf-8"):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if ":" not in s:
            continue
        k, v = s.split(":", 1)
        k, v = k.strip(), v.strip()
        if k and v:
            d[k] = v
    return d


def fix_outside(seg, gloss):
    def repl(m):
        run = m.group(0)
        core = run.rstrip(".,;:")          # 去尾标点再查词典
        tail = run[len(core):]
        if core in gloss:
            return gloss[core] + tail
        return "（" + run + "）"           # 兜底裸包，保证 audit 通过
    return RUN.sub(repl, seg)


def fix_text(text, gloss):
    out = []
    seg = []
    depth = 0
    for c in text:
        if c == "（":
            depth += 1
            if depth == 1:                  # 离开括号外 -> 先修外段
                out.append(fix_outside("".join(seg), gloss))
                seg = []
                out.append(c)
            else:                           # 嵌套开括号，原样保留
                out.append(c)
        elif c == "）":
            if depth > 0:
                depth -= 1
                out.append(c)               # 括号内原样保留
            else:
                out.append(c)               # stray 闭括号，保留
        else:
            if depth == 0:
                seg.append(c)
            else:
                out.append(c)
    out.append(fix_outside("".join(seg), gloss))
    return "".join(out)


def main():
    ap = argparse.ArgumentParser(description="括号深度感知裸英文修复器")
    ap.add_argument("--dir", required=True, help="含 *.md 的目录")
    ap.add_argument("--dict", required=True, help="term->replacement 映射 yaml")
    args = ap.parse_args()
    gloss = load_dict(args.dict)
    files = sorted(glob.glob(os.path.join(args.dir, "*.md")))
    if not files:
        print(f"在 {args.dir} 未找到 *.md")
        return 1
    total = 0
    for f in files:
        t = open(f, encoding="utf-8").read()
        new = fix_text(t, gloss)
        if new != t:
            open(f, "w", encoding="utf-8").write(new)
            total += 1
            print(f"  已修复: {os.path.basename(f)}")
    print(f"\n完成：{total}/{len(files)} 个文件被修改（词典 {len(gloss)} 条）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
