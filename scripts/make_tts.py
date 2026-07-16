#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_tts.py —— 从讲稿 .md 生成干净版 .tts.txt（供任何 TTS 引擎朗读）。

用法：`python make_tts.py [目录]`。省略目录时处理脚本所在目录的 *.md，
也可像 `audit.py` 一样指定任意含 *.md 的目录。
规则：
  - 删掉「整行」的纯括号标签，例如  （开场白）（正文）（主播延伸 · 板块名）
    （收尾与下集预告）（小节标题）等——这些只给主播/人看，不应被读出。
  - 剥掉 Markdown 行内标记：加粗 **x** / *x* / ***x***、反引号 `x`、
    行首列表符号 * - + •、标题 # 号；并把任何残留的零散 * 一律清除，
    确保朗读稿里不出现「星号」等怪音。
  - 正文、标题、章节名（中文）一律保留。
  - 折叠多余空行，去首尾空白。
输出：每个 xxx.md -> xxx.tts.txt，并打印中文字数。
"""
import re, glob, os, sys, argparse


def clean_md(text: str) -> str:
    """去掉 Markdown 行内/行首标记，保留中文正文，确保无残留 *。"""
    out = []
    for ln in text.split("\n"):
        s = ln
        # 行首列表符号（* - + • 后跟空格）
        s = re.sub(r"^\s*(?:[*\-+•]|\d+[.、])\s+", "", s)
        # 标题 # 号
        s = re.sub(r"^#{1,6}\s*", "", s)
        # 粗体/斜体：先 ***/** 再 *
        s = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", s)
        s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
        s = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*(?!\*)", r"\1", s)
        # 反引号代码
        s = re.sub(r"`([^`]*)`", r"\1", s)
        # 任何残留的零散 * 一律清除（防御性）
        s = s.replace("*", "")
        out.append(s)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="讲稿 .md -> 干净 .tts.txt")
    ap.add_argument("dir", nargs="?", default=os.path.dirname(os.path.abspath(__file__)),
                    help="含 *.md 的目录（默认：脚本所在目录）")
    args = ap.parse_args()
    workdir = os.path.abspath(args.dir)
    if not os.path.isdir(workdir):
        print(f"目录不存在：{workdir}", file=sys.stderr)
        return 1

    for f in sorted(glob.glob(os.path.join(workdir, "*.md"))):
        text = open(f, encoding="utf-8").read()
        out_lines = []
        for ln in text.split("\n"):
            s = ln.strip()
            # 删掉整行的纯括号标签（含 主播延伸 标签），正文与标题一律保留
            if re.match(r"^（[^）]*）$", s):
                # 防坑：图表描述若写成独占一行的标签会被静默吞掉（workflow §0.6
                # 要求图表描述写进正文散文）。含 图/表/示意/配图/曲线/结构 或较长的
                # 标签行疑似真内容，告警让作者改用正文散文或行内（图x）。
                if re.search(r"[图图表示意配曲线结构]", s) or len(s) > 40:
                    print(f"  ⚠ 疑似图表/描述被剥离（请改为正文散文，或用行内（图x））：{s}")
                continue
            out_lines.append(ln)
        joined = "\n".join(out_lines)
        # 剥 Markdown 标记
        joined = clean_md(joined)
        # 折叠多余空行，去掉首尾空白
        joined = re.sub(r"\n{3,}", "\n\n", joined).strip() + "\n"
        tts_name = re.sub(r"\.md$", ".tts.txt", os.path.basename(f))
        out_path = os.path.join(workdir, tts_name)
        open(out_path, "w", encoding="utf-8").write(joined)
        cjk = len(re.findall(r"[一-鿿]", joined))
        print(f"{tts_name}: 中文字={cjk}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
