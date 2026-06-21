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


# ═══════════════════════════════════════════════════════════════
# 资产大类推断
# ═══════════════════════════════════════════════════════════════

class TestGetAssetClass:
    """_get_asset_class(code) 代码→资产大类"""

    def test_cn_etf(self):
        from src.radar import _get_asset_class
        assert _get_asset_class("515080") == "A股资产"
        assert _get_asset_class("159941") == "A股资产"

    def test_cn_fund(self):
        from src.radar import _get_asset_class
        assert _get_asset_class("017093") == "基金"

    def test_us(self):
        from src.radar import _get_asset_class
        assert _get_asset_class("QQQ") == "美股资产"
        assert _get_asset_class("MU") == "美股资产"

    def test_hk(self):
        from src.radar import _get_asset_class
        assert _get_asset_class("00700") == "港股资产"
        assert _get_asset_class("09988") == "港股资产"

    def test_unknown(self):
        from src.radar import _get_asset_class
        assert _get_asset_class("") == "未知"
        assert _get_asset_class("??") == "未知"


# ═══════════════════════════════════════════════════════════════
# 历史价格抓取
# ═══════════════════════════════════════════════════════════════

class TestFetchHistoricalPrices:
    """_fetch_historical_prices(code, days) 历史行情"""

    def test_cn_etf_yfinance_success(self, monkeypatch):
        """yfinance 正常返回 → 提取 close + change_pct"""
        import pandas as pd

        def mock_history(self, period="1mo"):
            data = {f"day{i}": float(100 + i) for i in range(25)}
            return pd.DataFrame({
                "Close": list(data.values()),
            })

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_history)

        from src.radar import _fetch_historical_prices
        result = _fetch_historical_prices("515080", days=25)
        assert result is not None
        assert len(result["prices"]) == 25
        assert result["prices"][0] == 100.0
        assert result["prices"][-1] == 124.0
        assert result["source"] in ("yfinance", "akshare_em", "akshare_sina")

    def test_us_ticker_yfinance_fails_fallback(self, monkeypatch):
        """yfinance 失败 → akshare 兜底"""
        import pandas as pd

        def mock_yf_fail(self, period="1mo"):
            raise RuntimeError("fail")

        def mock_ak_us(symbol, period="daily", adjust=""):
            return pd.DataFrame({
                "收盘": [200.0 + i for i in range(25)],
            })

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_yf_fail)

        import akshare as ak
        monkeypatch.setattr(ak, "stock_us_hist", mock_ak_us)

        from src.radar import _fetch_historical_prices
        result = _fetch_historical_prices("QQQ", days=25)
        assert result is not None
        assert len(result["prices"]) == 25

    def test_all_sources_fail_returns_none(self, monkeypatch):
        """全部失败 → None"""
        import yfinance as yf
        import akshare as ak
        monkeypatch.setattr(yf.Ticker, "history",
                            lambda self, period="1mo": (_ for _ in ()).throw(RuntimeError("fail")))
        monkeypatch.setattr(ak, "fund_etf_hist_em",
                            lambda symbol, period, adjust: (_ for _ in ()).throw(RuntimeError("fail")))
        monkeypatch.setattr(ak, "fund_etf_hist_sina",
                            lambda symbol: (_ for _ in ()).throw(RuntimeError("fail")))

        from src.radar import _fetch_historical_prices
        result = _fetch_historical_prices("515080", days=25)
        assert result is None

    def test_insufficient_data(self, monkeypatch):
        """返回数据不足要求天数 → None"""
        import pandas as pd

        def mock_short(self, period="1mo"):
            return pd.DataFrame({"Close": [100.0, 101.0]})  # 仅2行

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_short)

        from src.radar import _fetch_historical_prices
        result = _fetch_historical_prices("QQQ", days=25)
        assert result is None  # 不够 25 天，用已有数据算不了


# ═══════════════════════════════════════════════════════════════
# scan_radar 集成测试
# ═══════════════════════════════════════════════════════════════

