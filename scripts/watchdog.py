#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
看门狗：监视 TTS 并行任务的「进程成活」+「日志变化」，以日志活动为准。

为什么需要它
------------
后台任务只要退出（正常/异常/被 kill）harness 都会发结束通知；
唯独「卡死在阻塞 socket、永不退出」才沉默——你干等半天以为它在跑。
本脚本靠「日志多久没新进展」抓出这种挂死，并用 PID 文件交叉验证进程是否真活。

判定逻辑（日志为主，进程为辅）
-----------------------------
- 日志含 `■ 脚本退出`                → 完成 / 失败(退出码)
- 有 PID 文件但进程已死、且无完成标记 → ⚠ 异常退出(无完成标记)
- 进程在、但日志 > stale 秒无变化      → ⚠ 卡死(进程在但无进展)
- 无 PID/进程未知、日志 > stale 无变化 → ⚠ 卡死/已结束无标记
- 其余                                → 运行中

依赖：仅标准库。
"""
import os, sys, re, time, glob, argparse

def parse_args():
    ap = argparse.ArgumentParser(description="TTS 并行任务看门狗（日志活动为主，进程成活为辅）")
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--dir", default=os.path.join(here, "audio"),
                    help="日志目录（默认 <脚本目录>/audio）")
    ap.add_argument("--pattern", default="gen*.log",
                    help="日志文件名通配（默认 gen*.log）")
    ap.add_argument("--stale", type=int, default=180,
                    help="日志多少秒无变化即判为「疑似卡死」（默认 180）")
    ap.add_argument("--interval", type=int, default=15,
                    help="循环轮询间隔秒（默认 15）")
    ap.add_argument("--once", action="store_true",
                    help="只快照一次就退出（不循环）")
    return ap.parse_args()

def is_alive(pid):
    """跨平台进程存活探测；无 pid 文件返回 None。
    os.kill(pid, 0) 不发信号只做存在性检查；进程不存在抛 ProcessLookupError/OSError。"""
    if pid is None:
        return None
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False

def read_pid(log_path):
    pidp = log_path + ".pid"
    if not os.path.exists(pidp):
        return None
    try:
        return int(open(pidp, encoding="utf-8").read().strip())
    except Exception:
        return None

PROGRESS_KEYS = ["▶ 开始生成", "… 合成中", "✓ 写出", "结果:", "全部完成", "✗✗"]

def analyze(log_path, stale):
    info = {
        "log": os.path.basename(log_path),
        "pid": read_pid(log_path),
        "alive": None, "completed": False, "exit_code": None,
        "last_line": "", "last_ts": "", "age": None, "status": "未知",
    }
    try:
        raw = open(log_path, encoding="utf-8", errors="replace").read()
        lines = raw.splitlines()
    except Exception:
        lines = []

    # 完成标记（atexit 写，含退出码）
    for ln in reversed(lines):
        if "■ 脚本退出" in ln:
            info["completed"] = True
            m = re.search(r"退出码\s*(\d+)", ln)
            if m:
                info["exit_code"] = int(m.group(1))
            break

    # 最后活动：用文件 mtime，比解析 HH:MM:SS 更稳（跨午夜不误判）
    try:
        info["age"] = time.time() - os.path.getmtime(log_path)
    except Exception:
        info["age"] = None

    # 最后进度行 + 其时间戳
    for ln in reversed(lines):
        s = ln.strip()
        if any(k in s for k in PROGRESS_KEYS):
            info["last_line"] = s
            if len(s) >= 8 and s[2] == ":" and s[5] == ":" and s[:2].isdigit():
                info["last_ts"] = s[:8]
            break

    # 进程成活（交叉验证）
    info["alive"] = is_alive(info["pid"])

    # 判定
    if info["completed"]:
        info["status"] = "完成" if info["exit_code"] == 0 else "失败(退出码%s)" % info["exit_code"]
    elif info["alive"] is False:
        info["status"] = "⚠ 异常退出(无完成标记)"
    elif info["age"] is not None and info["age"] > stale:
        info["status"] = "⚠ 卡死(进程在但无进展)" if info["alive"] is True else "⚠ 卡死/已结束无标记"
    else:
        info["status"] = "运行中"
    return info

def fmt_age(age):
    if age is None:
        return "?"
    s = int(age)
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm%02ds" % (s // 60, s % 60)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)

def snapshot(args):
    logs = sorted(glob.glob(os.path.join(args.dir, args.pattern)))
    print("=" * 86)
    print("看门狗快照  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("目录=%s  模式=%s  卡死阈值=%ds" % (args.dir, args.pattern, args.stale))
    print("-" * 86)
    if not logs:
        print("（未找到匹配 %s 的日志）" % args.pattern)
        return
    counts = {}
    for lp in logs:
        inf = analyze(lp, args.stale)
        if inf["pid"] is None:
            pcol = "PID ?"
        else:
            pcol = "PID %d %s" % (inf["pid"], "活" if inf["alive"] is True else ("死" if inf["alive"] is False else "?"))
        print("%-15s | %-14s | 活动 %-9s | %-24s | %s" % (
            inf["log"], pcol, fmt_age(inf["age"]), inf["status"], inf["last_line"][:38]))
        counts[inf["status"]] = counts.get(inf["status"], 0) + 1
    print("-" * 86)
    summary = "   ".join("%s ×%d" % (k, v) for k, v in sorted(counts.items()))
    print("汇总: " + (summary or "无"))

def main():
    args = parse_args()
    if args.once:
        snapshot(args)
        return
    try:
        while True:
            snapshot(args)
            print("（每 %ds 刷新一次，Ctrl+C 退出）\n" % args.interval)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n看门狗已停止。")

if __name__ == "__main__":
    main()
