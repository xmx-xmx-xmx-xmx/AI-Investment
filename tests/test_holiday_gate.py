# -*- coding: utf-8 -*-
"""节假日前置熔断测试（2026-09-23，TODO #28）。

覆盖三件事：

1. **三市场日历判断** —— A股(XSHG) / 港股(XHKG) / 美股(XNYS)。
   港股是 2026-09-23 审计时才补上的（此前完全没有，见 TODO §1.12）。

2. **日期越界兜底** —— exchange-calendars 的 XSHG 节假日**只记录到 2026 年底**，
   2027-01-01 起 `is_session()` 会抛 `DateOutOfBounds`。本模块必须**降级而不是抛异常**：
   `pending_resolver` 是流水线第一步且每次运行都调用它，一抛就整条失败。

3. **`holiday_notice()` 展示口径** —— 只有**部分市场**休市才提示；
   周末/长假全部休市是常态，不该在卡片上提醒。

⚠️ 所有断言都用**显式日期**，绝不依赖"今天"，否则会变成时间炸弹。
⚠️ 日期值取自 exchange_calendars 实测，不是猜的。
"""

from __future__ import annotations

import logging
from datetime import date

import pytest

from src import holiday_gate as hg


@pytest.fixture(autouse=True)
def _clean_calendar_cache():
    """每个用例前后清空日历与降级状态，避免缓存/告警去重互相污染。"""
    hg._reset_calendar_cache()
    yield
    hg._reset_calendar_cache()


# ═══════════════════════════════════════════════════════════════
# 1. 三市场日历判断（真实日期）
# ═══════════════════════════════════════════════════════════════

class TestMarketOpen:
    def test_mid_autumn_cn_closed_hk_us_open(self):
        """2026-09-25 周五 · 中秋节：只有 A 股休市。"""
        d = date(2026, 9, 25)
        assert hg.is_cn_market_open(d) is False
        assert hg.is_hk_market_open(d) is True
        assert hg.is_us_market_open(d) is True

    def test_christmas_hk_us_closed_cn_open(self):
        """2026-12-25 周五 · 圣诞：港股 + 美股休市，A 股反而开市。"""
        d = date(2026, 12, 25)
        assert hg.is_cn_market_open(d) is True
        assert hg.is_hk_market_open(d) is False
        assert hg.is_us_market_open(d) is False

    def test_hk_only_holiday(self):
        """2026-10-19 周一：港股休市但 A 股开市。

        这正是"港股没接日历"会误导人的场景 —— midday/closing 被 CN 门控放行，
        港股块却拿着上一交易日的数据。见 TODO §1.12 缺口 ①。
        """
        d = date(2026, 10, 19)
        assert hg.is_cn_market_open(d) is True
        assert hg.is_hk_market_open(d) is False
        assert hg.is_us_market_open(d) is True

    def test_normal_trading_day_nothing_closed(self):
        assert hg.closed_markets(date(2026, 9, 24)) == []

    def test_weekend_all_closed(self):
        assert hg.closed_markets(date(2026, 9, 26)) == ["A股", "港股", "美股"]

    def test_generic_market_argument_matches_wrappers(self):
        d = date(2026, 9, 25)
        assert hg.is_market_open("cn", d) == hg.is_cn_market_open(d)
        assert hg.is_market_open("hk", d) == hg.is_hk_market_open(d)
        assert hg.is_market_open("us", d) == hg.is_us_market_open(d)


# ═══════════════════════════════════════════════════════════════
# 2. 下一交易日
# ═══════════════════════════════════════════════════════════════

class TestNextTradingDay:
    def test_trading_day_returns_itself(self):
        """d 本身是交易日 → 返回 d（含 d 语义，pending_resolver 依赖这点）。"""
        assert hg.next_cn_trading_day(date(2026, 9, 24)) == date(2026, 9, 24)

    def test_mid_autumn_skips_weekend(self):
        """中秋周五 → 顺延到周一。"""
        assert hg.next_cn_trading_day(date(2026, 9, 25)) == date(2026, 9, 28)

    def test_national_day_seven_day_gap(self):
        """国庆长假：10/1 → 10/8（连休 7 天，验证 30 天扫描窗口足够）。"""
        assert hg.next_cn_trading_day(date(2026, 10, 1)) == date(2026, 10, 8)

    def test_weekend_only_gap(self):
        assert hg.next_cn_trading_day(date(2026, 9, 19)) == date(2026, 9, 21)


