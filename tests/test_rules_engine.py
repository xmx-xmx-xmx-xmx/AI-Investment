"""规则引擎（src/rules_engine.py）测试：规则 1/3/5 的纯函数 + fail-silent 接入。"""

import logging

import pytest

from src.rules_engine import (
    BREAKEVEN_BAND,
    FIELD_FIRED,
    FIELD_MIN_RET,
    THEME_GAP,
    build_rules_alert,
    evaluate_rules,
    render_alerts,
)


def h(name, ret=None, tag="普通持有", idx="", **kw):
    """构造一条底仓表记录（扁平形态，与 list_records 返回一致）。"""
    rec = {
        "_record_id": f"rec_{name}",
        "标的名称": name,
        "标签": [tag] if tag else [],
        "底层指数": idx,
        "最新收益率": ret,
        "市值": kw.pop("市值", 1000.0),
    }
    rec.update(kw)
    return rec


def bg():
    """背景组合：10 只各占 10%，让单条 fixture 不触发规则 5 的干扰。

    带 min 初值：否则它们自身会被规则 3 回填 min，污染 updates 断言。
    """
    return [h(f"__背景{i}__", ret=0.01, 市值=1e8,
              **{FIELD_MIN_RET: 0.01, FIELD_FIRED: False}) for i in range(10)]


# ═══════════════ 规则 3 回本提醒 ═══════════════

class TestRuleBreakeven:
    def test_deep_loss_recovered_triggers(self):
        rec = h("某深亏基金", ret=-0.01, **{FIELD_MIN_RET: -0.33, FIELD_FIRED: False})
        alerts, updates = evaluate_rules([rec, *bg()])
        r3 = [a for a in alerts if a["rule"] == 3]
        assert len(r3) == 1
        assert r3[0]["level"] == "建议" and "回本在即" in r3[0]["text"]
        assert "-33.0%" in r3[0]["text"]
        assert updates and updates[0][FIELD_FIRED] is True

    def test_no_history_no_trigger_but_backfills_min(self):
        """字段刚上线：无历史 min → 不触发，但把当前值回填为 min。"""
        rec = h("普通基金", ret=0.02)
        alerts, updates = evaluate_rules([rec, *bg()])
        assert not any(a["rule"] == 3 for a in alerts)
        assert updates[0][FIELD_MIN_RET] == pytest.approx(0.02)

    def test_never_deep_loss_never_triggers(self):
        """历史最低只有 −5% → 不算曾深亏，永远不响。"""
        rec = h("浅亏基金", ret=0.01, **{FIELD_MIN_RET: -0.05, FIELD_FIRED: False})
        alerts, updates = evaluate_rules([rec, *bg()])
        assert not any(a["rule"] == 3 for a in alerts)
        assert updates == []  # 值没变，不回写

    def test_fired_wont_refire_in_band(self):
        """已发过、仍在 ±3% 带内 → 不重复提醒，也不回写。"""
        rec = h("已提醒基金", ret=0.02, **{FIELD_MIN_RET: -0.20, FIELD_FIRED: True})
        alerts, updates = evaluate_rules([rec, *bg()])
        assert not any(a["rule"] == 3 for a in alerts)
        assert updates == []

    def test_rearm_on_fall_back_to_loss(self):
        """跌回 −5% 以下 → 复位已发标志（回写 False），再次回本可再提醒。"""
        rec = h("又跌回去基金", ret=-0.08, **{FIELD_MIN_RET: -0.20, FIELD_FIRED: True})
        alerts, updates = evaluate_rules([rec, *bg()])
        assert not any(a["rule"] == 3 for a in alerts)
        assert updates and updates[0][FIELD_FIRED] is False

    def test_rearm_on_clear_pass(self):
        rec = h("越过成本基金", ret=0.15, **{FIELD_MIN_RET: -0.20, FIELD_FIRED: True})
        _, updates = evaluate_rules([rec, *bg()])
        assert updates and updates[0][FIELD_FIRED] is False

    def test_new_low_records_min(self):
        """创新低 → min 更新并回写。"""
        rec = h("创新低基金", ret=-0.25, **{FIELD_MIN_RET: -0.20, FIELD_FIRED: False})
        alerts, updates = evaluate_rules([rec, *bg()])
        assert not any(a["rule"] == 3 for a in alerts)
        assert updates[0][FIELD_MIN_RET] == pytest.approx(-0.25)

    def test_fallback_ret_from_price_cost(self):
        """最新收益率缺失 → 现价/成本 兜底；未回本不触发。"""
        rec = h("缺收益率基金", ret=None, 现价=1.02, 成本均价=1.30,
                **{FIELD_MIN_RET: -0.30, FIELD_FIRED: False})
        alerts, _ = evaluate_rules([rec, *bg()])
        # 1.02/1.30 − 1 ≈ −21.5%，还在深亏区，不触发
        assert not any(a["rule"] == 3 for a in alerts)

    def test_fallback_ret_triggers_when_near_cost(self):
        rec = h("兜底触发基金", ret=None, 现价=1.29, 成本均价=1.30,
                **{FIELD_MIN_RET: -0.30, FIELD_FIRED: False})
        alerts, _ = evaluate_rules([rec, *bg()])
        r3 = [a for a in alerts if a["rule"] == 3]
        assert len(r3) == 1 and "回本在即" in r3[0]["text"]

    def test_unusable_ret_skipped(self):
        rec = h("无价格基金", ret=None, 现价=0, 成本均价=0)
        alerts, updates = evaluate_rules([rec, *bg()])
        assert alerts == [] and updates == []

    def test_band_boundary_inclusive(self):
        """恰好 +3% 边界算命中（闭区间）。"""
        rec = h("边界基金", ret=BREAKEVEN_BAND, **{FIELD_MIN_RET: -0.15, FIELD_FIRED: False})
        alerts, _ = evaluate_rules([rec, *bg()])
        assert len([a for a in alerts if a["rule"] == 3]) == 1


