"""
Tests for the macro economic calendar module (macro_calendar.py).

Tests cover filtering logic, formatting, priority detection,
holding sensitivity matching, impact level documentation,
and edge cases — all without network calls (mocked).
"""

from __future__ import annotations

import pytest
from datetime import date, timedelta

from src.macro_calendar import (
    _is_high_priority,
    _parse_date,
    _match_sensitivity,
    format_calendar_for_brief,
    calendar_context_for_prompt,
    generate_holding_warnings,
    fetch_today_calendar,
    fetch_upcoming_calendar,
    fetch_calendar,
    IMPACT_STARS,
    IMPACT_LEVELS,
    COUNTRY_TO_ASSET_CLASS,
    HIGH_PRIORITY_KEYWORDS,
    EVENT_SENSITIVITY,
)


# ═══════════════════════════════════════════════════════════════
# _is_high_priority —— 关键词命中检测
# ═══════════════════════════════════════════════════════════════

class TestIsHighPriority:
    """高优先级关键词检测 —— 纯函数，无需 mock。"""

    def test_cpi_hit(self):
        assert _is_high_priority("US CPI m/m") is True
        assert _is_high_priority("核心CPI年率") is True

    def test_fomc_hit(self):
        assert _is_high_priority("FOMC Meeting Minutes") is True
        assert _is_high_priority("美联储利率决议") is True

    def test_pboc_hit(self):
        assert _is_high_priority("PBOC LPR 1Y") is True
        assert _is_high_priority("央行降准公告") is True

    def test_gdp_hit(self):
        assert _is_high_priority("US GDP q/q") is True
        assert _is_high_priority("GDP年化季率") is True

    def test_nfp_hit(self):
        assert _is_high_priority("Nonfarm Payrolls") is True
        assert _is_high_priority("非农就业人数变化") is True

    def test_pmi_hit(self):
        assert _is_high_priority("Manufacturing PMI") is True

    def test_case_insensitive(self):
        """大小写不敏感"""
        assert _is_high_priority("us cpi m/m") is True
        assert _is_high_priority("fomc minutes") is True

    def test_no_match(self):
        """无关标题不命中"""
        assert _is_high_priority("Bank Holiday") is False
        assert _is_high_priority("Current Account") is False
        assert _is_high_priority("") is False

    def test_mlf_hit(self):
        assert _is_high_priority("央行MLF操作") is True
        assert _is_high_priority("逆回购到期") is True

    def test_retail_hit(self):
        assert _is_high_priority("Retail Sales m/m") is True
        assert _is_high_priority("消费者信心指数") is True


# ═══════════════════════════════════════════════════════════════
# _match_sensitivity —— 事件→持仓敏感度匹配
# ═══════════════════════════════════════════════════════════════