# ═══════════════════════════════════════════════════════════════
# 3. 越界兜底（XSHG 只记录到 2026 年底）
# ═══════════════════════════════════════════════════════════════

class TestOutOfBoundsFallback:
    def test_boundary_day_still_uses_calendar(self):
        """2026-12-31 仍在日历范围内 → 必须走日历，不能降级。"""
        assert hg.is_cn_market_open(date(2026, 12, 31)) is True

    def test_does_not_raise_after_calendar_bound(self):
        """2027 年起不得抛 DateOutOfBounds。"""
        for d in (date(2027, 1, 1), date(2027, 1, 6), date(2027, 3, 1)):
            assert isinstance(hg.is_cn_market_open(d), bool)

    def test_falls_back_to_weekday_after_bound(self):
        assert hg.is_cn_market_open(date(2027, 1, 6)) is True   # 周三
        assert hg.is_cn_market_open(date(2027, 1, 9)) is False  # 周六

    def test_next_trading_day_after_bound(self):
        assert hg.next_cn_trading_day(date(2027, 1, 1)) == date(2027, 1, 4)  # 周五 → 周一
        assert hg.next_cn_trading_day(date(2027, 1, 6)) == date(2027, 1, 7)

    def test_warns_exactly_once_per_market(self, caplog):
        """越界告警必须去重 —— 每次运行都刷一遍会淹没真正的问题。"""
        with caplog.at_level(logging.WARNING):
            hg.is_cn_market_open(date(2027, 1, 6))
            hg.is_cn_market_open(date(2027, 3, 1))
            hg.next_cn_trading_day(date(2027, 5, 5))
        warns = [r for r in caplog.records if "越界" in r.getMessage()]
        assert len(warns) == 1

    def test_hk_and_us_calendars_still_in_range(self):
        """对比：XHKG / XNYS 记录到 2027-09，2027 年初**不该**降级。"""
        assert hg.is_hk_market_open(date(2027, 1, 6)) is True
        assert hg.is_us_market_open(date(2027, 1, 6)) is True


# ═══════════════════════════════════════════════════════════════
# 4. 日历整体不可用时的降级
# ═══════════════════════════════════════════════════════════════

class TestCalendarUnavailableFallback:
    def test_get_calendar_failure_degrades_to_weekday(self, monkeypatch):
        import exchange_calendars as ec

        def boom(*_a, **_k):
            raise RuntimeError("simulated: no calendar data")

        monkeypatch.setattr(ec, "get_calendar", boom)
        hg._reset_calendar_cache()

        # 节假日识别不出（中秋周五被当普通工作日）—— 这是降级的代价
        assert hg.is_cn_market_open(date(2026, 9, 25)) is True
        assert hg.is_hk_market_open(date(2026, 9, 25)) is True
        # 但周末仍能识别
        assert hg.is_cn_market_open(date(2026, 9, 26)) is False

    def test_next_trading_day_degrades_to_weekday(self, monkeypatch):
        import exchange_calendars as ec

        def boom(*_a, **_k):
            raise RuntimeError("simulated: no calendar data")

        monkeypatch.setattr(ec, "get_calendar", boom)
        hg._reset_calendar_cache()
        # 降级的代价：只认周末，认不出节假日 → 中秋（周五）被当普通交易日返回自身
        assert hg.next_cn_trading_day(date(2026, 9, 25)) == date(2026, 9, 25)
        # 但周末仍会被跳过
        assert hg.next_cn_trading_day(date(2026, 9, 26)) == date(2026, 9, 28)


# ═══════════════════════════════════════════════════════════════
# 5. holiday_notice() 展示口径
# ═══════════════════════════════════════════════════════════════

