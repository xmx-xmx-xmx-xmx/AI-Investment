# -*- coding: utf-8 -*-
"""radar.py 单元测试 —— D6 雷达观测表"""

from __future__ import annotations

import pytest


# ═══════════════════════════════════════════════════════════════
# 趋势检测
# ═══════════════════════════════════════════════════════════════

class TestDetectTrend:
    """_detect_trend(prices_5d) 趋势方向判定"""

    def test_right_stabilized(self):
        """最近3天连续上涨 → 右侧企稳"""
        from src.radar import _detect_trend
        assert _detect_trend([10.0, 9.8, 9.5, 9.7, 10.0]) == "右侧企稳"

    def test_left_falling(self):
        """5天前高于今天 → 左侧下跌"""
        from src.radar import _detect_trend
        assert _detect_trend([10.0, 9.8, 9.6, 9.5, 9.3]) == "左侧下跌"
        # 即使最后2天微涨，整体仍左侧
        assert _detect_trend([10.0, 9.5, 9.3, 9.4, 9.35]) == "左侧下跌"

    def test_sideways(self):
        """无明显方向 → 横盘震荡"""
        from src.radar import _detect_trend
        assert _detect_trend([10.0, 10.05, 9.95, 10.02, 10.0]) == "横盘震荡"

    def test_too_few_points(self):
        """不足5天 → 空字符串"""
        from src.radar import _detect_trend
        assert _detect_trend([10.0, 10.5, 10.3, 10.7]) == ""
        assert _detect_trend([]) == ""


# ═══════════════════════════════════════════════════════════════
# 抄底信号
# ═══════════════════════════════════════════════════════════════

class TestCalcBuySignal:
    """_calc_buy_signal(change_10d, change_20d, trend) 抄底信号"""

    def test_attention_10d_minus5_trend_right(self):
        """10日跌超5%且右侧企稳 → 🟡 关注"""
        from src.radar import _calc_buy_signal
        assert _calc_buy_signal(-6.0, -2.0, "右侧企稳") == "🟡 关注"

    def test_reversal_20d_minus8_trend_right(self):
        """20日跌超8%且右侧企稳 → 🔵 底部反转"""
        from src.radar import _calc_buy_signal
        assert _calc_buy_signal(-3.0, -9.0, "右侧企稳") == "🔵 底部反转"

    def test_both_hit_stronger_wins(self):
        """两档同时命中 → 🔵 底部反转优先"""
        from src.radar import _calc_buy_signal
        assert _calc_buy_signal(-7.0, -10.0, "右侧企稳") == "🔵 底部反转"

    def test_trend_not_right_no_signal(self):
        """趋势不满足 → 空白，即使跌幅够"""
        from src.radar import _calc_buy_signal
        assert _calc_buy_signal(-6.0, -9.0, "左侧下跌") == ""
        assert _calc_buy_signal(-6.0, -9.0, "横盘震荡") == ""

    def test_no_signal_insufficient_drop(self):
        """跌幅不够 → 空白"""
        from src.radar import _calc_buy_signal
        assert _calc_buy_signal(-3.0, -5.0, "右侧企稳") == ""

    def test_no_signal_positive(self):
        """上涨中 → 空白"""
        from src.radar import _calc_buy_signal
        assert _calc_buy_signal(+2.0, +5.0, "右侧企稳") == ""

    def test_none_values(self):
        """None 输入（数据不足）→ 空白"""
        from src.radar import _calc_buy_signal
        assert _calc_buy_signal(None, -9.0, "右侧企稳") == ""
        assert _calc_buy_signal(-6.0, None, "右侧企稳") == ""
        assert _calc_buy_signal(-6.0, -9.0, "") == ""


# ═══════════════════════════════════════════════════════════════
# 追涨信号
# ═══════════════════════════════════════════════════════════════

class TestCalcChaseSignal:
    """_calc_chase_signal(daily_changes_5d, close, ma20) 追涨信号"""

    def test_chase_all_positive_within_ma20(self):
        """5日全阳 + 现价在20日线103%内 → 🟢 趋势加速"""
        from src.radar import _calc_chase_signal
        assert _calc_chase_signal(
            [0.5, 1.0, 0.8, 1.2, 0.3], close=102.0, ma20=100.0
        ) == "🟢 趋势加速"

    def test_chase_not_all_positive(self):
        """有阴线 → 空白"""
        from src.radar import _calc_chase_signal
        assert _calc_chase_signal(
            [0.5, -0.2, 0.8, 1.2, 0.3], close=102.0, ma20=100.0
        ) == ""

    def test_chase_too_far_above_ma20(self):
        """现价远超20日线（>103%）→ 空白，已飞"""
        from src.radar import _calc_chase_signal
        assert _calc_chase_signal(
            [0.5, 1.0, 0.8, 1.2, 0.3], close=108.0, ma20=100.0
        ) == ""

    def test_chase_exactly_at_103_boundary(self):
        """恰好 103% → 仍然算有效（刚突破）"""
        from src.radar import _calc_chase_signal
        assert _calc_chase_signal(
            [0.5, 0.5, 0.5, 0.5, 0.5], close=103.0, ma20=100.0
        ) == "🟢 趋势加速"

    def test_chase_too_few_days(self):
        """不足5日数据 → 空白"""
        from src.radar import _calc_chase_signal
        assert _calc_chase_signal([0.5, 1.0], close=102.0, ma20=100.0) == ""

    def test_chase_no_ma20(self):
        """无20日线 → 空白"""
        from src.radar import _calc_chase_signal
        assert _calc_chase_signal([0.5, 1.0, 0.8, 1.2, 0.3], close=102.0, ma20=None) == ""
