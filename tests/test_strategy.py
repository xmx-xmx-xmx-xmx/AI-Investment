"""
Tests for the core strategy engine (strategy.py).

All functions tested here are pure — no network, no database,
no Feishu. They exercise the hard math that must be correct.
"""

from __future__ import annotations

import pytest

from src.strategy import (
    _determine_signal,
    _apply_long_bottom_override,
    _apply_left_side_intercept,
    judge,
    THRESHOLD_STRONG_BUY,
    THRESHOLD_BUY,
    THRESHOLD_HOLD_HIGH,
    THRESHOLD_SOFT_WARN,
)


# ═══════════════════════════════════════════════════════════════
# _determine_signal —— 阶梯阈值判定
# ═══════════════════════════════════════════════════════════════

class TestDetermineSignal:
    """阶梯阈值——纯数学判定，关键边界值全覆盖。"""

    def test_strong_buy_far_below(self):
        """远低于目标 → 强烈买入"""
        assert _determine_signal(-0.15) == "TRIGGER_STRONG_BUY"

    def test_strong_buy_just_below(self):
        """刚好低于 -10% → 买入（非强烈）"""
        # deviation < -0.10 is STRONG_BUY, deviation == -0.10 is BUY
        assert _determine_signal(-0.10) == "TRIGGER_BUY"

    def test_buy_mid_range(self):
        """-10% ~ -5% 之间 → 加倍定投"""
        assert _determine_signal(-0.08) == "TRIGGER_BUY"

    def test_buy_upper_boundary(self):
        """刚好 -5% → HOLD（边界值属于正常区间，非 BUY）"""
        # deviation < -0.05 才触发 BUY，-0.05 本身落回 HOLD
        assert _determine_signal(-0.05) == "HOLD_AND_WAIT"

    def test_buy_just_past_boundary(self):
        """-5.01% → 买入（刚过边界）"""
        assert _determine_signal(-0.05001) == "TRIGGER_BUY"

    def test_hold_lower_range(self):
        """-5% ~ +5% 之间 → 维持节奏"""
        assert _determine_signal(-0.04) == "HOLD_AND_WAIT"

    def test_hold_exact_target(self):
        """恰好在目标权重 → 维持"""
        assert _determine_signal(0.0) == "HOLD_AND_WAIT"

    def test_hold_upper_range(self):
        """+5% 以内 → 维持"""
        assert _determine_signal(0.04) == "HOLD_AND_WAIT"

    def test_hold_at_soft_warn_boundary(self):
        """刚好 +5% → 维持（未触发软警告）"""
        assert _determine_signal(0.05) == "HOLD_AND_WAIT"

    def test_soft_warn_mid(self):
        """+5% ~ +10% 之间 → 仍为 HOLD（观察期）"""
        assert _determine_signal(0.08) == "HOLD_AND_WAIT"

    def test_soft_warn_upper_boundary(self):
        """刚好 +10% → HOLD"""
        assert _determine_signal(0.10) == "HOLD_AND_WAIT"

    def test_sell_just_above(self):
        """+10.01% → SELL"""
        assert _determine_signal(0.1001) == "TRIGGER_SELL"

    def test_sell_way_above(self):
        """远超 +10% → 止盈"""
        assert _determine_signal(0.25) == "TRIGGER_SELL"


# ═══════════════════════════════════════════════════════════════
# _apply_long_bottom_override —— 长底仓永不卖出
# ═══════════════════════════════════════════════════════════════