# ═══════════════ 规则 1 同主题业绩差 ═══════════════

class TestRuleThemeGap:
    def test_two_ev_cars_trigger(self):
        """头号案例：长城/天弘新能源车（主动型无指数，靠名称关键词归组）。"""
        recs = [
            h("长城全球新能源汽车C", ret=0.10, 市值=5000.0),
            h("天弘全球新能源汽车C", ret=-0.144, 市值=5000.0),
        ]
        alerts, _ = evaluate_rules(recs + bg())
        r1 = [a for a in alerts if a["rule"] == 1]
        assert len(r1) == 1
        assert "新能源车" in r1[0]["text"] and "24.4pp" in r1[0]["text"]
        assert "长城全球新能源汽车C" in r1[0]["text"] and "天弘全球新能源汽车C" in r1[0]["text"]

    def test_index_funds_group_by_index(self):
        recs = [
            h("A 基金", ret=0.30, idx="中证红利低波动100指数"),
            h("B 基金", ret=0.02, idx="中证红利低波动100指数"),
        ]
        alerts, _ = evaluate_rules(recs + bg())
        assert any(a["rule"] == 1 for a in alerts)

    def test_long_term_bucket_muted(self):
        """长期底仓桶禁言（Q2-C 语义）——同主题差再大也不提醒。"""
        recs = [
            h("X 指数C", ret=0.30, tag="长期底仓", idx="某指数", 市值=5000.0),
            h("Y 指数A", ret=-0.10, tag="长期底仓", idx="某指数", 市值=5000.0),
        ]
        alerts, _ = evaluate_rules(recs + bg())
        assert not any(a["rule"] == 1 for a in alerts)

    def test_gap_at_threshold_no_trigger(self):
        """恰好 20pp 不响（严格大于）。"""
        recs = [
            h("M 基金", ret=THEME_GAP, idx="T 指数"),
            h("N 基金", ret=0.0, idx="T 指数"),
        ]
        alerts, _ = evaluate_rules(recs + bg())
        assert not any(a["rule"] == 1 for a in alerts)

    def test_single_member_group_no_trigger(self):
        alerts, _ = evaluate_rules([h("孤儿基金", ret=-0.50, idx="独一份指数"), *bg()])
        assert not any(a["rule"] == 1 for a in alerts)

    def test_unusable_returns_ignored(self):
        recs = [h("P 基金", ret=None), h("Q 基金", ret=None)]
        alerts, _ = evaluate_rules(recs + bg())
        assert not any(a["rule"] == 1 for a in alerts)