class TestMatchSensitivity:
    """细粒度持仓敏感度匹配 —— 核心新功能。"""

    def test_cpi_matches_nasdaq_holding(self):
        """CPI事件 → 纳指持仓（命中"通胀/物价"组 + "纳斯达克"信号）"""
        matches = _match_sensitivity("US CPI m/m", "景顺长城纳斯达克科技")
        groups = {m["group"] for m in matches}
        assert "通胀/物价" in groups

    def test_cpi_matches_gold_holding(self):
        """CPI事件 → 黄金持仓"""
        matches = _match_sensitivity("US CPI m/m", "黄金ETF联接")
        groups = {m["group"] for m in matches}
        assert "通胀/物价" in groups

    def test_cpi_matches_bond_holding(self):
        """CPI事件 → 债基持仓"""
        matches = _match_sensitivity("US CPI m/m", "建信短债债券C")
        groups = {m["group"] for m in matches}
        assert "通胀/物价" in groups

    def test_fomc_matches_hstech_holding(self):
        """FOMC事件 → 港股持仓"""
        matches = _match_sensitivity("FOMC Statement", "腾讯控股")
        groups = {m["group"] for m in matches}
        assert "美联储/利率决议" in groups

    def test_lpr_matches_ashare_holding(self):
        """LPR事件 → A股持仓"""
        matches = _match_sensitivity("PBOC LPR 1Y", "沪深300ETF")
        groups = {m["group"] for m in matches}
        assert "中国央行/货币政策" in groups

    def test_lpr_matches_bond_holding(self):
        """LPR事件 → 债基（中国货币政策影响债市）"""
        matches = _match_sensitivity("PBOC LPR 1Y", "建信短债债券C")
        groups = {m["group"] for m in matches}
        assert "中国央行/货币政策" in groups

    def test_pmi_matches_ashare_holding(self):
        """PMI事件 → A股持仓"""
        matches = _match_sensitivity("Manufacturing PMI", "上证50ETF")
        groups = {m["group"] for m in matches}
        assert "PMI/制造业" in groups

    def test_no_match_if_event_not_sensitive(self):
        """非敏感事件不匹配任何持仓"""
        matches = _match_sensitivity("Bank Holiday", "景顺长城纳斯达克科技")
        assert matches == []

    def test_no_match_if_holding_not_in_signals(self):
        """CPI事件 → 无关键信号的持仓不匹配"""
        matches = _match_sensitivity("US CPI m/m", "某未映射ETF")
        assert matches == []

    def test_nfp_matches_sp500_holding(self):
        """非农事件 → 标普500持仓"""
        matches = _match_sensitivity("Nonfarm Payrolls", "标普500ETF")
        groups = {m["group"] for m in matches}
        assert "就业/劳动力" in groups

    def test_gdp_matches_hstech_holding(self):
        """GDP事件 → 港股持仓"""
        matches = _match_sensitivity("US GDP q/q", "阿里健康")
        groups = {m["group"] for m in matches}
        assert "GDP/经济增长" in groups

    def test_retail_matches_sp500_holding(self):
        """零售销售事件 → 标普持仓"""
        matches = _match_sensitivity("Retail Sales m/m", "标普500ETF联接")
        groups = {m["group"] for m in matches}
        assert "零售/消费" in groups

    def test_sensitivity_has_required_fields(self):
        """所有敏感度条目都有必要字段"""
        for sens in EVENT_SENSITIVITY:
            assert "group" in sens
            assert "keywords" in sens
            assert "affected_assets" in sens
            assert "holding_signals" in sens
            assert "impact_note" in sens
            assert "direction" in sens
            assert len(sens["keywords"]) > 0
            assert len(sens["holding_signals"]) > 0


# ═══════════════════════════════════════════════════════════════
# generate_holding_warnings —— 持仓预警生成
# ═══════════════════════════════════════════════════════════════

