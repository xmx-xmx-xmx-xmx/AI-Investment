"""#32 雷达超配闸门 + 飞书字段取值辅助（2026-09-24）。

背景（TODO §1.14 证据⑤）：审计发现 50 条推送里有 11 条把「🟢 趋势加速」
打在**已超配 15.5pp 的债券基金**上（招商产业债券A / 银华安颐中短债 /
兴业60天滚动短债C）。对超配大类发买入信号与「自然稀释」铁律直接矛盾，
而且是**反向误导**。本模块锁定闸门的判定与抑制行为。

纯函数测试，零网络（scan_radar 的抓取全部 mock）。
"""

from __future__ import annotations

import pytest

from src.radar import (
    OVERWEIGHT_BLOCK_PP,
    _calc_overweight_classes,
    _field_text,
    _holding_market_value,
)


# ═══════════════════════════════════════════════════════════════
# 取值辅助：飞书字段返回形态不统一
# ═══════════════════════════════════════════════════════════════

class TestFieldText:
    """⚠️ 底仓表的「资产大类」返回的是数组（`["美股资产"]`），只做 str()
    会得到 `"['美股资产']"` 这种脏值 → 静默匹配不上任何大类。"""

    def test_list_takes_first(self):
        assert _field_text(["固收资产"]) == "固收资产"

    def test_list_empty(self):
        assert _field_text([]) == ""

    def test_scalar_string(self):
        assert _field_text("017022") == "017022"

    def test_scalar_number(self):
        assert _field_text(2.77) == "2.77"

    def test_none(self):
        assert _field_text(None) == ""


class TestHoldingMarketValue:
    def test_formula_field_as_string(self):
        """公式字段「市值」实测被 API 返回成字符串 "284.18"。"""
        assert _holding_market_value({"市值": "284.18"}) == pytest.approx(284.18)

    def test_formula_field_as_number(self):
        assert _holding_market_value({"市值": 284.18}) == pytest.approx(284.18)

    def test_falls_back_to_shares_times_price(self):
        """市值取不到 → 回退「份额 × 现价」，好过整条闸门静默失效。"""
        h = {"市值": "", "持仓份额": 100, "现价": 2.5}
        assert _holding_market_value(h) == pytest.approx(250.0)

    def test_unparsable_market_value_falls_back(self):
        h = {"市值": "—", "持仓份额": 200, "现价": 1.5}
        assert _holding_market_value(h) == pytest.approx(300.0)

    def test_zero_when_nothing_usable(self):
        assert _holding_market_value({}) == 0.0


# ═══════════════════════════════════════════════════════════════
# 超配判定
# ═══════════════════════════════════════════════════════════════

class TestCalcOverweightClasses:
    """目标权重：固收50 / 美股20 / A股10 / 港股10 / 避险10。"""

    def test_known_mix(self):
        holdings = [
            {"资产大类": ["固收资产"], "市值": "8000"},
            {"资产大类": ["A股资产"], "市值": "1000"},
        ]
        ov = _calc_overweight_classes(holdings)
        # 固收 8000/9000=88.89% − 50% = +38.89
        assert ov["固收资产"] == pytest.approx(38.89, abs=0.01)
        # A股 1000/9000=11.11% − 10% = +1.11
        assert ov["A股资产"] == pytest.approx(1.11, abs=0.01)
        # 空仓大类 = 纯负偏离
        assert ov["美股资产"] == pytest.approx(-20.0, abs=0.01)
        assert ov["港股资产"] == pytest.approx(-10.0, abs=0.01)
        assert ov["避险商品"] == pytest.approx(-10.0, abs=0.01)

    def test_denominator_includes_unclassified(self):
        """⚠️ 分母含「待分类」：待分类占份额会让其他大类权重偏低 → 更保守。"""
        holdings = [
            {"资产大类": ["固收资产"], "市值": "5000"},
            {"资产大类": ["待分类"], "市值": "5000"},
        ]
        ov = _calc_overweight_classes(holdings)
        # 固收 5000/10000 = 50% → 恰好等于目标 → 0pp
        assert ov["固收资产"] == pytest.approx(0.0, abs=0.01)

    def test_empty_holdings_returns_empty(self):
        assert _calc_overweight_classes([]) == {}

    def test_all_zero_market_value_returns_empty(self):
        assert _calc_overweight_classes([{"资产大类": ["固收资产"], "市值": "0"}]) == {}

    def test_real_portfolio_shape_blocks_only_fixed_income(self):
        """贴近真实结构：固收 65.5% → +15.5pp，仅它越过 5pp 阈值。

        （2026-09-24 用真实 29 条底仓离线复算：固收 +15.48pp。）
        """
        holdings = [
            {"资产大类": ["固收资产"], "市值": "47352"},
            {"资产大类": ["美股资产"], "市值": "13321"},
            {"资产大类": ["港股资产"], "市值": "7089"},
            {"资产大类": ["避险商品"], "市值": "2712"},
            {"资产大类": ["A股资产"], "市值": "1846"},
        ]
        ov = _calc_overweight_classes(holdings)
        blocked = {c for c, d in ov.items() if d >= OVERWEIGHT_BLOCK_PP}
        assert blocked == {"固收资产"}
        assert ov["固收资产"] == pytest.approx(15.48, abs=0.05)