class TestLongBottomOverride:
    """长底仓标签 → 代码锁死卖出，LLM 无法推翻。"""

    def test_no_tag_passes_through(self):
        """无长底仓标签 → 信号原样返回"""
        sig, msg = _apply_long_bottom_override(
            "TRIGGER_SELL", [], "+12.0%", "某ETF", "美股资产"
        )
        assert sig == "TRIGGER_SELL"
        assert msg is None

    def test_long_bottom_sell_to_hold(self):
        """长底仓 + SELL → HOLD + 自然稀释提示"""
        sig, msg = _apply_long_bottom_override(
            "TRIGGER_SELL", ["长期底仓"], "+15.0%", "黄金ETF", "避险商品"
        )
        assert sig == "HOLD_AND_WAIT"
        assert msg is not None
        assert any(w in (msg or "") for w in ["长底仓", "长期底仓", "自然稀释"])
        assert "自然稀释" in msg

    def test_long_bottom_buy_encouraged(self):
        """长底仓 + BUY → 仍然 BUY，附鼓励提示"""
        sig, msg = _apply_long_bottom_override(
            "TRIGGER_BUY", ["长期底仓"], "-8.0%", "小米集团", "港股资产"
        )
        assert sig == "TRIGGER_BUY"
        assert msg is not None
        assert any(w in (msg or "") for w in ["长底仓", "长期底仓", "自然稀释"])

    def test_long_bottom_strong_buy_encouraged(self):
        """长底仓 + STRONG_BUY → 仍然 STRONG_BUY"""
        sig, msg = _apply_long_bottom_override(
            "TRIGGER_STRONG_BUY", ["长期底仓"], "-12.0%", "纳指基金", "美股资产"
        )
        assert sig == "TRIGGER_STRONG_BUY"
        assert msg is not None

    def test_long_bottom_hold_unchanged(self):
        """长底仓 + HOLD → 保持不变"""
        sig, msg = _apply_long_bottom_override(
            "HOLD_AND_WAIT", ["长期底仓"], "+2.0%", "标普基金", "美股资产"
        )
        assert sig == "HOLD_AND_WAIT"
        assert msg is None

    def test_other_tags_dont_trigger(self):
        """其他标签（观察仓等）不触发长底仓逻辑"""
        sig, msg = _apply_long_bottom_override(
            "TRIGGER_SELL", ["观察仓"], "+12.0%", "某ETF", "A股资产"
        )
        assert sig == "TRIGGER_SELL"
        assert msg is None


# ═══════════════════════════════════════════════════════════════
# _apply_left_side_intercept —— 防飞刀拦截
# ═══════════════════════════════════════════════════════════════

class TestLeftSideIntercept:
    """趋势自动检测 → 左侧下跌拦截买入，右侧企稳放行。"""

    def test_left_side_blocks_buy(self):
        """左侧下跌 + BUY → HOLD + 拦截提示"""
        sig, msg = _apply_left_side_intercept(
            "TRIGGER_BUY", [], "左侧下跌", "某ETF"
        )
        assert sig == "HOLD_AND_WAIT"
        assert msg is not None
        assert "左侧下跌" in msg

    def test_left_side_blocks_strong_buy(self):
        """左侧下跌 + STRONG_BUY → HOLD"""
        sig, msg = _apply_left_side_intercept(
            "TRIGGER_STRONG_BUY", [], "左侧下跌", "某ETF"
        )
        assert sig == "HOLD_AND_WAIT"
        assert msg is not None

    def test_left_side_does_not_block_sell(self):
        """左侧下跌不拦截卖出信号"""
        sig, msg = _apply_left_side_intercept(
            "TRIGGER_SELL", [], "左侧下跌", "某ETF"
        )
        assert sig == "TRIGGER_SELL"
        assert msg is None

    def test_left_side_does_not_block_hold(self):
        """左侧下跌不拦截 HOLD"""
        sig, msg = _apply_left_side_intercept(
            "HOLD_AND_WAIT", [], "左侧下跌", "某ETF"
        )
        assert sig == "HOLD_AND_WAIT"
        assert msg is None

    def test_right_stabilized_allows_buy(self):
        """右侧企稳 + BUY → BUY + 入场提示"""
        sig, msg = _apply_left_side_intercept(
            "TRIGGER_BUY", [], "右侧企稳", "某ETF"
        )
        assert sig == "TRIGGER_BUY"
        assert msg is not None
        assert "右侧" in msg

    def test_right_stabilized_allows_strong_buy(self):
        """右侧企稳 + STRONG_BUY → STRONG_BUY"""
        sig, msg = _apply_left_side_intercept(
            "TRIGGER_STRONG_BUY", [], "右侧企稳", "某ETF"
        )
        assert sig == "TRIGGER_STRONG_BUY"
        assert msg is not None

    def test_sideways_no_intercept(self):
        """横盘震荡 → 不拦截"""
        sig, msg = _apply_left_side_intercept(
            "TRIGGER_BUY", [], "横盘震荡", "某ETF"
        )
        assert sig == "TRIGGER_BUY"
        assert msg is None

    def test_empty_trend_no_intercept(self):
        """无趋势数据 → 不拦截"""
        sig, msg = _apply_left_side_intercept(
            "TRIGGER_BUY", [], "", "某ETF"
        )
        assert sig == "TRIGGER_BUY"
        assert msg is None


