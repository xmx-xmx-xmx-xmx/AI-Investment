"""
单元测试：src.price_updater 模块测试 (#16)
涵盖标的代码类型路由、基金趋势识别、多品种价格抓取、本地硬隔离保护与批量现价写回。
"""

from unittest.mock import MagicMock
import pandas as pd
import pytest

from src.price_updater import (
    _is_fund_code,
    _is_cn_etf_code,
    _is_us_code,
    _is_hk_code,
    detect_fund_trend,
    fetch_fund_price,
    fetch_etf_price,
    fetch_us_price,
    fetch_hk_price,
    fetch_price,
    update_all_prices,
)


# ═══════════════════════════════════════════════════════════════
# 1. 标的代码类型路由测试
# ═══════════════════════════════════════════════════════════════

def test_code_type_checks():
    # 场外基金（6位数字）
    assert _is_fund_code("019442") is True
    assert _is_fund_code("161128") is True
    assert _is_fund_code("00700") is False  # 5位不是
    assert _is_fund_code("QQQ") is False

    # 场内 ETF（51/56/58/159 开头）
    assert _is_cn_etf_code("512890") is True
    assert _is_cn_etf_code("159915") is True
    assert _is_cn_etf_code("560010") is True
    assert _is_cn_etf_code("588000") is True
    assert _is_cn_etf_code("019442") is False
    assert _is_cn_etf_code("161128") is False  # LOF 归为场外基金

    # 美股代码（纯字母）
    assert _is_us_code("SPY") is True
    assert _is_us_code("QQQ") is True
    assert _is_us_code("AAPL") is True
    assert _is_us_code("512890") is False
    assert _is_us_code("019442") is False

    # 港股代码（5位数字）
    assert _is_hk_code("00700") is True
    assert _is_hk_code("03486") is True
    assert _is_hk_code("512890") is False
    assert _is_hk_code("SPY") is False


# ═══════════════════════════════════════════════════════════════
# 2. 基金趋势识别测试
# ═══════════════════════════════════════════════════════════════

def test_detect_fund_trend():
    # 空数据或格式不符
    assert detect_fund_trend(None) == ""
    assert detect_fund_trend(pd.DataFrame()) == ""
    assert detect_fund_trend(pd.DataFrame({"净值": [1.0, 1.1]})) == ""

    # 数据不足 5 行
    short_df = pd.DataFrame({"单位净值": [1.0, 1.1, 1.2, 1.3]})
    assert detect_fund_trend(short_df) == ""

    # 最近 3 天连续上涨 -> 右侧企稳
    rebound_df = pd.DataFrame({"单位净值": [1.5, 1.4, 1.1, 1.2, 1.3]})
    assert detect_fund_trend(rebound_df) == "右侧企稳"

    # 5 天前比今天高且最后未连涨 3 天 -> 左侧下跌
    down_df = pd.DataFrame({"单位净值": [1.5, 1.4, 1.3, 1.35, 1.2]})
    assert detect_fund_trend(down_df) == "左侧下跌"

    # 横盘震荡（今天与5天前接近或上涨但最后3天未连涨）
    sideways_df = pd.DataFrame({"单位净值": [1.0, 1.2, 1.1, 1.3, 1.05]})
    assert detect_fund_trend(sideways_df) == "横盘震荡"


# ═══════════════════════════════════════════════════════════════
# 3. 价格抓取路由测试
# ═══════════════════════════════════════════════════════════════

def test_fetch_fund_price_mock(monkeypatch):
    mock_df = pd.DataFrame({
        "净值日期": ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"],
        "单位净值": [1.10, 1.12, 1.11, 1.13, 1.15],
        "日增长率": [0.5, 1.8, -0.9, 1.8, 1.77],
    })

    mock_ak = MagicMock()
    mock_ak.fund_open_fund_info_em.return_value = mock_df
    monkeypatch.setattr("src.net_guard.import_ak", lambda: mock_ak)

    res = fetch_fund_price("019442")
    assert res is not None
    assert res["price"] == 1.15
    assert res["date"] == "2026-11-06"
    assert res["change_pct"] == 1.77
    assert res["trend"] == "右侧企稳"
    assert res["source"] == "akshare_fund"