# ═══════════════════════════════════════════════════════════════
# scan_radar 集成：闸门真的把信号按下去了
# ═══════════════════════════════════════════════════════════════

def _chase_price_series():
    """构造只触发「🟢 趋势加速」的价格序列。

    - 近 5 日每日上涨（`all(c > 0)`）
    - 现价 10.05 ≤ ma20(10.0075) × 1.03 → 未溢价
    - 10 日 +0.5%，不触发抄底信号
    """
    return [10.0] * 20 + [10.01, 10.02, 10.03, 10.04, 10.05]


class _FakeClient:
    def __init__(self, data):
        self._data = data

    def list_records(self, table):
        return self._data.get(table, [])


@pytest.fixture
def mock_prices(monkeypatch):
    prices = _chase_price_series()
    changes = [
        round((prices[i] - prices[i - 1]) / prices[i - 1] * 100, 2)
        for i in range(1, len(prices))
    ]

    def fake_fetch(code, days=25):
        return {"prices": list(prices), "changes": list(changes), "source": "test"}

    monkeypatch.setattr("src.radar._fetch_historical_prices", fake_fetch)
    return prices


def _holding(rid, code, name, cls, mv):
    return {
        "_record_id": rid,
        "标的代码": code,
        "标的名称": name,
        # ⚠️ 刻意用数组形态：与真实 API 返回一致
        "资产大类": [cls],
        "市值": mv,
    }


class TestScanRadarOverweightGate:
    def test_overweight_class_signal_suppressed(self, monkeypatch, mock_prices):
        """固收 100% → +50pp → 该大类的「趋势加速」被抑制。"""
        client = _FakeClient({
            "雷达观测表": [],
            "底仓表": [_holding("rec_b", "217022", "招商产业债券A", "固收资产", "8000")],
        })
        monkeypatch.setattr("src.feishu_client.get_feishu_client_or_none", lambda: client)

        from src.radar import scan_radar
        result = scan_radar(dry_run=True)

        assert result["scanned"] == 1
        assert result["has_signal"] == 0, "超配大类的买入信号必须被按下"
        assert result["details"][0]["chase_signal"] == ""
        assert result["signal_items"] == []

    def test_non_overweight_class_signal_kept(self, monkeypatch, mock_prices):
        """A股 11.1% → +1.1pp（未越 5pp 阈值）→ 信号保留。

        同一份价格数据、同一个信号，只因所属大类不同而结果不同
        —— 这是闸门「按大类而非按信号」生效的直接证据。
        """
        client = _FakeClient({
            "雷达观测表": [],
            "底仓表": [
                _holding("rec_b", "217022", "招商产业债券A", "固收资产", "8000"),
                _holding("rec_a", "515080", "中证红利ETF", "A股资产", "1000"),
            ],
        })
        monkeypatch.setattr("src.feishu_client.get_feishu_client_or_none", lambda: client)

        from src.radar import scan_radar
        result = scan_radar(dry_run=True)

        assert result["scanned"] == 2
        assert result["has_signal"] == 1
        kept = result["signal_items"][0]
        assert kept["code"] == "515080"
        assert kept["chase_signal"] == "🟢 趋势加速"

    def test_stock_not_in_holdings_is_not_blocked(self, monkeypatch, mock_prices):
        """雷达观测表里的纯观察对象不在底仓 → 没有权重可算 → 一律放行。

        宁可漏拦，不可误杀。
        """
        client = _FakeClient({
            "雷达观测表": [{
                "_record_id": "rec_r",
                "标的代码": "03076",
                "标的名称": "富邦台湾半导体",
                "关联底仓": "",
                "入库日期": "",
            }],
            "底仓表": [_holding("rec_b", "217022", "招商产业债券A", "固收资产", "8000")],
        })
        monkeypatch.setattr("src.feishu_client.get_feishu_client_or_none", lambda: client)

        from src.radar import scan_radar
        result = scan_radar(dry_run=True)

        kept_codes = {d["code"] for d in result["signal_items"]}
        assert "03076" in kept_codes, "不在底仓的标的没有大类信息，不该被拦"
        assert "217022" not in kept_codes

    def test_holdings_read_failure_does_not_crash(self, monkeypatch, mock_prices):
        """底仓表读取失败 → 闸门静默不生效，扫描照常（不能因此整条挂掉）。"""
        class FlakyClient:
            def list_records(self, table):
                if table == "底仓表":
                    raise RuntimeError("模拟读取失败")
                return [{
                    "_record_id": "rec_r",
                    "标的代码": "03076",
                    "标的名称": "富邦台湾半导体",
                    "关联底仓": "",
                    "入库日期": "",
                }]

        monkeypatch.setattr("src.feishu_client.get_feishu_client_or_none", lambda: FlakyClient())

        from src.radar import scan_radar
        result = scan_radar(dry_run=True)

        assert result["scanned"] == 1
        assert result["has_signal"] == 1
