"""
单元测试：src.advisor 模块测试 (#16)
涵盖字段解析、持仓加载、汇率折算、再平衡偏离度计算与 Prompt 组装。
"""

from unittest.mock import MagicMock
import pytest

from src.advisor import (
    _unwrap_select,
    _unwrap_select_list,
    load_portfolio,
    _get_rate,
    _collect_exchange_rates,
    calculate_rebalance,
    build_prompt,
    DEVIATION_THRESHOLD,
    PANIC_THRESHOLD,
)
from src.constants import TARGET_WEIGHTS


# ═══════════════════════════════════════════════════════════════
# 1. 字段归一化测试
# ═══════════════════════════════════════════════════════════════

def test_unwrap_select():
    assert _unwrap_select("美股资产") == "美股资产"
    assert _unwrap_select(["固收资产", "其他"]) == "固收资产"
    assert _unwrap_select([]) == ""
    assert _unwrap_select(None) == ""


def test_unwrap_select_list():
    assert _unwrap_select_list(["长期底仓", "观察仓"]) == ["长期底仓", "观察仓"]
    assert _unwrap_select_list("长期底仓") == ["长期底仓"]
    assert _unwrap_select_list([]) == []
    assert _unwrap_select_list(None) == []


# ═══════════════════════════════════════════════════════════════
# 2. 从飞书读取持仓测试
# ═══════════════════════════════════════════════════════════════

def test_load_portfolio_no_client(monkeypatch):
    """本地模式且无 client 时，应短路返回空列表，不调飞书。"""
    monkeypatch.setattr("src.feishu_client.get_feishu_client_or_none", lambda: None)
    result = load_portfolio(client=None)
    assert result == []


def test_load_portfolio_with_mock_client():
    mock_client = MagicMock()
    mock_client.list_records.return_value = [
        {
            "_record_id": "rec_001",
            "标的名称": "纳指100ETF",
            "标的代码": "019442",
            "资产大类": ["美股资产"],
            "持仓份额": 1000,
            "成本均价": 1.25,
            "现价": 1.35,
            "结算货币": "CNY",
            "标签": ["长期底仓"],
            "投资载体": "场外基金",
            "趋势": "🟢 趋势向上",
            "日涨跌幅%": 1.5,
        },
        {
            "_record_id": "rec_002",
            "标的名称": "腾讯控股",
            "标的代码": "00700",
            "资产大类": "港股资产",
            "持仓份额": 100,
            "成本均价": 350.0,
            "现价": None,  # 测试现价为空时默认0.0
            "结算货币": ["HKD"],
            "标签": None,
        },
    ]

    portfolio = load_portfolio(client=mock_client)
    assert len(portfolio) == 2

    p0 = portfolio[0]
    assert p0["name"] == "纳指100ETF"
    assert p0["asset_class"] == "美股资产"
    assert p0["shares"] == 1000.0
    assert p0["cost"] == 1.25
    assert p0["latest_price"] == 1.35
    assert p0["currency"] == "CNY"
    assert p0["tags"] == ["长期底仓"]
    assert p0["investment_vehicle"] == "场外基金"
    assert p0["trend"] == "🟢 趋势向上"
    assert p0["daily_change_pct"] == 1.5

    p1 = portfolio[1]
    assert p1["name"] == "腾讯控股"
    assert p1["asset_class"] == "港股资产"
    assert p1["shares"] == 100.0
    assert p1["latest_price"] == 0.0
    assert p1["currency"] == "HKD"
    assert p1["tags"] == []


# ═══════════════════════════════════════════════════════════════
# 3. 汇率折算与偏离度计算测试
# ═══════════════════════════════════════════════════════════════

def test_get_rate(monkeypatch):
    assert _get_rate("CNY") == 1.0
    assert _get_rate(None) == 1.0

    monkeypatch.setattr("src.market_data.fetch_exchange_rate", lambda cur: 0.85 if cur == "HKD" else None)
    assert _get_rate("HKD") == 0.85
    # 抓取失败降级为 1.0
    assert _get_rate("USD") == 1.0


def test_collect_exchange_rates(monkeypatch):
    monkeypatch.setattr("src.advisor._get_rate", lambda cur: 0.85 if cur == "HKD" else (7.2 if cur == "USD" else 1.0))
    portfolio = [
        {"currency": "CNY"},
        {"currency": "HKD"},
        {"currency": "USD"},
    ]
    rates = _collect_exchange_rates(portfolio)
    assert rates["HKD"] == 0.85
    assert rates["USD"] == 7.2
    assert rates["CNY"] == 1.0

    # 纯 CNY 组合返回空 dict
    assert _collect_exchange_rates([{"currency": "CNY"}]) == {}


def test_calculate_rebalance_empty():
    res = calculate_rebalance([])
    assert res["total_value"] == 0.0
    assert res["positions"] == []
    assert len(res["deviation_report"]) == len(TARGET_WEIGHTS)
    for d in res["deviation_report"]:
        assert d["actual_value"] == 0.0
        assert d["actual_weight"] == 0.0
        assert d["status"] == "低配"