# ═══════════════════════════════════════════════════════════════
# judge() —— 核心判定引擎集成测试
# ═══════════════════════════════════════════════════════════════

class TestJudge:
    """judge() 集成测试 —— 构造持仓输入，验证输出结构。"""

    def test_empty_portfolio(self):
        """空持仓 → 不崩溃，返回有效结构"""
        result = judge([], client=None)
        assert "overall_verdict" in result
        assert "signals" in result
        assert "command" in result
        assert "psyche_facts" in result
        assert "total_value" in result
        # 空持仓总市值应为接近 0（被 floor 到 0.01）
        assert result["total_value"] >= 0.01

    def test_perfectly_balanced(self):
        """持仓刚好匹配目标权重 → 全部 HOLD"""
        portfolio = [
            {"name": "美股基金", "code": "096001", "asset_class": "美股资产",
             "shares": 2500, "latest_price": 1.0, "cost": 1.0,
             "currency": "CNY", "tags": [], "trend": ""},
            {"name": "A股ETF", "code": "510500", "asset_class": "A股资产",
             "shares": 1000, "latest_price": 1.0, "cost": 1.0,
             "currency": "CNY", "tags": [], "trend": ""},
            {"name": "港股", "code": "00700", "asset_class": "港股资产",
             "shares": 500, "latest_price": 1.0, "cost": 1.0,
             "currency": "CNY", "tags": [], "trend": ""},
            {"name": "黄金", "code": "518880", "asset_class": "避险商品",
             "shares": 1000, "latest_price": 1.0, "cost": 1.0,
             "currency": "CNY", "tags": [], "trend": ""},
            {"name": "债基", "code": "017093", "asset_class": "固收资产",
             "shares": 5000, "latest_price": 1.0, "cost": 1.0,
             "currency": "CNY", "tags": [], "trend": ""},
        ]
        result = judge(portfolio, client=None)
        assert result["overall_verdict"] == "HOLD"
        for s in result["signals"]:
            assert s["signal"] == "HOLD_AND_WAIT"

    def test_single_class_overweight_triggers_sell(self):
        """单类严重超配触发 SELL"""
        portfolio = [
            {"name": "美股基金", "code": "096001", "asset_class": "美股资产",
             "shares": 8000, "latest_price": 1.0, "cost": 1.0,
             "currency": "CNY", "tags": [], "trend": ""},
            {"name": "A股ETF", "code": "510500", "asset_class": "A股资产",
             "shares": 1000, "latest_price": 1.0, "cost": 1.0,
             "currency": "CNY", "tags": [], "trend": ""},
            {"name": "债基", "code": "017093", "asset_class": "固收资产",
             "shares": 1000, "latest_price": 1.0, "cost": 1.0,
             "currency": "CNY", "tags": [], "trend": ""},
        ]
        result = judge(portfolio, client=None)
        # 美股 8000 / 10000 = 80% 远超目标 25%
        us_signal = next(s for s in result["signals"] if s["asset_class"] == "美股资产")
        assert us_signal["signal"] == "TRIGGER_SELL"

    def test_single_class_underweight_triggers_buy(self):
        """单类严重低配触发买入信号"""
        portfolio = [
            {"name": "美股基金", "code": "096001", "asset_class": "美股资产",
             "shares": 100, "latest_price": 1.0, "cost": 1.0,
             "currency": "CNY", "tags": [], "trend": ""},
            {"name": "债基", "code": "017093", "asset_class": "固收资产",
             "shares": 9900, "latest_price": 1.0, "cost": 1.0,
             "currency": "CNY", "tags": [], "trend": ""},
        ]
        result = judge(portfolio, client=None)
        us_signal = next(s for s in result["signals"] if s["asset_class"] == "美股资产")
        # 美股 100 / 10000 = 1% 远低于目标 25%
        assert us_signal["signal"] in ("TRIGGER_BUY", "TRIGGER_STRONG_BUY")

    def test_none_client_no_cooldown(self):
        """client=None 时不检查冷却期（不崩溃）"""
        portfolio = [
            {"name": "美股基金", "code": "096001", "asset_class": "美股资产",
             "shares": 100, "latest_price": 1.0, "cost": 1.0,
             "currency": "CNY", "tags": [], "trend": ""},
            {"name": "债基", "code": "017093", "asset_class": "固收资产",
             "shares": 9900, "latest_price": 1.0, "cost": 1.0,
             "currency": "CNY", "tags": [], "trend": ""},
        ]
        result = judge(portfolio, client=None)
        assert result["overall_verdict"] in ("HOLD", "ACT")

    def test_priority_target_identified(self):
        """优先买入方向正确识别——负偏离最大的类"""
        portfolio = [
            {"name": "A股ETF", "code": "510500", "asset_class": "A股资产",
             "shares": 100, "latest_price": 1.0, "cost": 1.0,
             "currency": "CNY", "tags": [], "trend": ""},
            {"name": "债基", "code": "017093", "asset_class": "固收资产",
             "shares": 9900, "latest_price": 1.0, "cost": 1.0,
             "currency": "CNY", "tags": [], "trend": ""},
        ]
        result = judge(portfolio, client=None)
        # A股 100/10000=1% vs 目标 10%，负偏离 -9%；固收 99% vs 目标 50%，正偏离
        # 优先目标应是偏离为负且最小的
        assert result["priority_target"] is not None
        assert result["priority_deviation"] < 0

    def test_long_bottom_prevents_sell_in_judge(self):
        """judge() 集成——长底仓类 SELL 降级为 HOLD"""
        portfolio = [
            {"name": "黄金ETF", "code": "518880", "asset_class": "避险商品",
             "shares": 5000, "latest_price": 1.0, "cost": 1.0,
             "currency": "CNY", "tags": ["长期底仓"], "trend": ""},
            {"name": "债基", "code": "017093", "asset_class": "固收资产",
             "shares": 5000, "latest_price": 1.0, "cost": 1.0,
             "currency": "CNY", "tags": [], "trend": ""},
        ]
        result = judge(portfolio, client=None)
        gold_signal = next(s for s in result["signals"] if s["asset_class"] == "避险商品")
        # 黄金 5000/10000=50% vs 目标 10%，正偏离 +40%，应 SELL
        # 但被长底仓标签覆盖，降为 HOLD
        assert gold_signal["signal"] == "HOLD_AND_WAIT"
        assert gold_signal["override"] is not None
        assert "长底仓" in gold_signal["override"] or "自然稀释" in gold_signal["override"]

    def test_left_side_blocks_buy_in_judge(self):
        """judge() 集成——左侧下跌拦截买入"""
        portfolio = [
            {"name": "A股ETF", "code": "510500", "asset_class": "A股资产",
             "shares": 100, "latest_price": 1.0, "cost": 1.0,
             "currency": "CNY", "tags": [], "trend": "左侧下跌"},
            {"name": "债基", "code": "017093", "asset_class": "固收资产",
             "shares": 9900, "latest_price": 1.0, "cost": 1.0,
             "currency": "CNY", "tags": [], "trend": ""},
        ]
        result = judge(portfolio, client=None)
        a_signal = next(s for s in result["signals"] if s["asset_class"] == "A股资产")
        # 虽然偏离度触发买入，但趋势为左侧下跌 → HOLD
        assert a_signal["signal"] == "HOLD_AND_WAIT"
        assert a_signal["override"] is not None
        assert "左侧下跌" in a_signal["override"]
