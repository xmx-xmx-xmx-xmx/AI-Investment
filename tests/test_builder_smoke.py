# -*- coding: utf-8 -*-
"""六时段 builder 冒烟测试 —— 「只在生产暴露」类 bug 的兜底网。

背景（2026-10-03 事故）
    `_build_sat_morning` 在规则 2 接线处调用了 `get_feishu_client_or_none()`，
    却漏写函数内局部导入。**平日时段的测试完全没有覆盖周末 builder**，于是这个
    `NameError` 一路活到周六 09:00 首次真跑才引爆 —— 周六复盘卡整卡丢推；又因为
    `cron_guard` 的补跑窗口已经滑出，当天再也没有任何机会补上。

本文件的作用
    对 `BRIEFINGS` 里**每一个** slot 逐个调用它的 builder。任何"生产才暴露"的问题
    （未定义名 / 导入缺失 / 桩缺失 / 签名不匹配 / 路由表漏项）都会在 CI 里立刻炸，
    而不是等到那个时段真的轮到时才炸。

两条硬要求
    1. **只调 builder，绝不调 `main()`** —— 只有 `main()` 会写飞书、推群。
    2. **全部外部出口打桩** —— 网络（akshare / yfinance）、飞书、LLM、日历。
       漏掉任何一个都会真打网络（历史事故：漏 mock 曾让单测跑出 25.8s）。

断言刻意宽松（返回非空 str 即可）：本文件只负责"不崩"，
各块内容由 test_rules_engine / test_peer_rank / test_briefing_diff 等分别断言。
"""

from __future__ import annotations

import os
import time

import pytest

SLOTS = ["morning", "midday", "closing", "evening", "sat_morning", "sun_evening"]

# `_build_asia_pacific_market` / `_build_global_market_snapshot` 会**直接 pop**
# 这些键来绕过代理。撞上之后必须原样还原，否则会悄悄改掉跑测试的人的环境。
_PROXY_KEYS = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")


class _MarketDataStub:
    """行情出口一律返回 None。

    之所以不用 MagicMock：builder 里有大量 `f"{x['close']:.2f}"`，而 MagicMock 的
    `__format__` 拿到 `.2f` 会直接 TypeError。返回 None 则会走代码里现成的
    `if not data:` 降级分支 —— 这正是离线冒烟想要的路径。
    """

    def __getattr__(self, _name):
        return lambda *a, **k: None


def _stub(monkeypatch, value, *targets):
    """在多个点路径上同时打桩。

    顶层 `from x import y` 与函数内 `from x import y` 会各留一份绑定，
    只打源模块会漏掉顶层那份（反之亦然），所以两处都要打。
    `raising=False` 让"该名字本就不存在"（如 briefing 顶层未导入的）静默跳过。
    """
    for t in targets:
        monkeypatch.setattr(t, value, raising=False)