class TestGenerateHoldingWarnings:
    """持仓预警生成 —— 交叉匹配事件与持仓。"""

    EVENTS = [
        {
            "title": "US CPI m/m",
            "country": "USD",
            "date": "2026-06-20T08:30:00-04:00",
            "impact": "High",
            "forecast": "0.3%",
            "previous": "0.2%",
            "stars": "★★★",
            "is_high_priority": True,
            "asset_class": "美股资产",
            "sensitivity_group": "通胀/物价",
        },
        {
            "title": "PBOC LPR 1Y",
            "country": "CNY",
            "date": "2026-06-20T09:15:00+08:00",
            "impact": "High",
            "forecast": "3.1%",
            "previous": "3.1%",
            "stars": "★★★",
            "is_high_priority": True,
            "asset_class": "A股资产",
            "sensitivity_group": "中国央行/货币政策",
        },
        {
            "title": "Bank Holiday",
            "country": "CHF",
            "date": "2026-06-20T00:00:00",
            "impact": "Holiday",
            "forecast": "",
            "previous": "",
            "stars": "",
            "is_high_priority": False,
            "asset_class": "",
            "sensitivity_group": "",
        },
    ]

    PORTFOLIO = [
        {"name": "景顺长城纳斯达克科技", "asset_class": "美股资产"},
        {"name": "建信短债债券C", "asset_class": "固收资产"},
        {"name": "黄金ETF联接", "asset_class": "避险商品"},
        {"name": "沪深300ETF联接", "asset_class": "A股资产"},
    ]

    def test_cpi_warns_nasdaq(self):
        """CPI → 纳指持仓生成预警"""
        warnings = generate_holding_warnings(self.EVENTS, self.PORTFOLIO)
        nasdaq_warnings = [w for w in warnings if "纳斯达克" in w["holding_name"]]
        assert len(nasdaq_warnings) > 0
        assert any("CPI" in w["event_title"] for w in nasdaq_warnings)

    def test_cpi_warns_gold(self):
        """CPI → 黄金持仓生成预警"""
        warnings = generate_holding_warnings(self.EVENTS, self.PORTFOLIO)
        gold_warnings = [w for w in warnings if "黄金" in w["holding_name"]]
        assert len(gold_warnings) > 0

    def test_cpi_warns_bond(self):
        """CPI → 债基生成预警"""
        warnings = generate_holding_warnings(self.EVENTS, self.PORTFOLIO)
        bond_warnings = [w for w in warnings if "债券" in w["holding_name"] or "债基" in w["holding_name"] or "短债" in w["holding_name"]]
        assert len(bond_warnings) > 0

    def test_lpr_warns_ashare(self):
        """LPR → A股持仓生成预警"""
        warnings = generate_holding_warnings(self.EVENTS, self.PORTFOLIO)
        ashare_warnings = [w for w in warnings if "沪深300" in w["holding_name"]]
        assert len(ashare_warnings) > 0
        assert any("LPR" in w["event_title"] for w in ashare_warnings)

    def test_bank_holiday_no_warning(self):
        """非敏感事件不生成预警"""
        warnings = generate_holding_warnings(self.EVENTS, self.PORTFOLIO)
        holiday_warnings = [w for w in warnings if "Holiday" in w["event_title"]]
        assert len(holiday_warnings) == 0

    def test_empty_events(self):
        warnings = generate_holding_warnings([], self.PORTFOLIO)
        assert warnings == []

    def test_empty_portfolio(self):
        warnings = generate_holding_warnings(self.EVENTS, [])
        assert warnings == []

    def test_warning_structure(self):
        """验证预警结构字段完整"""
        warnings = generate_holding_warnings(self.EVENTS, self.PORTFOLIO)
        for w in warnings:
            assert "holding_name" in w
            assert "event_title" in w
            assert "event_stars" in w
            assert "sensitivity_group" in w
            assert "impact_note" in w
            assert "direction" in w


# ═══════════════════════════════════════════════════════════════
# _parse_date —— 日期解析
# ═══════════════════════════════════════════════════════════════

class TestParseDate:
    """日期解析 —— 处理 ISO 带时区格式和纯日期格式。"""

    def test_iso_with_tz(self):
        result = _parse_date("2026-06-15T23:19:00-04:00")
        assert result == date(2026, 6, 15)

    def test_iso_with_positive_tz(self):
        result = _parse_date("2026-06-16T08:00:00+08:00")
        assert result == date(2026, 6, 16)

    def test_simple_date(self):
        result = _parse_date("2026-06-20")
        assert result == date(2026, 6, 20)

    def test_empty_string(self):
        assert _parse_date("") is None

    def test_none(self):
        assert _parse_date(None) is None  # type: ignore[arg-type]

    def test_invalid(self):
        assert _parse_date("not-a-date") is None


# ═══════════════════════════════════════════════════════════════
# format_calendar_for_brief —— 简报格式化
# ═══════════════════════════════════════════════════════════════

