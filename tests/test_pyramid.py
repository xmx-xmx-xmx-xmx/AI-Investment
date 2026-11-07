# -*- coding: utf-8 -*-
"""单元测试：金字塔结构动作卡模块 (#31 不动日卡片金字塔重构 / 体验降噪与闲暇建议)"""

from __future__ import annotations

import pytest

from src.briefing_modules.pyramid import (
    _extract_vix_val,
    build_action_thresholds,
    build_idle_optimization_tips,
    build_pyramid_action_card,
    build_reassurance_sentence,
)


# ═══════════════════════════════════════════════════════════════
# 1. VIX 提取测试
# ═══════════════════════════════════════════════════════════════

def test_extract_vix_val():
    assert _extract_vix_val(16.5) == 16.5
    assert _extract_vix_val(20) == 20.0
    assert _extract_vix_val({"vix": 18.2, "level": "正常"}) == 18.2
    assert _extract_vix_val("😨 **VIX 恐慌指数**: 25.6（偏高）") == 25.6
    assert _extract_vix_val("VIX: 15.0") == 15.0
    assert _extract_vix_val(None) is None
    assert _extract_vix_val("") is None


# ═══════════════════════════════════════════════════════════════
# 2. 一句话定心丸测试
# ═══════════════════════════════════════════════════════════════

def test_reassurance_calm_day():
    verdict = {
        "signals": [
            {"asset_class": "固收资产", "actual_weight": "55.0%", "deviation_pct": "+0.5%"},
            {"asset_class": "美股资产", "actual_weight": "18.5%", "deviation_pct": "+1.8%"},
        ]
    }
    sent = build_reassurance_sentence(verdict=verdict, vix_val=16.5, has_action=False)
    assert "🛡️ **安心定心丸**" in sent
    assert "组合底盘很稳" in sent
    assert "55% 稳稳收息" in sent
    assert "微偏 +1.8%" in sent
    assert "VIX=16.5 市场很平静" in sent
    assert "完全不用动" in sent


def test_reassurance_action_day():
    verdict = {
        "signals": [
            {"asset_class": "美股资产", "actual_weight": "35.5%", "deviation_pct": "+5.5%"},
        ]
    }
    sent = build_reassurance_sentence(verdict=verdict, vix_val=26.0, has_action=True)
    assert "🛡️ **纪律指引**" in sent
    assert "按纪律执行调仓" in sent
    assert "美股 偏离 +5.5%" in sent
    assert "锁死利润" in sent


# ═══════════════════════════════════════════════════════════════
# 3. 核心行动警戒线测试
# ═══════════════════════════════════════════════════════════════

def test_action_thresholds_distance_calculation():
    verdict = {
        "signals": [
            {"asset_class": "美股资产", "actual_weight": "31.8%", "deviation_pct": "+1.8%"},
            {"asset_class": "固收资产", "actual_weight": "54.0%", "deviation_pct": "-0.5%"},
        ]
    }
    pf = [
        {"name": "富国上海金ETF联接C", "cost": 1.2, "latest_price": 1.344, "profit_rate": 0.12},  # +12%
    ]
    lines = build_action_thresholds(verdict=verdict, portfolio=pf, vix_val=16.5)
    assert len(lines) == 3

    # 再平衡线
    assert any("再平衡线：美股 当前微偏 +1.8%（还得再涨 +1.2% 才会叫你止盈调仓）" in l for l in lines)
    # 低吸买点
    assert any("低吸买点" in l for l in lines)
    # 恐慌风控
    assert any("VIX=16.5（飙到 25.0 才会触发防守，还有 8.5 点安全垫）" in l for l in lines)


def test_action_thresholds_negative_dev():
    verdict = {
        "signals": [
            {"asset_class": "避险商品", "actual_weight": "8.5%", "deviation_pct": "-1.5%"},
        ]
    }
    lines = build_action_thresholds(verdict=verdict, vix_val=None)
    assert any("再平衡线：避险商品 当前微偏 -1.5%（还得再跌 -1.5% 才会叫你补仓）" in l for l in lines)


# ═══════════════════════════════════════════════════════════════
# 4. 闲暇轻度优化建议测试
# ═══════════════════════════════════════════════════════════════

