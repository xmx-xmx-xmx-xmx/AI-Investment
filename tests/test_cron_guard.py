"""#6 cron 兜底判重器测试。

覆盖：时段推断 / 窗口判定 / 运行分类 / fail-open / CLI 退出码。
所有测试离线（runs 注入，不打 GitHub API）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.cron_guard import (
    SLOT_WINDOWS,
    TZ_CN,
    classify_dispatch_runs,
    decide,
    infer_mode,
    in_window,
)


def _cn(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=TZ_CN)


def _run(started_cn: datetime, status="completed", conclusion="success"):
    """构造一条 GitHub API 形态的运行记录（run_started_at 转 UTC ISO）。"""
    started_utc = started_cn.astimezone(timezone.utc)
    return {
        "status": status,
        "conclusion": conclusion,
        "run_started_at": started_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ── infer_mode ──

def test_infer_mode_weekday_slots():
    assert infer_mode(_cn(2026, 9, 30, 8, 40)) == "morning"
    assert infer_mode(_cn(2026, 9, 30, 12, 10)) == "midday"
    assert infer_mode(_cn(2026, 9, 30, 14, 40)) == "closing"
    assert infer_mode(_cn(2026, 9, 30, 21, 10)) == "evening"


def test_infer_mode_weekend():
    # 2026-10-03 周六 / 10-04 周日（同时覆盖原 10 分与改后 45 分）
    assert infer_mode(_cn(2026, 10, 3, 9, 10)) == "sat_morning"
    assert infer_mode(_cn(2026, 10, 3, 9, 45)) == "sat_morning"
    assert infer_mode(_cn(2026, 10, 4, 19, 10)) == "sun_evening"
    assert infer_mode(_cn(2026, 10, 4, 19, 45)) == "sun_evening"


def test_infer_mode_out_of_window_returns_none():
    assert infer_mode(_cn(2026, 9, 30, 3, 0)) is None       # 凌晨不在任何窗口
    assert infer_mode(_cn(2026, 10, 3, 21, 10)) is None     # 周六晚上没有既定时段
    assert infer_mode(_cn(2026, 10, 4, 9, 10)) is None      # 周日早上没有既定时段


def test_infer_mode_midday_boundary():
    # 11:45 起算 midday（Bot 实际 12:00 触发 midday）
    assert infer_mode(_cn(2026, 9, 30, 11, 45)) == "midday"
    assert infer_mode(_cn(2026, 9, 30, 11, 44)) == "morning"


# ── in_window ──

def test_in_window_boundaries():
    assert in_window(_cn(2026, 9, 30, 8, 0), "morning")
    assert not in_window(_cn(2026, 9, 30, 11, 45), "morning")
    assert not in_window(_cn(2026, 9, 30, 7, 59), "morning")


def test_in_window_unknown_mode():
    assert not in_window(_cn(2026, 9, 30, 9, 0), "notify")


# ── classify_dispatch_runs ──

def test_classify_success_in_window_skips():
    runs = [_run(_cn(2026, 9, 30, 8, 33))]  # Bot 08:31 触发、08:33 已跑完
    ok, why = classify_dispatch_runs(runs, "morning", _cn(2026, 9, 30, 8, 40))
    assert ok is False
    assert "成功" in why


def test_classify_in_progress_skips():
    runs = [_run(_cn(2026, 9, 30, 8, 33), status="in_progress", conclusion=None)]
    ok, why = classify_dispatch_runs(runs, "morning", _cn(2026, 9, 30, 8, 40))
    assert ok is False
    assert "进行中" in why


def test_classify_queued_skips():
    runs = [_run(_cn(2026, 9, 30, 8, 35), status="queued", conclusion=None)]
    ok, _ = classify_dispatch_runs(runs, "morning", _cn(2026, 9, 30, 8, 40))
    assert ok is False


def test_classify_failed_runs_fallback():
    # 9/22 12:00 那次 cancelled（超时强杀）→ 兜底应该补跑
    runs = [_run(_cn(2026, 9, 22, 12, 0), conclusion="cancelled")]
    ok, why = classify_dispatch_runs(runs, "midday", _cn(2026, 9, 22, 12, 10))
    assert ok is True
    assert "未成功" in why


def test_classify_no_runs_fallback():
    ok, why = classify_dispatch_runs([], "evening", _cn(2026, 9, 30, 21, 10))
    assert ok is True
    assert "无任何" in why


def test_classify_ignores_runs_outside_window():
    # 上一时段（morning 08:33 成功）不能顶替 midday 的判定
    runs = [_run(_cn(2026, 9, 30, 8, 33))]
    ok, _ = classify_dispatch_runs(runs, "midday", _cn(2026, 9, 30, 12, 10))
    assert ok is True


def test_classify_ignores_yesterday():
    runs = [_run(_cn(2026, 9, 29, 8, 33))]
    ok, _ = classify_dispatch_runs(runs, "morning", _cn(2026, 9, 30, 8, 40))
    assert ok is True


def test_classify_success_beats_earlier_failure():
    # 失败后重跑成功 → skip
    runs = [
        _run(_cn(2026, 9, 30, 8, 31), conclusion="failure"),
        _run(_cn(2026, 9, 30, 8, 38), conclusion="success"),
    ]
    ok, _ = classify_dispatch_runs(runs, "morning", _cn(2026, 9, 30, 8, 40))
    assert ok is False


def test_classify_malformed_timestamp_ignored():
    runs = [{"status": "completed", "conclusion": "success", "run_started_at": "garbage"}]
    ok, _ = classify_dispatch_runs(runs, "morning", _cn(2026, 9, 30, 8, 40))
    assert ok is True


# ── decide（窗口 + fail-open）──

def test_decide_out_of_window_skips_even_with_no_runs():
    # 03:00 不在任何窗口 → 就算 runs 为空也不该跑
    ok, why = decide("morning", now_utc=_cn(2026, 9, 30, 3, 0), runs=[])
    assert ok is False
    assert "滑出" in why


def test_decide_fail_open_on_fetch_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr("src.cron_guard.fetch_dispatch_runs", boom)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "x/y")
    ok, why = decide("morning", now_utc=_cn(2026, 9, 30, 8, 40))
    assert ok is True           # fail-open：宁可重复不可漏推
    assert "fail-open" in why


def test_decide_missing_token_fails_open(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    ok, why = decide("morning", now_utc=_cn(2026, 9, 30, 8, 40))
    assert ok is True
    assert "fail-open" in why


def test_decide_with_injected_runs_skip():
    runs = [_run(_cn(2026, 9, 30, 12, 4))]
    ok, _ = decide("midday", now_utc=_cn(2026, 9, 30, 12, 10), runs=runs)
    assert ok is False


def test_slots_cover_all_cron_modes():
    """cron 表里的每个 mode 都必须有窗口定义（防手滑改漏）。"""
    from src.cron_guard import CRON_UTC
    modes = {m for _, m in CRON_UTC}
    assert modes == {"morning", "midday", "closing", "evening", "sat_morning", "sun_evening"}
    assert modes <= set(SLOT_WINDOWS)
