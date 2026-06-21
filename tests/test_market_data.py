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

    def test_yfinance_success_returns_data(self, monkeypatch):
        """yfinance 正常返回时直接使用"""
        import pandas as pd

        def mock_history(self, period="5d"):
            return pd.DataFrame({
                "Close": [100.0, 101.0, 102.0, 103.0, 104.0],
            })

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_history)

        from src import market_data
        result = market_data.fetch_us_index("^DJI")
        assert result is not None
        assert result["ticker"] == "^DJI"
        assert result["name"] == "道琼斯工业指数"
        assert result["market"] == "美股"
        assert result["close"] == 104.0
        assert result["change_pct"] == pytest.approx(0.9708, rel=1e-2)  # (104-103)/103*100
        assert result["source"] == "yfinance"

    def test_yfinance_fails_falls_back_to_akshare(self, monkeypatch):
        """yfinance 失败时回退到 akshare sina 源"""
        import pandas as pd

        def mock_yf_history_fail(self, period="5d"):
            raise RuntimeError("fail")

        def mock_ak_sina(symbol):
            return pd.DataFrame({
                "close": [200.0, 201.0, 202.0, 203.0, 204.0],
            })

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_yf_history_fail)

        import akshare as ak
        monkeypatch.setattr(ak, "index_us_stock_sina", mock_ak_sina)

        from src import market_data
        result = market_data.fetch_us_index("^DJI")
        assert result is not None
        assert result["close"] == 204.0
        assert result["source"] == "akshare_sina"

    def test_insufficient_data_returns_none(self, monkeypatch):
        """不够 2 行数据时返回 None"""
        import pandas as pd

        def mock_history_one_row(self, period="5d"):
            return pd.DataFrame({"Close": [100.0]})

        def mock_ak_sina_fail(symbol):
            raise RuntimeError("akshare no data")

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_history_one_row)

        import akshare as ak
        monkeypatch.setattr(ak, "index_us_stock_sina", mock_ak_sina_fail)

        from src import market_data
        result = market_data.fetch_us_index("^DJI")
        assert result is None


class TestFetchUsIndices:
    """fetch_us_indices() 批量抓取"""

    def test_batch_success_all(self, monkeypatch):
        """全部成功返回完整列表"""
        import pandas as pd

        def mock_history(self, period="5d"):
            return pd.DataFrame({"Close": [100.0, 101.0]})

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_history)
        # 让 Ticker 构造器记录 ticker
        original_init = yf.Ticker.__init__

        def mock_init(self, ticker):
            self.ticker = ticker
            original_init(self, ticker)

        monkeypatch.setattr(yf.Ticker, "__init__", mock_init)

        from src import market_data
        results = market_data.fetch_us_indices(["^DJI", "^GSPC", "^IXIC"])
        assert len(results) == 3
        assert all(r["close"] == 101.0 for r in results)

    def test_batch_partial_failure(self, monkeypatch):
        """部分失败不影响其他"""
        import pandas as pd

        def mock_history(self, period="5d"):
            if self.ticker == "^GSPC":
                raise RuntimeError("fail")
            return pd.DataFrame({"Close": [100.0, 101.0]})

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_history)
        original_init = yf.Ticker.__init__

        def mock_init(self, ticker):
            self.ticker = ticker
            original_init(self, ticker)

        monkeypatch.setattr(yf.Ticker, "__init__", mock_init)

        # Mock akshare to also fail for ^GSPC (fallback from yfinance)
        import akshare as ak
        monkeypatch.setattr(ak, "index_us_stock_sina", lambda symbol: (_ for _ in ()).throw(RuntimeError("fail")))

        from src import market_data
        results = market_data.fetch_us_indices(["^DJI", "^GSPC", "^IXIC"])
        assert len(results) == 2  # ^GSPC 失败被跳过

    def test_all_fail_returns_empty(self, monkeypatch):
        """全部失败返回空列表"""
        def mock_history_all_fail(self, period="5d"):
            raise RuntimeError("fail")

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_history_all_fail)

        # 也 mock akshare 失败
        import akshare as ak
        monkeypatch.setattr(ak, "index_us_stock_sina", lambda symbol: (_ for _ in ()).throw(RuntimeError("fail")))

        from src import market_data
        results = market_data.fetch_us_indices(["^DJI", "^GSPC"])
        assert results == []


class TestFetchUsTreasury:
    """fetch_us_treasury() 美债收益率"""

    def test_all_sources_fail_returns_none(self, monkeypatch):
        """akshare 异常时返回 None"""
        import akshare as ak
        monkeypatch.setattr(ak, "bond_zh_us_rate", lambda: (_ for _ in ()).throw(RuntimeError("fail")))

        from src import market_data
        result = market_data.fetch_us_treasury()
        assert result is None

    def test_success_returns_data(self, monkeypatch):
        """正常返回完整数据结构"""
        import pandas as pd

        def mock_bond_rate():
            return pd.DataFrame([{
                "日期": "2026-06-18",
                "美国国债收益率2年": 4.19,
                "美国国债收益率10年": 4.46,
                "美国国债收益率10年-2年": 0.27,
            }])

        import akshare as ak
        monkeypatch.setattr(ak, "bond_zh_us_rate", mock_bond_rate)

        from src import market_data
        result = market_data.fetch_us_treasury()
        assert result is not None
        assert result["date"] == "2026-06-18"
        assert result["us_2y"] == 4.19
        assert result["us_10y"] == 4.46
        assert result["us_10y2y_spread"] == 0.27
        assert result["source"] == "akshare_bond"

    def test_empty_dataframe_returns_none(self, monkeypatch):
        """空 DataFrame 返回 None"""
        import pandas as pd

        import akshare as ak
        monkeypatch.setattr(ak, "bond_zh_us_rate", lambda: pd.DataFrame())

        from src import market_data
        result = market_data.fetch_us_treasury()
        assert result is None
