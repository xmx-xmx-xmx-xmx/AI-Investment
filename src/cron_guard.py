"""#6 CI cron 兜底判重器（2026-09-30）。

背景：简报的全部时段触发走的是「飞书定时任务 → Render bot → workflow_dispatch」
这条链，GitHub Actions 自己不会主动跑。这条链有两个**历史复发过**的单点：
Render "Port scan timeout"（09-05 / 09-17 各一次）会让 bot 全程下线；workflow
被强杀（09-22 12:00 cancelled）会让该时段推送整条丢失。两者都**静默**，无兜底。

本模块为 schedule（cron）触发路径服务：cron 在每个时段后约 10 分钟自动补跑一次，
但必须先判定「该时段今天是否已经有人跑过」，否则 Bot 正常时会重复推送：

  - 该时段窗口内已有**成功**的 workflow_dispatch → skip（Bot 已正常推送）
  - 该时段窗口内已有 dispatch **在跑/排队**      → skip（它会推，别抢）
  - 该时段窗口内 dispatch **失败/被取消**        → run（补推）
  - 该时段窗口内**没有任何** dispatch           → run（Bot 挂了，兜底）
  - 当前时刻已**滑出**该时段窗口               → skip（延迟太久，推了也没意义）

⚠️ 两个刻意的设计决策：
  1. **查询失败 fail-open**（照跑）：宁可冒一次重复推送的风险，也不静默漏推。
     重复推送用户能看见、能容忍；漏推无人知晓。
  2. 判定粒度是「时段窗口」不是「当天」：防止 cron 延迟跨过时段边界后，
     误把上一时段的成功当成本时段已推过。

CLI：
    python -m src.cron_guard infer-mode   # 按当前北京时间推断时段名（cron step 用）
    python -m src.cron_guard guard <mode> # 判重，exit 0=补跑 / 3=跳过 / 1=异常
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime, time, timedelta, timezone

logger = logging.getLogger(__name__)

TZ_CN = timezone(timedelta(hours=8))

# ── 时段窗口（北京时间，闭开区间 [start, end)）──
# 分界 11:45 的来历：Bot 实际在 12:00 触发 midday（见 Actions 运行记录），
# morning 的 cron 兜底在 08:40 跑；若 morning 延迟到 12:00+ 才跑，已滑出窗口 → skip。
SLOT_WINDOWS: dict[str, tuple[time, time]] = {
    "morning":     (time(8, 0),  time(11, 45)),
    "midday":      (time(11, 45), time(14, 0)),
    "closing":     (time(14, 0), time(20, 0)),
    "evening":     (time(20, 0), time(23, 59, 59)),
    "sat_morning": (time(8, 0),  time(12, 0)),
    "sun_evening": (time(18, 0), time(23, 59, 59)),
}

# cron 时刻（UTC）——与 daily-run.yml 的 schedule: 块一一对应，改一处必改另一处。
# 🔴 2026-09-30 双推事故后定稿：所有时段 cron 一律放在 **bot 触发后约 45 分钟**
#    （bot 实测每天稳定在 08:30 / 12:00 / 14:30 / 21:00，周六 09:00 / 周日 19:00）。
#    原因：GitHub cron 有 ±12 分钟级别的前置抖动，
#    cron 与 bot 只隔 10 分钟时，cron 抖到 bot 之前就触发 → 判重看不到未来的
#    dispatch → 双推。后移 45 分钟后正常路径 = bot 先推、cron 判重跳过；bot 失联时
#    兜底晚约 45 分钟补推（代价可接受）。
# 北京 = UTC+8：
#   09:15→01:15 / 12:45→04:45 / 15:15→07:15 / 21:45→13:45
#   周六 09:45→01:45 / 周日 19:45→11:45
CRON_UTC = [
    ("15 1 * * 1-5", "morning"),
    ("45 4 * * 1-5", "midday"),
    ("15 7 * * 1-5", "closing"),
    ("45 13 * * 1-5", "evening"),
    ("45 1 * * 6", "sat_morning"),
    ("45 11 * * 0", "sun_evening"),
]


# ═══════════════════════════════════════════════════════════════
# 纯逻辑（可离线测试）
# ═══════════════════════════════════════════════════════════════

def infer_mode(now_cn: datetime) -> str | None:
    """按当前北京时间推断时段名。窗口外/周末非既定时段返回 None。"""
    wd = now_cn.weekday()  # 0=周一 … 6=周日
    t = now_cn.time()
    if wd == 5:  # 周六
        start, end = SLOT_WINDOWS["sat_morning"]
        return "sat_morning" if start <= t < end else None
    if wd == 6:  # 周日
        start, end = SLOT_WINDOWS["sun_evening"]
        return "sun_evening" if start <= t < end else None
    for mode in ("morning", "midday", "closing", "evening"):
        start, end = SLOT_WINDOWS[mode]
        if start <= t < end:
            return mode
    return None


def in_window(now_cn: datetime, mode: str) -> bool:
    if mode not in SLOT_WINDOWS:
        return False
    start, end = SLOT_WINDOWS[mode]
    return start <= now_cn.time() < end


def classify_dispatch_runs(
    runs: list[dict], mode: str, now_cn: datetime
) -> tuple[bool, str]:
    """按「本时段窗口内的 workflow_dispatch 运行状态」判定补跑还是跳过。

    runs: GitHub API /actions/runs 的 items（run_started_at 为 UTC ISO）。
    返回 (should_run, reason)。
    """
    start, end = SLOT_WINDOWS[mode]
    today = now_cn.date()
    window_status: list[str] = []

    for r in runs:
        started = r.get("run_started_at") or ""
        try:
            started_dt = datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except (ValueError, TypeError):
            continue
        cn = started_dt.astimezone(TZ_CN)
        if cn.date() != today:
            continue
        if not (start <= cn.time() < end):
            continue
        status, conclusion = r.get("status", ""), r.get("conclusion")
        if status in ("in_progress", "queued", "waiting", "pending"):
            return False, f"该时段已有运行进行中（started={started}），等它推送"
        window_status.append(str(conclusion))

    if any(c == "success" for c in window_status):
        return False, f"该时段已有成功运行 {window_status}，Bot 已正常推送"
    if window_status:
        return True, f"该时段的运行均未成功（{window_status}）→ 兜底补跑"
    return True, "该时段窗口内无任何 workflow_dispatch → Bot 未触发，兜底补跑"


# ═══════════════════════════════════════════════════════════════
# IO 层
# ═══════════════════════════════════════════════════════════════

def fetch_dispatch_runs(token: str, repo: str, since_cn: date) -> list[dict]:
    """拉取 since（北京日期）以来的 workflow_dispatch 运行。失败抛异常。"""
    import urllib.request

    # created 过滤按 UTC 日期；北京日期的凌晨可能落在 UTC 前一日，
    # 因此回退一天取全，再由 classify 按北京时间精确过滤。
    since_utc = (datetime.combine(since_cn, time.min, tzinfo=TZ_CN) - timedelta(hours=8)).date()
    url = (
        f"https://api.github.com/repos/{repo}/actions/runs"
        f"?event=workflow_dispatch&per_page=100&created=>={since_utc.isoformat()}"
    )
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-investment-cron-guard",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (固定 https + 官方域名)
        import json
        data = json.loads(resp.read().decode())
    return data.get("workflow_runs", [])


def decide(mode: str, now_utc: datetime | None = None, runs: list[dict] | None = None) -> tuple[bool, str]:
    """主入口。runs 注入用于测试；None 时真实拉取（fail-open）。"""
    now_utc = now_utc or datetime.now(timezone.utc)
    now_cn = now_utc.astimezone(TZ_CN)

    if not in_window(now_cn, mode):
        return False, f"当前 {now_cn:%H:%M} 已滑出 {mode} 窗口 {SLOT_WINDOWS[mode]}，放弃兜底"

    if runs is None:
        token = os.environ.get("GITHUB_TOKEN", "")
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if not token or not repo:
            # CI 里两者必在；缺失说明运行环境异常 —— fail-open 照跑
            return True, "GITHUB_TOKEN/GITHUB_REPOSITORY 缺失，无法判重 → fail-open 照跑"
        try:
            runs = fetch_dispatch_runs(token, repo, now_cn.date())
        except Exception as e:  # noqa: BLE001 —— fail-open 是刻意设计
            logger.warning("查询运行记录失败（%s）→ fail-open 照跑", e)
            return True, f"查询运行记录失败（{e}）→ fail-open 照跑"

    return classify_dispatch_runs(runs, mode, now_cn)


def _cli(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(argv) >= 1 and argv[0] == "infer-mode":
        mode = infer_mode(datetime.now(TZ_CN))
        if mode is None:
            print("?", end="")
            return 1
        print(mode, end="")
        return 0
    if len(argv) >= 2 and argv[0] == "guard":
        should_run, reason = decide(argv[1])
        print(f"{'RUN' if should_run else 'SKIP'}: {reason}")
        return 0 if should_run else 3
    print("usage: python -m src.cron_guard infer-mode | guard <mode>", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
