"""
单元测试：#30 决策日志持久化与解析逻辑
"""

import pytest
from src.briefing_modules.decision_log import (
    _extract_conclusion,
    _extract_action,
    _extract_signals,
    _extract_total_value,
    _extract_deviations,
    _extract_market_vix,
    record_briefing_decision,
)
from src.feishu_client import write_decision_log, read_decision_logs


def test_extract_conclusion():
    assert _extract_conclusion("closing", "", "green", None) == "建议买入加仓"
    assert _extract_conclusion("closing", "", "red", None) == "风控拦截/防御"
    assert _extract_conclusion("closing", "", "yellow", None) == "维持定投不动"
    assert _extract_conclusion("morning", "", "", None) == "早盘隔夜研判"
    assert _extract_conclusion("evening", "", "", None) == "夜盘美股前瞻"
    assert _extract_conclusion("sat_morning", "", "", None) == "全周复盘与同类对比"
    assert _extract_conclusion("sun_evening", "", "", None) == "周日下周仓位前瞻"


def test_extract_action():
    verdict = {"suggestion": "优先定投美股资产"}
    assert _extract_action("", verdict) == "优先定投美股资产"

    card = "【增量资金优先方向】建议将本期新增资金优先投向美股资产（100-200 元）"
    assert "优先投向美股资产" in _extract_action(card, None)

    assert "维持既定节奏" in _extract_action("平淡无奇的一天", None)


def test_extract_signals():
    rules_alert = "🎯 **决策参考**\n· 主题「新能源车」持了多只，收益差 30.9pp\n· 回本提醒：天弘短债"
    sig = _extract_signals("", rules_alert, None)
    assert "新能源车" in sig
    assert "回本提醒" in sig

    verdict = {"hard_signals": ["左侧下跌拦截: SOXX"]}
    assert "左侧下跌拦截" in _extract_signals("", "", verdict)

    assert "无特殊触发" in _extract_signals("", "", None)


def test_extract_total_value_and_deviations():
    verdict = {
        "total_value": 70019.49,
        "signals": [
            {"asset_class": "美股资产", "deviation_pct": "-13.0%"},
            {"asset_class": "固收资产", "deviation_pct": "+22.7%"},
        ],
    }
    assert _extract_total_value("", verdict, None) == 70019.49
    devs = _extract_deviations("", verdict, None)
    assert "美股资产-13.0%" in devs
    assert "固收资产+22.7%" in devs

    # 从卡片文本提取
    card = """
**仓位健康报告**
· 美股资产：目标30%　实际17.02%　偏离-13.0%　🔻 低配
· 固收资产：目标45%　实际67.73%　偏离+22.7%　⚠️ 超配
总市值 ¥70,019.49
"""
    assert _extract_total_value(card, None, None) == 70019.49
    card_devs = _extract_deviations(card, None, None)
    assert "美股-13.0%" in card_devs
    assert "固收+22.7%" in card_devs


def test_extract_market_vix():
    card = """
😨 **VIX 恐慌指数**: 26.8（恐慌）
· 标普500: 7,353.73　🔻-4.36%
· 纳斯达克100: 647.05　🔻-6.49%
"""
    mv = _extract_market_vix(card)
    assert "VIX" in mv
    assert "26.8" in mv
    assert "标普500" in mv


def test_record_briefing_decision(tmp_path, monkeypatch):
    test_fixture = tmp_path / "decision_log_test.json"
    monkeypatch.setattr("src.feishu_client._DECISION_LOG_FIXTURE_PATH", test_fixture)

    card = """
⚡ **2026-11-06 收盘前指令**
【增量资金优先方向】建议将本期新增资金优先投向美股资产
总市值 ¥70,019.49
"""
    entry = record_briefing_decision(
        slot="closing",
        card=card,
        dyn_title="⚡ 2026-11-06 收盘前指令",
        dyn_template="green",
        rules_alert="· 规则1 主题差距过大",
        dry_run=True,
    )

    assert entry["时段"] == "closing"
    assert entry["决策结论"] == "建议买入加仓"
    assert "美股资产" in entry["建议动作"]
    assert "规则1" in entry["触发规则与信号"]
    assert entry["总市值"] == 70019.49
    assert entry["执行状态"] == "待对账"

    # 验证读取
    logs = read_decision_logs(limit=10)
    assert len(logs) >= 1
    assert logs[-1]["决策结论"] == "建议买入加仓"
