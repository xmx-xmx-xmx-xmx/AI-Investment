# -*- coding: utf-8 -*-
"""market_data.py 单元测试 —— D1 全球行情直连"""

from __future__ import annotations

import pytest
import pandas as pd

# 将要测试的模块（Task 2 实现后 import 成功）
# from src import market_data


class TestFetchUsIndex:
    """fetch_us_index() 美股三大指数抓取"""

    def test_all_sources_fail_returns_none(self, monkeypatch):
        """yfinance 和 akshare 均失败时返回 None"""
        # 模拟 yfinance Ticker.history 抛异常
        def mock_yf_history_fail(self, period="5d"):
            raise RuntimeError("yfinance network error")

        # 模拟 akshare index_us_stock_sina 抛异常
        def mock_ak_sina_fail(symbol):
            raise RuntimeError("akshare network error")

        # 注入 mock
        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_yf_history_fail)

        import akshare as ak
        monkeypatch.setattr(ak, "index_us_stock_sina", mock_ak_sina_fail)

        from src import market_data
        result = market_data.fetch_us_index("^DJI")
        assert result is None