class TestHolidayNotice:
    def test_partial_closure_cn_only(self):
        assert hg.holiday_notice(date(2026, 9, 25)) == \
            "**🏖️ 今日休市**：A股（港股、美股 正常交易）"

    def test_partial_closure_hk_and_us(self):
        assert hg.holiday_notice(date(2026, 12, 25)) == \
            "**🏖️ 今日休市**：港股、美股（A股 正常交易）"

    def test_all_open_returns_empty(self):
        assert hg.holiday_notice(date(2026, 9, 24)) == ""

    def test_all_closed_returns_empty(self):
        """全部休市（周末/长假）是常态，不该在卡片上提醒。"""
        assert hg.holiday_notice(date(2026, 9, 26)) == ""


# ═══════════════════════════════════════════════════════════════
# 6. 接入行为（briefing 层）
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def _no_network(monkeypatch):
    """切断 briefing 里的外部抓取，并保护被就地 pop 的代理环境变量。"""
    import os

    import src.net_guard as ng

    proxy_keys = ("http_proxy", "https_proxy", "HTTP_PROXY",
                  "HTTPS_PROXY", "all_proxy", "ALL_PROXY")
    saved = {k: os.environ.get(k) for k in proxy_keys}

    def boom(*_a, **_k):
        raise RuntimeError("no network in test")

    monkeypatch.setattr(ng, "import_ak", boom)
    monkeypatch.setattr(ng, "import_yf", boom)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class TestBriefingWiring:
    def test_asia_pacific_hk_closed_shows_notice_not_stale_prices(
        self, monkeypatch, _no_network
    ):
        """港股休市 → 亚太块显式标注休市，不再引用上一交易日的恒生数据。"""
        from src import briefing

        monkeypatch.setattr(briefing, "is_hk_market_open", lambda *a, **k: False)
        out = briefing._build_asia_pacific_market()

        assert "港股" in out
        assert "今日休市" in out
        assert "恒生指数:" not in out          # 不得出现陈旧数字行

    def test_asia_pacific_hk_open_keeps_price_section(self, monkeypatch, _no_network):
        """港股开市 → 正常走抓取分支（拿不到数据时也不该误报"休市"）。"""
        from src import briefing

        monkeypatch.setattr(briefing, "is_hk_market_open", lambda *a, **k: True)
        out = briefing._build_asia_pacific_market()
        assert "今日休市" not in out

    def test_global_snapshot_marks_stale_hk(self, monkeypatch, _no_network):
        """全球市场块：港股休市时恒生行要带「（上一交易日）」标注，开市时不能带。"""
        import pandas as pd

        import src.net_guard as ng
        from src import briefing

        class _FakeAk:
            def stock_hk_index_spot_sina(self):
                return pd.DataFrame([
                    {"名称": "恒生指数", "最新价": 26000.0, "涨跌幅": 1.23},
                    {"名称": "恒生科技", "最新价": 5800.0, "涨跌幅": -0.45},
                ])

        monkeypatch.setattr(ng, "import_ak", lambda *a, **k: _FakeAk())
        monkeypatch.setattr(briefing.market_data, "fetch_us_index", lambda *a, **k: None)
        monkeypatch.setattr(briefing.market_data, "fetch_us_etf", lambda *a, **k: None)

        monkeypatch.setattr(briefing, "is_hk_market_open", lambda *a, **k: False)
        closed_out = briefing._build_global_market_snapshot()
        assert "恒生指数" in closed_out
        assert closed_out.count("（上一交易日）") == 2  # 恒生指数 + 恒生科技

        monkeypatch.setattr(briefing, "is_hk_market_open", lambda *a, **k: True)
        open_out = briefing._build_global_market_snapshot()
        assert "恒生指数" in open_out
        assert "（上一交易日）" not in open_out


# ═══════════════════════════════════════════════════════════════
# 7. 死代码不得复活（TODO #28 的回归锁）
# ═══════════════════════════════════════════════════════════════

class TestDeadCodeRemoved:
    def test_briefing_dead_code_gone(self):
        from src import briefing

        assert not hasattr(briefing, "_should_skip")
        assert not hasattr(briefing, "_US_GATED")

    def test_holiday_gate_dead_code_gone(self):
        assert not hasattr(hg, "market_status")
        assert not hasattr(hg, "next_us_trading_day")
        assert not hasattr(hg, "tz_cn")