class TestFormatCalendarForBrief:
    """简报格式化 —— 纯函数测试。"""

    def test_empty_events(self):
        assert format_calendar_for_brief([]) == ""

    def test_single_event(self):
        events = [{
            "title": "US CPI m/m",
            "country": "USD",
            "date": "2026-06-20T08:30:00-04:00",
            "impact": "High",
            "forecast": "0.3%",
            "previous": "0.2%",
            "stars": "★★★",
            "is_high_priority": True,
            "asset_class": "美股资产",
            "sensitivity_group": "通胀/物价",
        }]
        result = format_calendar_for_brief(events)
        assert "📅 今日宏观日历" in result
        assert "★★★" in result
        assert "[USD]" in result
        assert "US CPI m/m" in result
        assert "预期 0.3%" in result
        assert "前值 0.2%" in result

    def test_event_without_forecast_previous(self):
        """无预测值/前值的事件 —— 不显示数据详情"""
        events = [{
            "title": "FOMC Statement",
            "country": "USD",
            "date": "2026-06-20T14:00:00-04:00",
            "impact": "High",
            "forecast": "",
            "previous": "",
            "stars": "★★★",
            "is_high_priority": True,
            "asset_class": "美股资产",
            "sensitivity_group": "美联储/利率决议",
        }]
        result = format_calendar_for_brief(events)
        assert "FOMC Statement" in result
        assert "预期" not in result
        assert "前值" not in result

    def test_max_8_events(self):
        """最多展示 8 条"""
        events = [
            {
                "title": f"Event {i}",
                "country": "USD",
                "date": f"2026-06-20T0{i}:00:00-04:00",
                "impact": "Medium",
                "forecast": "",
                "previous": "",
                "stars": "★★",
                "is_high_priority": False,
                "asset_class": "美股资产",
                "sensitivity_group": "",
            }
            for i in range(15)
        ]
        result = format_calendar_for_brief(events)
        event_lines = [l for l in result.split("\n") if l.startswith("·")]
        assert len(event_lines) == 8

    def test_impact_stars(self):
        """验证星级显示"""
        events = [
            {
                "title": "High Event", "country": "USD",
                "date": "2026-06-20T08:00:00-04:00",
                "impact": "High", "forecast": "", "previous": "",
                "stars": IMPACT_STARS["High"],
                "is_high_priority": True, "asset_class": "美股资产",
                "sensitivity_group": "",
            },
            {
                "title": "Medium Event", "country": "CNY",
                "date": "2026-06-20T10:00:00+08:00",
                "impact": "Medium", "forecast": "", "previous": "",
                "stars": IMPACT_STARS["Medium"],
                "is_high_priority": False, "asset_class": "A股资产",
                "sensitivity_group": "",
            },
        ]
        result = format_calendar_for_brief(events)
        assert "★★★" in result
        assert "★★" in result


# ═══════════════════════════════════════════════════════════════
# calendar_context_for_prompt —— AI Prompt 上下文（含持仓预警）
# ═══════════════════════════════════════════════════════════════