def test_idle_optimization_tips_detects_observation_fund():
    pf = [
        {
            "name": "易方达亚洲半导体ETF",
            "shares": 1000,
            "cost": 2.0,
            "latest_price": 1.9,
            "tags": ["观察仓"],
        },
        {
            "name": "建信短债债券C",
            "shares": 9000,
            "cost": 1.0,
            "latest_price": 1.05,
            "tags": ["长期底仓"],
        },
    ]
    verdict = {
        "priority_target": "固收资产",
        "priority_deviation": -0.008,
    }
    tips = build_idle_optimization_tips(portfolio=pf, verdict=verdict)
    assert len(tips) == 2
    assert "观察仓杂草清理" in tips[0]
    assert "易方达亚洲半导体" in tips[0]
    assert "零钱增量打卡" in tips[1]
    assert "固收" in tips[1]
    assert "-0.8%" in tips[1]


def test_idle_optimization_tips_fallback():
    tips = build_idle_optimization_tips(portfolio=[], verdict={})
    assert len(tips) >= 1
    assert "守住核心不追高" in tips[0]


# ═══════════════════════════════════════════════════════════════
# 5. 金字塔卡片组装测试
# ═══════════════════════════════════════════════════════════════

def test_build_pyramid_action_card_calm():
    verdict = {
        "overall_verdict": "HOLD",
        "priority_target": "固收资产",
        "priority_deviation": -0.01,
        "signals": [
            {"asset_class": "固收资产", "actual_weight": "54.0%", "deviation_pct": "-1.0%"},
            {"asset_class": "美股资产", "actual_weight": "19.0%", "deviation_pct": "+1.5%"},
        ],
    }
    pf = [
        {"name": "易方达亚洲半导体ETF", "shares": 500, "latest_price": 2.0, "tags": ["观察仓"]},
    ]
    card = build_pyramid_action_card(
        slot="closing",
        verdict=verdict,
        portfolio=pf,
        closing_signal="",
        vix_val=17.2,
    )
    assert "🎯 **核心指令**：🟢 维持观望 · 全天无需操作" in card
    assert "工作" not in card  # 严禁出现"工作"字眼
    assert "🛡️ **安心定心丸**" in card
    assert "📏 **行动警戒线**" in card
    assert "💡 **闲暇轻度建议**" in card
    assert "观察仓杂草清理" in card


def test_build_pyramid_action_card_action_suppresses_idle():
    verdict = {
        "overall_verdict": "ACT",
        "signals": [
            {"asset_class": "美股资产", "actual_weight": "35.0%", "deviation_pct": "+5.0%"},
        ],
    }
    card = build_pyramid_action_card(
        slot="closing",
        verdict=verdict,
        portfolio=[],
        closing_signal="美股再平衡",
        vix_val=22.0,
    )
    assert "🎯 **核心指令**：🟠 出现操作信号 👉 **美股再平衡**" in card
    assert "🛡️ **纪律指引**" in card
    assert "💡 **闲暇轻度建议**" not in card  # 强信号日自动抑制闲暇建议


def test_sat_morning_vix_plain_number_and_idle_advice():
    """回归测试 2026-11-07 周末复盘真实问题：
    1. sat_morning 传入 '25.6（恐慌）'（不含 VIX 字样）必须能正确解析出 25.6，绝不能误报'离 25.0 恐慌警戒线还很远'；
    2. 周六休市即使 verdict.overall_verdict == 'ACT'，也不得触发盘中调仓指令或丢失闲暇建议。
    """
    verdict = {
        "overall_verdict": "ACT",
        "signals": [
            {"asset_class": "固收资产", "actual_weight": "63.5%", "deviation_pct": "+23.5%"},
        ],
    }
    pf = [
        {"name": "易方达亚洲半导体ETF", "shares": 500, "latest_price": 2.0, "tags": ["观察仓"]},
    ]
    card = build_pyramid_action_card(
        slot="sat_morning",
        verdict=verdict,
        portfolio=pf,
        closing_signal="",
        vix_val="25.6（恐慌）",
    )
    assert "离 25.0 恐慌警戒线还很远" not in card
    assert "当前 VIX=25.6（已进入警戒区间" in card
    assert "💡 **闲暇轻度建议**" in card