@pytest.fixture
def smoke_env(monkeypatch):
    """把所有外部出口换成桩：零网络、零飞书、零 LLM 调用。"""
    saved = {k: os.environ.get(k) for k in _PROXY_KEYS}
    try:
        import src.briefing as b

        # ── 行情 ──
        monkeypatch.setattr(b, "market_data", _MarketDataStub(), raising=False)

        # ── akshare / yfinance：返回 None → 调用处 AttributeError 被 except 吞 ──
        _stub(monkeypatch, lambda: None, "src.net_guard.import_ak", "src.net_guard.import_yf")

        # ── 新闻 ──
        _stub(monkeypatch, lambda *a, **k: [],
              "src.briefing.fetch_all_news", "src.news_fetcher.fetch_all_news")
        _stub(monkeypatch, lambda *a, **k: [],
              "src.briefing._filter_by_keywords", "src.news_fetcher._filter_by_keywords")
        _stub(monkeypatch, lambda *a, **k: "",
              "src.briefing._clean_html", "src.news_fetcher._clean_html")
        _stub(monkeypatch, lambda *a, **k: [], "src.news_fetcher.curate_for_display")
        _stub(monkeypatch, lambda *a, **k: "", "src.news_mapper.build_news_links_block")

        # ── LLM（不打桩就会真扣 token）──
        monkeypatch.setattr(b, "_ai_insight", lambda *a, **k: "（离线桩）", raising=False)

        # ── 飞书 ──
        _stub(monkeypatch, lambda: None, "src.feishu_client.get_feishu_client_or_none")
        _stub(monkeypatch, lambda *a, **k: None, "src.feishu_client.read_briefing_snapshot")
        _stub(monkeypatch, lambda *a, **k: None, "src.feishu_client.write_briefing_snapshot")
        monkeypatch.setattr(b, "_save_snapshot", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(b, "_load_prev_snapshot", lambda *a, **k: None, raising=False)

        # ── 持仓 ──
        _stub(monkeypatch, lambda *a, **k: [],
              "src.briefing.load_portfolio", "src.advisor.load_portfolio")
        _stub(monkeypatch, lambda *a, **k: {"total_value": 0.0, "deviation_report": []},
              "src.briefing.calculate_rebalance", "src.advisor.calculate_rebalance")

        # ── 节假日：统一按"开市"给，让 builder 走正常构建路径而不是 SKIP ──
        _stub(monkeypatch, lambda *a, **k: "",
              "src.briefing.holiday_notice", "src.holiday_gate.holiday_notice")
        for n in ("is_cn_market_open", "is_hk_market_open", "is_us_market_open"):
            _stub(monkeypatch, lambda *a, **k: True,
                  f"src.briefing.{n}", f"src.holiday_gate.{n}")

        # ── 雷达 ──
        _stub(monkeypatch, lambda *a, **k: [], "src.radar.scan_radar")
        _stub(monkeypatch, lambda *a, **k: "", "src.radar.build_radar_brief")
        _stub(monkeypatch, lambda *a, **k: [], "src.radar._fetch_historical_prices")

        # ── 全球新闻 ──
        _stub(monkeypatch, lambda *a, **k: "", "src.global_news._build_global_news_brief")
        _stub(monkeypatch, lambda *a, **k: "", "src.global_news.build_global_news_for_ai")

        # ── 策略 ──
        # 形状完整的最小 verdict：closing 会硬取 verdict['total_value']，
        # 其余各处都是 .get()，给全了才能走通正常构建路径。
        _stub(monkeypatch, lambda *a, **k: {
            "total_value": 0.0,
            "signals": [],
            "health_report": "",
            "overall_verdict": "HOLD",
            "priority_target": "",
            "exchange_rates": {},
        }, "src.strategy.judge_from_feishu")

        # ── 宏观日历 ──
        for n in ("fetch_today_calendar", "filter_portfolio_relevant",
                  "fetch_past_calendar", "fetch_upcoming_calendar"):
            _stub(monkeypatch, lambda *a, **k: [],
                  f"src.briefing.{n}", f"src.macro_calendar.{n}")
        for n in ("format_macro_signal_line", "calendar_context_for_prompt"):
            _stub(monkeypatch, lambda *a, **k: "",
                  f"src.briefing.{n}", f"src.macro_calendar.{n}")

        # ── 财报日历 ──
        for n in ("fetch_yesterdays_earnings", "fetch_weekly_earnings"):
            _stub(monkeypatch, lambda *a, **k: [], f"src.earnings_calendar.{n}")
        for n in ("format_yesterdays_earnings", "format_weekly_earnings"):
            _stub(monkeypatch, lambda *a, **k: "", f"src.earnings_calendar.{n}")

        # ── 同类分位（规则 2，仅周六）──
        _stub(monkeypatch, lambda *a, **k: "", "src.peer_rank.build_peer_alert")

        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.mark.parametrize("slot", SLOTS)
def test_builder_produces_card(smoke_env, slot):
    """每个时段的 builder 都能在离线桩下构建出非空字符串。"""
    from src.briefing import BRIEFINGS

    title, builder = BRIEFINGS[slot]
    card = builder()

    assert isinstance(card, str), f"{slot} 返回了非字符串：{type(card)}"
    assert card.strip(), f"{slot} 返回空卡片（{title}）"
    assert card != "SKIP", f"{slot} 在桩环境下被判为休市，正常路径未被覆盖"


@pytest.mark.parametrize("slot", SLOTS)
def test_builder_is_offline(smoke_env, slot):
    """防回归：桩漏一个就会真打网络（历史事故：单测跑出 25.8s）。

    纯桩路径应是毫秒级；这里给 20s 的宽松上限，只为抓住"某个出口没打桩、
    真的走了 DNS/TLS/超时"这类事故 —— 它们在 CI 上表现为耗时暴涨。
    """
    from src.briefing import BRIEFINGS

    _, builder = BRIEFINGS[slot]
    t0 = time.monotonic()
    builder()
    elapsed = time.monotonic() - t0
    assert elapsed < 20, f"{slot} 耗时 {elapsed:.1f}s，疑似有出口未打桩、真打了网络"


def test_every_slot_has_builder():
    """路由表的每个 slot 都必须指向可调用对象（防写错/漏项）。"""
    from src.briefing import BRIEFINGS

    for slot in SLOTS:
        assert slot in BRIEFINGS, f"路由表缺少 slot: {slot}"
        title, builder = BRIEFINGS[slot]
        assert title, f"{slot} 缺少标题"
        assert callable(builder), f"{slot} 的 builder 不可调用"


def test_sat_morning_covers_the_1003_regression(smoke_env):
    """事故本体回归：`_build_sat_morning` 必须能独立构建（2026-10-03 NameError）。

    这是本文件存在的直接理由 —— 单独点名它，让将来的人在 CI 红的时候
    一眼看到"就是这个时段、就是这类原因"。
    """
    from src.briefing import BRIEFINGS

    _, builder = BRIEFINGS["sat_morning"]
    assert isinstance(builder(), str)