# ═══════════════ 规则 5 单只占比保险丝 ═══════════════

class TestRuleSingleCap:
    def test_over_cap_triggers(self):
        recs = [h("巨无霸", ret=0.01, 市值=9000.0), h("小不点", ret=0.01, 市值=1000.0)]
        alerts, _ = evaluate_rules(recs)
        r5 = [a for a in alerts if a["rule"] == 5]
        assert len(r5) == 1 and "90.0%" in r5[0]["text"]

    def test_mv_fallback_price_times_shares(self):
        recs = [
            h("巨无霸2", ret=0.01, 市值=0, 现价=9.0, 持仓份额=1000.0),
            h("配角", ret=0.01, 市值=0, 现价=1.0, 持仓份额=1000.0),
        ]
        alerts, _ = evaluate_rules(recs)
        assert any(a["rule"] == 5 for a in alerts)

    def test_no_cap_violation_silent(self):
        recs = [h(f"持有人{i}", ret=0.01, 市值=100.0) for i in range(7)]  # 各 14.3%
        alerts, _ = evaluate_rules(recs)
        assert not any(a["rule"] == 5 for a in alerts)

    def test_all_zero_mv_silent(self):
        recs = [h("甲", ret=0.01, 市值=0, 现价=0), h("乙", ret=0.01, 市值=0, 现价=0)]
        alerts, _ = evaluate_rules(recs)
        assert not any(a["rule"] == 5 for a in alerts)


# ═══════════════ 汇总 / 渲染 / 接入 ═══════════════

class TestAggregateAndRender:
    def test_suggestion_level_first(self):
        recs = [
            h("巨无霸", ret=0.01, 市值=9000.0),
            h("小不点", ret=0.01, 市值=1000.0),
            h("回本基金", ret=0.0, **{FIELD_MIN_RET: -0.30, FIELD_FIRED: False}),
        ]
        alerts, _ = evaluate_rules(recs)
        assert alerts[0]["level"] == "建议" and alerts[0]["rule"] == 3

    def test_render_empty(self):
        assert render_alerts([]) == ""

    def test_render_contains_header_and_cap(self):
        alerts = [{"rule": i, "level": "提醒", "text": f"· 第{i}条"} for i in range(8)]
        text = render_alerts(alerts, max_lines=6)
        assert "决策参考（8 条" in text and "另有 2 条" in text

    def test_records_without_id_skipped(self):
        alerts, updates = evaluate_rules([{"标的名称": "无ID", "最新收益率": 0.0,
                                           FIELD_MIN_RET: -0.3, FIELD_FIRED: False}])
        assert updates == []


class TestFeishuIntegration:
    class _FakeClient:
        def __init__(self, holdings):
            self._holdings = holdings
            self.written = None

        def list_records(self, table):
            assert table == "底仓表"
            return self._holdings

        def batch_update_records(self, table, updates):
            self.written = updates
            return len(updates)

    def test_client_none_silent(self):
        assert build_rules_alert(None) == ""

    def test_happy_path_with_writeback(self):
        client = self._FakeClient([
            h("回本基金", ret=0.0, **{FIELD_MIN_RET: -0.30, FIELD_FIRED: False}),
            *bg(),
        ])
        text = build_rules_alert(client)
        assert "回本在即" in text
        assert client.written and client.written[0][FIELD_FIRED] is True

    def test_no_updates_no_write(self):
        client = self._FakeClient([
            h("躺平基金", ret=0.5, **{FIELD_MIN_RET: -0.1, FIELD_FIRED: False}),
            *bg(),
        ])
        assert build_rules_alert(client) == ""
        assert client.written is None

    def test_fail_silent_on_exception(self, caplog):
        class _Boom:
            def list_records(self, table):
                raise RuntimeError("飞书挂了")

        with caplog.at_level(logging.WARNING):
            assert build_rules_alert(_Boom()) == ""
        assert "规则引擎" in caplog.text