class TestScanRadar:
    """scan_radar() 核心扫描循环"""

    def test_empty_table(self, monkeypatch):
        """雷达表为空 → 返回空结果"""
        class FakeClient:
            def list_records(self, table_name):
                return []

        monkeypatch.setattr("src.radar.FeishuClient", lambda: FakeClient())

        from src.radar import scan_radar
        result = scan_radar(dry_run=True)
        assert result["scanned"] == 0
        assert result["has_signal"] == 0
        assert result["details"] == []

    def test_single_record_with_signal(self, monkeypatch):
        """有信号的标的 → 返回含信号详情"""
        class FakeClient:
            def list_records(self, table_name):
                return [{
                    "_record_id": "rec_001",
                    "标的代码": "515080",
                    "标的名称": "中证红利ETF",
                    "资产大类": "A股",
                    "关联底仓": "",
                    "现价": 0,
                    "10日涨跌幅%": 0,
                    "20日涨跌幅%": 0,
                    "趋势": "",
                    "抄底信号": "",
                    "追涨信号": "",
                    "入库日期": "",
                }]

        # mock 历史价格：20日跌 ~8.75%（触发 🔵 底部反转）、近5日连续微涨
        def mock_fetch(code, days=25):
            import time as _time
            _time.sleep(0.01)
            # 25 根价格线，保证 len ≥ 21 可算 change_20d
            prices = [10.0 + i * 0.04 for i in range(25)]  # ~10.0 → ~10.96
            prices[-21] = 12.0  # 20天前=12.0，当前≈10.95 → 跌约 8.75%
            prices[-1] = 10.95
            changes = [round((prices[i] - prices[i-1]) / prices[i-1] * 100, 2) if prices[i-1] != 0 else 0.0
                       for i in range(1, len(prices))]
            changes.insert(0, 0.0)
            # 让最后3天微涨
            prices[-3] = 10.88; prices[-2] = 10.92; prices[-1] = 10.95
            changes[-3] = 0.1; changes[-2] = 0.37; changes[-1] = 0.27
            return {"prices": prices, "changes": changes, "source": "test"}

        monkeypatch.setattr("src.radar._fetch_historical_prices", mock_fetch)
        monkeypatch.setattr("src.radar.FeishuClient", lambda: FakeClient())

        from src.radar import scan_radar
        result = scan_radar(dry_run=True)
        assert result["scanned"] == 1
        assert result["has_signal"] == 1
        assert len(result["details"]) == 1
        assert result["details"][0]["code"] == "515080"
        assert result["details"][0]["buy_signal"] != "" or result["details"][0]["chase_signal"] != ""
        # 写入回写字段
        assert len(result["updates"]) == 1
        assert result["updates"][0]["_record_id"] == "rec_001"
        assert "现价" in result["updates"][0]

    def test_record_without_signal(self, monkeypatch):
        """无信号的标的 → details 有记录但信号为空"""
        class FakeClient:
            def list_records(self, table_name):
                return [{
                    "_record_id": "rec_002",
                    "标的代码": "QQQ",
                    "标的名称": "纳斯达克100",
                    "资产大类": "美股",
                    "关联底仓": "",
                    "现价": 0,
                    "10日涨跌幅%": 0,
                    "20日涨跌幅%": 0,
                    "趋势": "",
                    "抄底信号": "",
                    "追涨信号": "",
                    "入库日期": "",
                }]

        def mock_fetch(code, days=25):
            import time as _time
            _time.sleep(0.01)
            prices = [500.0 + i * 2 for i in range(25)]  # 稳步上涨，无信号
            changes = [round((prices[i] - prices[i-1]) / prices[i-1] * 100, 2) if prices[i-1] != 0 else 0.0
                       for i in range(1, len(prices))]
            changes.insert(0, 0.0)
            return {"prices": prices, "changes": changes, "source": "test"}

        monkeypatch.setattr("src.radar._fetch_historical_prices", mock_fetch)
        monkeypatch.setattr("src.radar.FeishuClient", lambda: FakeClient())

        from src.radar import scan_radar
        result = scan_radar(dry_run=True)
        assert result["scanned"] == 1
        assert result["has_signal"] == 0

    def test_partial_fetch_failure(self, monkeypatch):
        """部分标的抓取失败 → 跳过并继续"""
        fetch_calls = []

        def mock_fetch(code, days=25):
            fetch_calls.append(code)
            if code == "BAD":
                return None
            import time as _time
            _time.sleep(0.01)
            prices = [100.0 + i for i in range(25)]
            changes = [0.5] * 25
            return {"prices": prices, "changes": changes, "source": "test"}

        class FakeClient:
            def list_records(self, table_name):
                return [
                    {"_record_id": "r1", "标的代码": "GOOD", "标的名称": "好标的",
                     "资产大类": "美股", "关联底仓": "", "现价": 0, "10日涨跌幅%": 0,
                     "20日涨跌幅%": 0, "趋势": "", "抄底信号": "", "追涨信号": "", "入库日期": ""},
                    {"_record_id": "r2", "标的代码": "BAD", "标的名称": "坏标的",
                     "资产大类": "美股", "关联底仓": "", "现价": 0, "10日涨跌幅%": 0,
                     "20日涨跌幅%": 0, "趋势": "", "抄底信号": "", "追涨信号": "", "入库日期": ""},
                ]

        monkeypatch.setattr("src.radar._fetch_historical_prices", mock_fetch)
        monkeypatch.setattr("src.radar.FeishuClient", lambda: FakeClient())

        from src.radar import scan_radar
        result = scan_radar(dry_run=True)
        assert result["scanned"] == 1  # 只有 GOOD 被扫了
        assert result["failed"] == 1   # BAD 失败