def test_calculate_rebalance_multi_currency(monkeypatch):
    # 模拟汇率 HKD -> 0.85 CNY
    monkeypatch.setattr("src.advisor._get_rate", lambda cur: 0.85 if cur == "HKD" else 1.0)

    portfolio = [
        # 美股: 1000 份 * 1.5 = 1500 CNY, 成本 1000 * 1.0 = 1000 CNY -> PnL +500 (+50%)
        {
            "name": "纳指ETF",
            "code": "019442",
            "asset_class": "美股资产",
            "shares": 1000,
            "cost": 1.0,
            "latest_price": 1.5,
            "currency": "CNY",
            "tags": ["长期底仓"],
        },
        # 港股: 100 份 * 100 HKD * 0.85 = 8500 CNY, 成本 100 * 90 CNY = 9000 CNY -> PnL -500 (-5.56%)
        {
            "name": "港股半导体",
            "code": "03486",
            "asset_class": "港股资产",
            "shares": 100,
            "cost": 90.0,
            "latest_price": 100.0,
            "currency": "HKD",
            "tags": [],
        },
    ]

    res = calculate_rebalance(portfolio)
    # 总市值 = 1500 + 8500 = 10000 CNY
    assert res["total_value"] == 10000.0

    pos0 = res["positions"][0]
    assert pos0["market_value"] == 1500.0
    assert pos0["pnl"] == 500.0
    assert pos0["pnl_pct"] == 50.0

    pos1 = res["positions"][1]
    assert pos1["market_value"] == 8500.0
    assert pos1["market_value_original"] == 10000.0
    assert pos1["pnl"] == -500.0
    assert round(pos1["pnl_pct"], 2) == -5.56

    # 检查偏离度报告
    report_map = {d["asset_class"]: d for d in res["deviation_report"]}

    # 美股目标 30%, 实际 15%, 偏离 -15% -> 低配
    us = report_map["美股资产"]
    assert us["actual_value"] == 1500.0
    assert round(us["actual_weight"], 2) == 0.15
    assert round(us["deviation"], 2) == -0.15
    assert us["status"] == "低配"
    assert "加大买入" in us["action_hint"]

    # 港股目标 10%, 实际 85%, 偏离 +75% -> 超配
    hk = report_map["港股资产"]
    assert hk["actual_value"] == 8500.0
    assert round(hk["actual_weight"], 2) == 0.85
    assert round(hk["deviation"], 2) == 0.75
    assert hk["status"] == "超配"
    assert "适度止盈" in hk["action_hint"]

    # 固收目标 45%, 实际 0% -> 低配
    fi = report_map["固收资产"]
    assert fi["status"] == "低配"


# ═══════════════════════════════════════════════════════════════
# 4. Prompt 组装测试
# ═══════════════════════════════════════════════════════════════

def test_build_prompt_full():
    rebalance_data = {
        "total_value": 70000.0,
        "deviation_report": [
            {
                "asset_class": "美股资产",
                "status": "低配",
                "target_weight_pct": "30%",
                "actual_weight_pct": "15.00%",
                "deviation_pct": "-15.00%",
                "actual_value": 10500.0,
                "total_pnl": -500.0,
            }
        ],
        "positions": [
            {
                "name": "标普500ETF",
                "code": "018064",
                "asset_class": "美股资产",
                "market_value": 10500.0,
                "pnl": -1500.0,
                "pnl_pct": -12.5,  # 浮亏超过 -5%, 触发情绪安抚
                "shares": 10000,
                "cost": 1.20,
                "latest_price": 1.05,
                "currency": "CNY",
                "tags": ["长期底仓"],
            },
            {
                "name": "测试小仓位",
                "code": "999999",
                "asset_class": "美股资产",
                "market_value": 500.0,
                "pnl": 0.0,
                "pnl_pct": 0.0,
                "shares": 500,
                "cost": 1.0,
                "latest_price": 1.0,
                "currency": "CNY",
                "tags": ["观察仓"],
            },
        ],
    }

    vix_data = {"vix": 26.5, "level": "恐慌"}
    news = [{"title": "美联储降息预期升温", "snippet": "最新非农数据出炉"}]
    verdict = {
        "command": "【指令】增量资金优先补足美股资产",
        "priority_target": "美股资产",
        "priority_deviation": "-15.0%",
        "signals": [
            {
                "asset_class": "美股资产",
                "signal": "BUY",
                "signal_label": "🟢 加仓",
                "deviation_pct": "-15.0%",
                "intensity": "HIGH",
                "explanation": "偏离度大幅低配",
                "override": "规则5触发",
                "cooldown_status": "冷却中",
            }
        ],
        "psyche_facts": "美股长期均值回归概率高达90%",
    }

    prompt = build_prompt(rebalance_data, vix_data, news_articles=news, verdict=verdict)

    # 验证系统角色与规则
    assert "<system_role>" in prompt
    assert "<hard_rules>" in prompt

    # 验证行情与市值
    assert 'vix value="26.5"' in prompt
    assert "¥70,000.00" in prompt

    # 验证持仓与特殊标签
    assert "标普500ETF" in prompt
    assert "长期底仓" in prompt
    assert "观察仓" in prompt
    assert "严禁建议割肉" in prompt

    # 验证情绪安抚（浮亏 -12.5%）
    assert "【💚 情绪安抚要求】" in prompt
    assert "浮亏 -12.50%" in prompt

    # 验证资讯
    assert "【📰 今日要闻】" in prompt
    assert "美联储降息预期升温" in prompt

    # 验证 verdict 块
    assert "<python_verdict>" in prompt
    assert "【指令】增量资金优先补足美股资产" in prompt
    assert "美股长期均值回归概率高达90%" in prompt
