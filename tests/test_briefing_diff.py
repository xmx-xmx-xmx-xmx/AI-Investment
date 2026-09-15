# -*- coding: utf-8 -*-
"""briefing.py 变化感知 (E 改造) 单元测试。

覆盖 2026-09-15 二次校正的核心：signature 语义从"卡片文本 hash（含 HH:MM 恒变）"
改为"信息面指纹（当日新闻+行情+快讯）"，修复用户实测的
"持仓微动但新闻换了 → 仍被判无变化 → 天天维持不动" 的 bug。
"""

from __future__ import annotations


def _patch_prev(monkeypatch, prev: dict | None):
    """把上一期快照替换成桩数据（None = 首次推送）。"""
    monkeypatch.setattr("src.briefing._load_prev_snapshot", lambda slot: prev)


class TestDiffAgainstLast:
    """判据 = 信息面指纹变化 OR 持仓指标超阈值。"""

    def test_first_run_always_has_change(self, monkeypatch):
        _patch_prev(monkeypatch, None)
        from src.briefing import _diff_against_last
        d = _diff_against_last("morning", "abc", {})
        assert d["has_change"] is True
        assert d["is_first_run"] is True

    def test_info_surface_changed_triggers_despite_flat_metrics(self, monkeypatch):
        """核心痛点：固收为主组合持仓纹丝不动（Δ¥30 / Δ0.1%），但新闻换了 → 必须调 LLM。"""
        _patch_prev(monkeypatch, {
            "signature": "old_sig",
            "payload": {"total_value": 70000, "deviation_us": 0.1},
        })
        from src.briefing import _diff_against_last
        d = _diff_against_last("morning", "new_sig",
                               {"total_value": 70030, "deviation_us": 0.2})
        assert d["has_change"] is True
        assert d["signature_changed"] is True
        assert d["metric_changes"] == []          # 指标确实都没超阈值

    def test_identical_info_surface_skips(self, monkeypatch):
        """同日重跑 / 假期：信息面与指标都没动 → 跳过 LLM。"""
        _patch_prev(monkeypatch, {
            "signature": "same_sig",
            "payload": {"total_value": 70000},
        })
        from src.briefing import _diff_against_last
        d = _diff_against_last("morning", "same_sig", {"total_value": 70010})
        assert d["has_change"] is False
        assert d["signature_changed"] is False

    def test_real_metric_move_reported(self, monkeypatch):
        """持仓真动（Δ¥300 / Δ0.4%）→ metric_changes 逐项点名。"""
        _patch_prev(monkeypatch, {
            "signature": "same_sig",
            "payload": {"total_value": 70000, "deviation_us": 20.0},
        })
        from src.briefing import _diff_against_last
        d = _diff_against_last("morning", "same_sig",
                               {"total_value": 70300, "deviation_us": 20.4})
        assert d["has_change"] is True
        assert any("total_value" in c for c in d["metric_changes"])
        assert any("deviation_us" in c for c in d["metric_changes"])

    def test_signature_none_falls_back_to_metrics(self, monkeypatch):
        """极端容错：拿不到信息面指纹时只按指标判定，不会因 None 恒判「有变化」。"""
        _patch_prev(monkeypatch, {
            "signature": "old_sig",
            "payload": {"total_value": 70000},
        })
        from src.briefing import _diff_against_last
        d = _diff_against_last("morning", None, {"total_value": 70010})
        assert d["has_change"] is False

    def test_thresholds_are_sensitive_enough(self):
        """阈值已下调：¥50 / 0.3%（原 ¥100 / 0.5% 对 7 万固收组合几乎不可触发）。"""
        from src.briefing import _METRIC_THRESHOLDS
        assert _METRIC_THRESHOLDS["total_value"] <= 50
        assert _METRIC_THRESHOLDS["deviation_us"] <= 0.3


class TestFormatDiffBrief:
    """注入 LLM 的 diff 文案要与新语义一致。"""

    def test_first_run_text(self):
        from src.briefing import _format_diff_brief
        assert "首次推送" in _format_diff_brief({"is_first_run": True})

    def test_info_update_text(self):
        from src.briefing import _format_diff_brief
        out = _format_diff_brief({
            "is_first_run": False, "signature_changed": True, "metric_changes": [],
        })
        assert "信息面" in out

    def test_metric_change_prefixed(self):
        from src.briefing import _format_diff_brief
        out = _format_diff_brief({
            "is_first_run": False, "signature_changed": False,
            "metric_changes": ["total_value: 70000 → 70300 (Δ+300.00)"],
        })
        assert "持仓指标变化" in out

    def test_no_change_text(self):
        from src.briefing import _format_diff_brief
        out = _format_diff_brief({
            "is_first_run": False, "signature_changed": False, "metric_changes": [],
        })
        assert "均无显著变化" in out