class TestCalendarContextForPrompt:
    """Prompt 上下文生成 —— 含细粒度持仓预警。"""

    EVENTS = [
        {
            "title": "US CPI m/m",
            "country": "USD",
            "date": "2026-06-20T08:30:00-04:00",
            "impact": "High",
            "forecast": "0.3%",
            "previous": "0.2%",
            "stars": "★★★",
            "is_high_priority": True,
            "asset_class": "美股资产",
            "sensitivity_group": "通胀/物价",
        },
    ]

    PORTFOLIO = [
        {"name": "景顺长城纳斯达克科技", "asset_class": "美股资产"},
        {"name": "建信短债债券C", "asset_class": "固收资产"},
    ]

    def test_empty_events(self):
        result = calendar_context_for_prompt([])
        assert "无重大" in result

    def test_single_with_priority_flag(self):
        result = calendar_context_for_prompt(self.EVENTS)
        assert "CPI" in result
        assert "⚠️" in result
        assert "美股资产" in result
        assert "预期:0.3%" in result
        assert "前值:0.2%" in result
        assert "通胀/物价" in result

    def test_with_portfolio_generates_warnings(self):
        """提供 portfolio 时自动生成持仓预警"""
        result = calendar_context_for_prompt(self.EVENTS, self.PORTFOLIO)
        assert "持仓敏感度预警" in result
        assert "纳斯达克" in result
        assert "CPI" in result
        assert "通胀" in result

    def test_with_portfolio_warning_includes_impact_note(self):
        """持仓预警包含影响机制说明"""
        result = calendar_context_for_prompt(self.EVENTS, self.PORTFOLIO)
        # 应有影响机制说明的摘要
        assert "利率" in result or "通胀" in result

    def test_with_empty_portfolio_no_warnings(self):
        """空持仓不生成预警"""
        result = calendar_context_for_prompt(self.EVENTS, [])
        assert "持仓敏感度预警" not in result

    def test_non_priority_no_flag(self):
        events = [{
            "title": "Current Account",
            "country": "EUR",
            "date": "2026-06-20T04:00:00-04:00",
            "impact": "Medium",
            "forecast": "",
            "previous": "",
            "stars": "★★",
            "is_high_priority": False,
            "asset_class": "美股资产",
            "sensitivity_group": "",
        }]
        result = calendar_context_for_prompt(events)
        assert "Current Account" in result
        assert "⚠️" not in result

    def test_no_asset_class(self):
        """无映射的资产大类时不出问题"""
        events = [{
            "title": "Some Event",
            "country": "XXX",
            "date": "2026-06-20T00:00:00",
            "impact": "Medium",
            "forecast": "",
            "previous": "",
            "stars": "★★",
            "is_high_priority": False,
            "asset_class": "",
            "sensitivity_group": "",
        }]
        result = calendar_context_for_prompt(events)
        assert "Some Event" in result

    def test_sensitivity_group_displayed(self):
        """敏感度分组在上下文中可见"""
        result = calendar_context_for_prompt(self.EVENTS)
        assert "[通胀/物价]" in result


# ═══════════════════════════════════════════════════════════════
# 常量验证
# ═══════════════════════════════════════════════════════════════

class TestConstants:
    """验证映射常量的正确性。"""

    def test_impact_stars_coverage(self):
        """所有影响级别都有星级映射"""
        assert "High" in IMPACT_STARS
        assert "Medium" in IMPACT_STARS
        assert "Low" in IMPACT_STARS

    def test_impact_levels_coverage(self):
        """IMPACT_LEVELS 覆盖 High/Medium/Low"""
        for level in ("High", "Medium", "Low"):
            assert level in IMPACT_LEVELS
            val, desc = IMPACT_LEVELS[level]
            assert isinstance(val, int)
            assert isinstance(desc, str)

    def test_country_mapping_covers_portfolio(self):
        """国家映射覆盖了所有资产大类"""
        asset_classes = set(COUNTRY_TO_ASSET_CLASS.values())
        assert "美股资产" in asset_classes
        assert "A股资产" in asset_classes
        assert "港股资产" in asset_classes

    def test_high_priority_keywords_non_empty(self):
        """关键词列表非空"""
        assert len(HIGH_PRIORITY_KEYWORDS) > 5

    def test_event_sensitivity_covers_all_asset_classes(self):
        """敏感度映射覆盖所有持仓资产大类"""
        affected = set()
        for sens in EVENT_SENSITIVITY:
            affected.update(sens["affected_assets"])
        assert "美股资产" in affected
        assert "港股资产" in affected
        assert "A股资产" in affected

    def test_event_sensitivity_groups_unique(self):
        """各组名称不重复"""
        groups = [s["group"] for s in EVENT_SENSITIVITY]
        assert len(groups) == len(set(groups))

    def test_event_sensitivity_groups_count(self):
        """至少有 5 组敏感度映射"""
        assert len(EVENT_SENSITIVITY) >= 5


# ═══════════════════════════════════════════════════════════════
# fetch_calendar —— 集成测试（mock 网络）
# ═══════════════════════════════════════════════════════════════

