"""规则 2（同类分位）测试：全 mock，零网络。"""

import pandas as pd
import pytest

from src.peer_rank import (
    WORST_PERCENTILE,
    build_peer_alert,
    classify_holding,
    evaluate_peer_rank,
    render_peer_alerts,
)


def rec(code, idx, **kw):
    r = {"_record_id": f"rec_{code}", "标的代码": code, "底层指数": idx,
         "标的名称": kw.pop("name", "某基金"), "最新收益率": 0.05}
    r.update(kw)
    return r


def frame(rows):
    """rows: [(code, name, 近1年字符串)] → akshare 排行形态的 DataFrame。"""
    return pd.DataFrame(rows, columns=["基金代码", "基金简称", "近1年"])


# ── 分类 ──

class TestClassify:
    def test_bond(self):
        assert classify_holding(rec("x", "无（主动债券）")) == "债券型"
        assert classify_holding(rec("x", "无（主动短债）")) == "债券型"
        assert classify_holding(rec("x", "无（主动中短债）")) == "债券型"

    def test_qdii_with_suffix(self):
        assert classify_holding(rec("x", "无（主动QDII·全球新能源车）")) == "QDII"

    def test_stock(self):
        assert classify_holding(rec("x", "无（主动股票）")) == "股票型"

    def test_index_fund_excluded(self):
        """指数基金（底层指数真实名）不参与——它们不需要同类对比。"""
        assert classify_holding(rec("x", "纳斯达克100")) is None
        assert classify_holding(rec("x", "中证红利低波动100")) is None

    def test_unknown_active_falls_to_mixed(self):
        assert classify_holding(rec("x", "无（主动平衡）")) == "混合型"


# ── 分位计算与触发 ──

def _big_frame(v_of_fund):
    """构造 100 只的类别：99 只均匀分布 + 目标基金。"""
    rows = [(f"{100000 + i}", f"同类{i}", f"{v_of_fund + (i - 50) * 0.5:.2f}")
            for i in range(100)]
    return frame(rows)


class TestEvaluate:
    def test_worst_30pct_triggers(self):
        """基金近1年垫底（低于 99% 同类）→ 触发。"""
        df = _big_frame(5.0)
        df.loc[len(df)] = ["006327", "落后基金", "-25.00"]  # 低于所有同类（序列最低 -20）
        alerts = evaluate_peer_rank([rec("006327", "无（主动QDII）")], {"QDII": df})
        assert len(alerts) == 1
        assert alerts[0]["rule"] == "R2"
        assert "只跑赢同类" in alerts[0]["text"]

    def test_median_performer_no_trigger(self):
        """跑赢 50% 同类 → 不触发。"""
        df = _big_frame(5.0)
        df.loc[len(df)] = ["006327", "中游基金", "5.00"]
        alerts = evaluate_peer_rank([rec("006327", "无（主动QDII）")], {"QDII": df})
        assert alerts == []

    def test_percentile_boundary_exactly_30(self):
        """分位恰在阈值 30%（≤）→ 触发。"""
        # 100 只同类中 30 只更低 → pctile=30.0
        vals = [f"{1.0 + i * 0.01:.2f}" for i in range(100)]  # 1.00..1.99
        df = frame([(f"{200000 + i}", f"同类{i}", v) for i, v in enumerate(vals)])
        df.loc[len(df)] = ["006327", "边界基金", "1.30"]  # 30 只比它低
        alerts = evaluate_peer_rank([rec("006327", "无（主动债券）")], {"债券型": df})
        assert len(alerts) == 1

    def test_index_fund_skipped_even_in_frames(self):
        df = _big_frame(5.0)
        df.loc[len(df)] = ["006327", "指数基金", "-25.00"]
        alerts = evaluate_peer_rank([rec("006327", "纳斯达克100")], {"QDII": df})
        assert alerts == []

    def test_missing_code_skipped(self):
        df = _big_frame(5.0)
        df.loc[len(df)] = ["006327", "基金", "-25.00"]
        r = rec("", "无（主动QDII）")
        alerts = evaluate_peer_rank([r], {"QDII": df})
        assert alerts == []

    def test_fund_not_in_frame_skipped(self):
        df = _big_frame(5.0)
        alerts = evaluate_peer_rank([rec("999999", "无（主动QDII）")], {"QDII": df})
        assert alerts == []

    def test_new_fund_no_1y_data_skipped(self):
        """成立不满 1 年：近1年列 '-' → 跳过（宁漏勿误）。"""
        df = _big_frame(5.0)
        df.loc[len(df)] = ["006327", "新基金", "-"]
        alerts = evaluate_peer_rank([rec("006327", "无（主动QDII）")], {"QDII": df})
        assert alerts == []

    def test_none_frame_skipped(self):
        alerts = evaluate_peer_rank(
            [rec("006327", "无（主动QDII）")], {"QDII": None})
        assert alerts == []

    def test_category_missing_from_frames(self):
        df = _big_frame(5.0)
        df.loc[len(df)] = ["006327", "基金", "-25.00"]
        alerts = evaluate_peer_rank([rec("006327", "无（主动股票）")], {"QDII": df})
        assert alerts == []


# ── 渲染 ──

class TestRender:
    def test_empty(self):
        assert render_peer_alerts([]) == ""

    def test_non_empty(self):
        out = render_peer_alerts([{"rule": "R2", "level": "提醒",
                                   "text": "「X」近1年 -3.0%，只跑赢同类 5%"}])
        assert out.startswith("📡 **同类对比（近1年）**")
        assert "「X」" in out


# ── 接入口（fail-silent）──

class TestBuildPeerAlert:
    def test_none_client(self):
        assert build_peer_alert(None) == ""

    def test_no_active_holdings_no_fetch(self):
        """全是指数基金 → 不出门抓数据，直接空串。"""

        class C:
            def list_records(self, _):
                return [rec("006327", "纳斯达克100")]

        assert build_peer_alert(C()) == ""

    def test_fetch_failure_silent(self, monkeypatch):
        class C:
            def list_records(self, _):
                return [rec("006327", "无（主动QDII）")]

        import src.peer_rank as m
        monkeypatch.setattr(m, "_import_ak_guarded", lambda: None)
        assert build_peer_alert(C()) == ""

    def test_full_flow(self, monkeypatch):
        df = _big_frame(5.0)
        df.loc[len(df)] = ["006327", "落后基金", "-25.00"]

        class C:
            def list_records(self, _):
                return [rec("006327", "无（主动QDII）")]

        class FakeAk:
            def fund_open_fund_rank_em(self, symbol):
                return df

        import src.peer_rank as m
        monkeypatch.setattr(m, "_import_ak_guarded", lambda: FakeAk())
        out = build_peer_alert(C())
        assert "同类对比" in out and "落后基金" in out

    def test_client_exception_silent(self):
        class C:
            def list_records(self, _):
                raise RuntimeError("boom")

        assert build_peer_alert(C()) == ""


def test_threshold_matches_spike_doc():
    """口径与 docs/RULE2_SPIKE.md 一致：近1年分位 ≤30%。"""
    assert WORST_PERCENTILE == 30.0
