"""
单元测试：src.earnings_calendar 模块测试 (#16)
涵盖美股标的扫描与兜底、未来财报日程提取、昨日财报对比及简报文本格式化。
"""

from datetime import date, timedelta
from unittest.mock import MagicMock
import pandas as pd
import pytest

from src.earnings_calendar import (
    _get_radar_us_tickers,
    _FALLBACK_TICKERS,
    fetch_weekly_earnings,
    fetch_yesterdays_earnings,
    format_weekly_earnings,
    format_yesterdays_earnings,
)


# ═══════════════════════════════════════════════════════════════
# 1. 美股标的读取与兜底测试
# ═══════════════════════════════════════════════════════════════

def test_get_radar_us_tickers_local(monkeypatch):
    monkeypatch.setattr("src.feishu_client.get_feishu_client_or_none", lambda: None)
    tickers = _get_radar_us_tickers()
    assert tickers == list(_FALLBACK_TICKERS)


def test_get_radar_us_tickers_with_client(monkeypatch):
    mock_client = MagicMock()
    mock_client.list_records.side_effect = [
        # 雷达观测表
        [
            {"标的代码": "NVDA"},
            {"标的代码": "019442"},  # 场外基金，非纯英文字母，应过滤
            {"标的代码": "tsm"},     # 小写，应过滤
        ],
        # 底仓表
        [
            {"标的代码": "AAPL", "资产大类": ["美股资产"]},
            {"标的代码": "00700", "资产大类": ["港股资产"]},
            {"标的代码": "512890", "资产大类": "A股资产"},
        ],
    ]
    monkeypatch.setattr("src.feishu_client.get_feishu_client_or_none", lambda: mock_client)

    tickers = _get_radar_us_tickers()
    assert "NVDA" in tickers
    assert "AAPL" in tickers
    assert "019442" not in tickers
    assert "00700" not in tickers


# ═══════════════════════════════════════════════════════════════
# 2. 财报日程获取测试 (Mock yfinance)
# ═══════════════════════════════════════════════════════════════

def test_fetch_weekly_earnings(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr("src.earnings_calendar._get_radar_us_tickers", lambda: ["NVDA", "AAPL"])

    today = date.today()
    future_date = today + timedelta(days=3)
    far_future = today + timedelta(days=20)

    # 模拟 yfinance Ticker
    df_nvda = pd.DataFrame(
        {"EPS Estimate": [0.85, 0.90]},
        index=[pd.Timestamp(future_date), pd.Timestamp(far_future)],
    )

    mock_ticker_nvda = MagicMock()
    mock_ticker_nvda.info = {"shortName": "NVIDIA Corporation"}
    mock_ticker_nvda.earnings_dates = df_nvda

    mock_ticker_aapl = MagicMock()
    mock_ticker_aapl.info = {"shortName": "Apple Inc."}
    mock_ticker_aapl.earnings_dates = None  # 无财报数据

    mock_yf = MagicMock()
    mock_yf.Ticker.side_effect = lambda t: mock_ticker_nvda if t == "NVDA" else mock_ticker_aapl
    monkeypatch.setattr("src.net_guard.import_yf", lambda: mock_yf)

    results = fetch_weekly_earnings(days_ahead=7)
    assert len(results) == 1
    res = results[0]
    assert res["ticker"] == "NVDA"
    assert res["name"] == "NVIDIA Corporation"
    assert res["earnings_date"] == future_date
    assert res["eps_estimate"] == 0.85


def test_fetch_yesterdays_earnings(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr("src.earnings_calendar._get_radar_us_tickers", lambda: ["MU"])

    yesterday = date.today() - timedelta(days=1)
    df_mu = pd.DataFrame(
        {
            "EPS Estimate": [1.20],
            "Reported EPS": [1.35],
            "Surprise(%)": [12.5],
        },
        index=[pd.Timestamp(yesterday)],
    )

    mock_ticker_mu = MagicMock()
    mock_ticker_mu.info = {"shortName": "Micron Technology"}
    mock_ticker_mu.earnings_dates = df_mu

    mock_yf = MagicMock()
    mock_yf.Ticker.return_value = mock_ticker_mu
    monkeypatch.setattr("src.net_guard.import_yf", lambda: mock_yf)

    results = fetch_yesterdays_earnings()
    assert len(results) == 1
    res = results[0]
    assert res["ticker"] == "MU"
    assert res["eps_estimate"] == 1.20
    assert res["eps_actual"] == 1.35
    assert res["surprise_pct"] == 12.5


# ═══════════════════════════════════════════════════════════════
# 3. 简报文本格式化测试
# ═══════════════════════════════════════════════════════════════

def test_format_weekly_earnings():
    assert format_weekly_earnings([]) == ""

    test_date = date(2026, 11, 10)  # 周二
    earnings = [
        {
            "ticker": "NVDA",
            "name": "NVIDIA",
            "earnings_date": test_date,
            "eps_estimate": 0.95,
        }
    ]
    txt = format_weekly_earnings(earnings)
    assert "📅 **近期财报提示**" in txt
    assert "NVDA NVIDIA 11/10(周二) | EPS预期 $0.95" in txt


def test_format_yesterdays_earnings():
    assert format_yesterdays_earnings([]) == ""

    earnings_beat = [
        {
            "ticker": "AAPL",
            "name": "Apple",
            "eps_actual": 1.50,
            "eps_estimate": 1.40,
            "surprise_pct": 7.1,
        }
    ]
    txt_beat = format_yesterdays_earnings(earnings_beat)
    assert "📅 **昨日财报**" in txt_beat
    assert "AAPL Apple EPS实际$1.5 vs 预期$1.4（超预期 +7.1%）" in txt_beat

    earnings_miss = [
        {
            "ticker": "INTC",
            "name": "Intel",
            "eps_actual": 0.20,
            "eps_estimate": 0.25,
            "surprise_pct": -20.0,
        }
    ]
    txt_miss = format_yesterdays_earnings(earnings_miss)
    assert "低于预期" in txt_miss