class TestFetchCalendar:
    """使用 mock 数据测试核心筛选逻辑。"""

    MOCK_RAW = [
        {
            "title": "US CPI m/m",
            "country": "USD",
            "date": "2026-06-20T08:30:00-04:00",
            "impact": "High",
            "forecast": "0.3%",
            "previous": "0.2%",
        },
        {
            "title": "FOMC Statement",
            "country": "USD",
            "date": "2026-06-20T14:00:00-04:00",
            "impact": "High",
            "forecast": "",
            "previous": "",
        },
        {
            "title": "PBOC LPR 1Y",
            "country": "CNY",
            "date": "2026-06-20T09:15:00+08:00",
            "impact": "High",
            "forecast": "3.1%",
            "previous": "3.1%",
        },
        {
            "title": "Current Account",
            "country": "EUR",
            "date": "2026-06-19T04:00:00-04:00",
            "impact": "Low",
            "forecast": "",
            "previous": "",
        },
        {
            "title": "Bank Holiday",
            "country": "CHF",
            "date": "2026-06-20T00:00:00",
            "impact": "Holiday",
            "forecast": "",
            "previous": "",
        },
    ]

    def test_min_impact_medium_filters_low_and_holiday(self, monkeypatch):
        """≥ Medium → 过滤掉 Low 和 Holiday"""
        from src import macro_calendar

        monkeypatch.setattr(macro_calendar, "_fetch_raw_calendar", lambda: self.MOCK_RAW)

        result = macro_calendar.fetch_calendar(min_impact="Medium")
        titles = {e["title"] for e in result}
        assert "US CPI m/m" in titles
        assert "FOMC Statement" in titles
        assert "PBOC LPR 1Y" in titles
        assert "Current Account" not in titles
        assert "Bank Holiday" not in titles

    def test_min_impact_high(self, monkeypatch):
        """仅限 High 影响"""
        from src import macro_calendar

        monkeypatch.setattr(macro_calendar, "_fetch_raw_calendar", lambda: self.MOCK_RAW)

        result = macro_calendar.fetch_calendar(min_impact="High")
        titles = {e["title"] for e in result}
        assert len(titles) == 3
        assert "US CPI m/m" in titles

    def test_country_filter(self, monkeypatch):
        """国家过滤"""
        from src import macro_calendar

        monkeypatch.setattr(macro_calendar, "_fetch_raw_calendar", lambda: self.MOCK_RAW)

        result = macro_calendar.fetch_calendar(min_impact="Medium", countries=["CNY"])
        assert len(result) == 1
        assert result[0]["title"] == "PBOC LPR 1Y"

    def test_date_filter(self, monkeypatch):
        """日期过滤"""
        from src import macro_calendar

        monkeypatch.setattr(macro_calendar, "_fetch_raw_calendar", lambda: self.MOCK_RAW)

        result = macro_calendar.fetch_calendar(
            min_impact="Medium", target_date=date(2026, 6, 20),
        )
        titles = {e["title"] for e in result}
        assert "US CPI m/m" in titles
        assert "FOMC Statement" in titles
        assert "PBOC LPR 1Y" in titles
        assert "Current Account" not in titles

    def test_stars_field_added(self, monkeypatch):
        """返回结果包含 stars 字段"""
        from src import macro_calendar

        monkeypatch.setattr(macro_calendar, "_fetch_raw_calendar", lambda: self.MOCK_RAW)

        result = macro_calendar.fetch_calendar(min_impact="Medium")
        for e in result:
            assert "stars" in e
            assert "is_high_priority" in e
            assert "asset_class" in e
            assert "sensitivity_group" in e

    def test_high_priority_detected(self, monkeypatch):
        """CPI / FOMC / LPR 标记为高优先级"""
        from src import macro_calendar

        monkeypatch.setattr(macro_calendar, "_fetch_raw_calendar", lambda: self.MOCK_RAW)

        result = macro_calendar.fetch_calendar(min_impact="Medium")
        for e in result:
            if e["title"] in ("US CPI m/m", "FOMC Statement", "PBOC LPR 1Y"):
                assert e["is_high_priority"] is True, f"{e['title']} should be high priority"

    def test_asset_class_mapping(self, monkeypatch):
        """正确的资产大类映射"""
        from src import macro_calendar

        monkeypatch.setattr(macro_calendar, "_fetch_raw_calendar", lambda: self.MOCK_RAW)

        result = macro_calendar.fetch_calendar(min_impact="Medium")
        usd_event = next(e for e in result if e["country"] == "USD")
        assert usd_event["asset_class"] == "美股资产"
        cny_event = next(e for e in result if e["country"] == "CNY")
        assert cny_event["asset_class"] == "A股资产"

    def test_sensitivity_group_assigned(self, monkeypatch):
        """CPI 事件被正确分配敏感度分组"""
        from src import macro_calendar

        monkeypatch.setattr(macro_calendar, "_fetch_raw_calendar", lambda: self.MOCK_RAW)

        result = macro_calendar.fetch_calendar(min_impact="Medium")
        cpi_event = next(e for e in result if e["title"] == "US CPI m/m")
        assert cpi_event["sensitivity_group"] == "通胀/物价"

        fomc_event = next(e for e in result if e["title"] == "FOMC Statement")
        assert fomc_event["sensitivity_group"] == "美联储/利率决议"

        lpr_event = next(e for e in result if e["title"] == "PBOC LPR 1Y")
        assert "央行" in lpr_event["sensitivity_group"]

    def test_api_failure_graceful(self, monkeypatch):
        """网络异常时返回空列表，不崩溃"""
        from src import macro_calendar

        monkeypatch.setattr(macro_calendar, "_fetch_raw_calendar", lambda: [])
        result = macro_calendar.fetch_calendar()
        assert result == []