def test_fetch_price_routing(monkeypatch):
    # 场外基金
    monkeypatch.setattr("src.price_updater.fetch_fund_price", lambda code: {"price": 1.23, "source": "fund"})
    # 场内 ETF
    monkeypatch.setattr("src.price_updater.fetch_etf_price", lambda code: {"price": 2.34, "source": "etf"})
    # 港股
    monkeypatch.setattr("src.price_updater.fetch_hk_price", lambda code: {"price": 3.45, "source": "hk"})
    # 美股
    monkeypatch.setattr("src.price_updater.fetch_us_price", lambda code: {"price": 4.56, "source": "us"})

    assert fetch_price("") is None
    assert fetch_price("019442")["source"] == "fund"
    assert fetch_price("512890")["source"] == "etf"
    assert fetch_price("00700")["source"] == "hk"
    assert fetch_price("SPY")["source"] == "us"
    assert fetch_price("INVALID-CODE-1234") is None


# ═══════════════════════════════════════════════════════════════
# 4. update_all_prices 主流程测试
# ═══════════════════════════════════════════════════════════════

def test_update_all_prices_local_hard_isolation(monkeypatch):
    """验证 AGENTS.md 1.1 红线：本地开发且未传 client 时，必须抛出 RuntimeError 拦截。"""
    monkeypatch.setattr("src.env.is_production", lambda: False)
    with pytest.raises(RuntimeError, match="本地禁止执行"):
        update_all_prices(client=None)


def test_update_all_prices_pipeline(monkeypatch):
    # 禁用 sleep
    monkeypatch.setattr("time.sleep", lambda s: None)

    # 模拟底仓表记录
    records = [
        # 1. 成功更新
        {
            "_record_id": "rec_001",
            "标的名称": "红利低波ETF",
            "标的代码": "512890",
            "现价": 1.05,
        },
        # 2. 抓取失败但有旧现价 -> 自动复用缓存
        {
            "_record_id": "rec_002",
            "标的名称": "某海外基金",
            "标的代码": "000001",
            "现价": 2.50,
            "价格更新日期": "2026-11-05",
            "趋势": "横盘震荡",
        },
        # 3. 抓取失败且无旧现价 -> failed
        {
            "_record_id": "rec_003",
            "标的名称": "新录入基金",
            "标的代码": "000002",
            "现价": 0.0,
        },
        # 4. 无 _record_id -> skipped
        {
            "_record_id": "",
            "标的名称": "坏记录",
            "标的代码": "000003",
        },
    ]

    mock_client = MagicMock()
    mock_client.list_records.return_value = records
    mock_client.batch_update_records.return_value = 2

    def fake_fetch_price(code):
        if code == "512890":
            return {"price": 1.10, "date": "2026-11-07", "change_pct": 1.25, "trend": "右侧企稳", "source": "etf"}
        return None

    monkeypatch.setattr("src.price_updater.fetch_price", fake_fetch_price)

    # 测试 dry_run = True
    dry_res = update_all_prices(client=mock_client, dry_run=True)
    assert dry_res["updated"] == 2  # 1 个新更新 + 1 个缓存复用
    assert dry_res["failed"] == 1
    assert dry_res["skipped"] == 1
    mock_client.batch_update_records.assert_not_called()

    # 测试 dry_run = False 真实写回 mock client
    real_res = update_all_prices(client=mock_client, dry_run=False)
    assert real_res["updated"] == 2
    mock_client.batch_update_records.assert_called_once()
    args, kwargs = mock_client.batch_update_records.call_args
    assert args[0] == "底仓表"
    updates = args[1]
    assert len(updates) == 2
    # 验证第一条更新
    assert updates[0]["_record_id"] == "rec_001"
    assert updates[0]["现价"] == 1.10
    # 验证第二条缓存
    assert updates[1]["_record_id"] == "rec_002"
    assert updates[1]["现价"] == 2.50
    assert updates[1]["日涨跌幅%"] == 0.0
