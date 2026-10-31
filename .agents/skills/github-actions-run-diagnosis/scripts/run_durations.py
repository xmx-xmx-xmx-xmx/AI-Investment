#!/usr/bin/env python3
"""按时段统计 GitHub Actions 运行耗时分布，并指出撞破 timeout 的时段。

用法:
    python3 scripts/run_durations.py /tmp/runs.json [--timeout 15] [--workflow .github/workflows/daily-run.yml]

输入是 /repos/{owner}/{repo}/actions/runs?per_page=60 的响应 JSON。

为什么不只看失败的那一次：
    单元数据会得出"纯属偶发"的错误结论。必须看分布——中位数告诉你常态，
    最大值告诉你余量。真正危险的是"中位数已经很高"的时段，而不是偶尔失败的那个。
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime

FMT = "%Y-%m-%dT%H:%M:%SZ"


def parse_time(s):
    if not s:
        return None
    for fmt in (FMT, "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def duration_min(run):
    a = parse_time(run.get("run_started_at") or run.get("created_at"))
    b = parse_time(run.get("updated_at"))
    if not a or not b:
        return None
    return (b - a).total_seconds() / 60.0


def slot_of(run):
    t = parse_time(run.get("run_started_at") or run.get("created_at"))
    if not t:
        return "??:??"
    return f"{t.hour:02d}:{t.minute:02d}"


def read_configured_timeout(path):
    """从 workflow yml 里读 timeout-minutes。"""
    try:
        for line in open(path, encoding="utf-8"):
            m = re.match(r"\s*timeout-minutes:\s*(\d+)", line)
            if m:
                return int(m.group(1))
    except OSError:
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs_json")
    ap.add_argument("--timeout", type=int, default=None, help="墙钟上限（分钟）；不给则尝试从 workflow 读")
    ap.add_argument("--workflow", default=".github/workflows/daily-run.yml")
    args = ap.parse_args()

    limit = args.timeout
    if limit is None:
        limit = read_configured_timeout(args.workflow)
        if limit is not None:
            print(f"（从 {args.workflow} 读到 timeout-minutes = {limit}）\n")

    data = json.load(open(args.runs_json, encoding="utf-8"))
    runs = data.get("workflow_runs", data if isinstance(data, list) else [])
    print(f"total_count = {data.get('total_count', len(runs))} | 本页 {len(runs)} 条\n")

    slots = defaultdict(list)
    cancelled = []
    for r in runs:
        d = duration_min(r)
        if d is None:
            continue
        slots[slot_of(r)].append(d)
        if r.get("conclusion") == "cancelled":
            cancelled.append((r, d))

    hdr = f"{'时段':<8}{'次数':>5}{'中位':>9}{'最长':>9}{'撞墙':>6}"
    if limit:
        hdr += f"{'余量':>10}"
    print(hdr)
    print("-" * len(hdr))

    riskiest = None
    for slot in sorted(slots):
        ds = sorted(slots[slot])
        med = ds[len(ds) // 2]
        mx = ds[-1]
        hits = sum(1 for x in ds if limit and x > limit)
        line = f"{slot:<8}{len(ds):>5}{med:>8.1f}m{mx:>8.1f}m{hits:>6}"
        if limit:
            margin = limit - mx
            line += f"{margin:>9.1f}m"
            # 只把"还没撞过墙"的时段列为预警对象：已经撞过的时段问题已暴露，
            # 真正会再次惊吓你的是那个"看着很稳、其实余量最小"的时段。
            if hits == 0 and (riskiest is None or margin < riskiest[1]):
                riskiest = (slot, margin, mx)
        print(line)

    if cancelled:
        print("\n=== conclusion=cancelled ===")
        for r, d in sorted(cancelled, key=lambda x: x[1], reverse=True):
            ran = r.get("run_started_at", "?")
            print(f"  {ran}  {d:.1f}m  {r['name']}  {r['head_sha'][:7]}  id={r['id']}")
        if limit:
            near = [f"{d:.1f}m" for _, d in cancelled if d >= limit - 0.5]
            if near:
                print(f"  → 耗时 {', '.join(near)}，全部贴着上限 {limit}m ⇒ 确认是 timeout 强杀，不是偶发中断")

    if riskiest and riskiest[1] < limit * 0.35:
        slot, margin, mx = riskiest
        print(f"\n⚠️ 最危险时段 {slot}：最长 {mx:.1f}m，距上限仅剩 {margin:.1f}m。"
              f"该时段只要再慢 {margin:.1f}m 就会重演同样的静默失败。")


if __name__ == "__main__":
    sys.exit(main())