# ═══════════════════════════════════════════════════════════════
# fetch_today_calendar & fetch_upcoming_calendar
# ═══════════════════════════════════════════════════════════════

class TestConvenienceFunctions:
    """便捷函数测试。"""

    def test_fetch_today_calendar_filters_countries(self, monkeypatch):
        """fetch_today_calendar 自动使用关联国家列表"""
        from src import macro_calendar

        today = date.today()
        mock_data = [
            {
                "title": "US Event",
                "country": "USD",
                "date": today.isoformat(),
                "impact": "High",
                "forecast": "",
                "previous": "",
            },
            {
                "title": "China Event",
                "country": "CNY",
                "date": today.isoformat(),
                "impact": "Medium",
                "forecast": "",
                "previous": "",
            },
            {
                "title": "Swiss Event",
                "country": "CHF",
                "date": today.isoformat(),
                "impact": "High",
                "forecast": "",
                "previous": "",
            },
        ]
        monkeypatch.setattr(macro_calendar, "_fetch_raw_calendar", lambda: mock_data)

        result = macro_calendar.fetch_today_calendar()
        countries = {e["country"] for e in result}
        assert "USD" in countries
        assert "CNY" in countries
        assert "CHF" not in countries

    def test_fetch_upcoming_calendar_date_range(self, monkeypatch):
        """fetch_upcoming_calendar 正确限定日期范围"""
        from src import macro_calendar

        today = date.today()
        tomorrow = today + timedelta(days=1)
        far_future = today + timedelta(days=10)

        mock_data = [
            {"title": "Today", "country": "USD", "date": today.isoformat(),
             "impact": "Medium", "forecast": "", "previous": ""},
            {"title": "Tomorrow", "country": "USD", "date": tomorrow.isoformat(),
             "impact": "Medium", "forecast": "", "previous": ""},
            {"title": "Far", "country": "USD", "date": far_future.isoformat(),
             "impact": "Medium", "forecast": "", "previous": ""},
        ]
        monkeypatch.setattr(macro_calendar, "_fetch_raw_calendar", lambda: mock_data)

        result = macro_calendar.fetch_upcoming_calendar(days_ahead=1)
        titles = {e["title"] for e in result}
        assert "Today" in titles
        assert "Tomorrow" in titles
        assert "Far" not in titles
