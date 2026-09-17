# -*- coding: utf-8 -*-
"""briefing.py `_build_hard_signals_block` 单元测试。

D 任务（2026-09-05）在投资宪法之后注入 `<hard_signals>` 段，把 judge() 的
量化硬判定（偏离度 / 信号 / 冷却期）交给 LLM，避免 AI 解读与纪律自相矛盾。
这个函数直接决定 LLM 看到的"硬约束"文本，此前**零测试**。

覆盖：
  - 空值容错（None / 无 signals → 不输出该段，注入位置不显示）
  - 三类偏离状态判定（超配 / 低配 / 正常）与 ±5% 边界
  - 信号优先级排序（STRONG_BUY → BUY → SELL → HOLD_AND_WAIT → 未知最后）
  - override / timing / cooldown 附加约束的合并与去重
  - 整体判定文案（ACT / HOLD）与增量资金优先方向
  - 大类简称映射（固收资产 → 固收），与 prompt_templates.py 的排序保持一致
"""

from __future__ import annotations


def _sig(**over) -> dict:
    """构造一条接近 strategy.judge() 真实输出的 signal 项。"""
    s = {
        "asset_class": "美股资产",
        "target_weight": "20%",
        "actual_weight": "26.20%",
        "deviation": 0.062,
        "deviation_pct": "+6.2%",
        "signal": "TRIGGER_SELL",
        "signal_label": "🔺 建议止盈",
        "cooldown_status": None,
        "override": None,
        "timing": None,
    }
    s.update(over)
    return s


def _block(verdict):
    from src.briefing import _build_hard_signals_block
    return _build_hard_signals_block(verdict)


class TestEmptyInputs:
    """verdict 为空时必须返回空串 —— 否则 AI prompt 里会出现空的 <hard_signals> 段。"""

    def test_none_verdict_returns_empty(self):
        assert _block(None) == ""

    def test_non_dict_verdict_returns_empty(self):
        assert _block("not-a-dict") == ""
        assert _block([]) == ""

    def test_dict_without_signals_returns_empty(self):
        assert _block({}) == ""
        assert _block({"overall_verdict": "HOLD"}) == ""

    def test_empty_signals_list_returns_empty(self):
        assert _block({"signals": []}) == ""


class TestDeviationStatus:
    """偏离度状态 emoji：> 5% 超配 / < -5% 低配 / 其余正常。边界是 ±5 本身算正常。"""

    def test_overweight(self):
        out = _block({"signals": [_sig(deviation_pct="+6.2%")]})
        assert "⚠️ 超配" in out

    def test_underweight(self):
        out = _block({"signals": [_sig(deviation_pct="-8.0%")]})
        assert "🔻 低配" in out

    def test_normal(self):
        out = _block({"signals": [_sig(deviation_pct="+1.5%")]})
        assert "✅ 正常" in out

    def test_boundary_exactly_five_is_normal(self):
        """+5.0% 恰好等于阈值 → 不算超配（判定是严格大于）。"""
        out = _block({"signals": [_sig(deviation_pct="+5.0%")]})
        assert "✅ 正常" in out
        assert "⚠️ 超配" not in out

    def test_boundary_just_over_five_is_overweight(self):
        out = _block({"signals": [_sig(deviation_pct="+5.01%")]})
        assert "⚠️ 超配" in out

    def test_negative_boundary_five_is_normal(self):
        out = _block({"signals": [_sig(deviation_pct="-5.0%")]})
        assert "✅ 正常" in out
        assert "🔻 低配" not in out

    def test_deviation_without_percent_sign_still_parsed(self):
        """deviation_pct 也可能是不带 % 的字符串（如 "6.2"）→ 仍应判出状态。"""
        out = _block({"signals": [_sig(deviation_pct="6.2")]})
        assert "⚠️ 超配" in out

    def test_unparsable_deviation_does_not_crash(self):
        """脏数据不该让整个简报崩掉 —— 只是没有状态 emoji。"""
        out = _block({"signals": [_sig(deviation_pct="N/A")]})
        assert "<hard_signals>" in out
        for emoji in ("⚠️ 超配", "🔻 低配", "✅ 正常"):
            assert emoji not in out


class TestPriorityOrdering:
    """LLM 先看到"最该行动"的大类。顺序 = _SIGNAL_PRIORITY。"""

    def test_sorted_by_signal_priority(self):
        out = _block({"signals": [
            _sig(asset_class="港股资产", signal="HOLD_AND_WAIT"),
            _sig(asset_class="美股资产", signal="TRIGGER_SELL"),
            _sig(asset_class="A股资产", signal="TRIGGER_STRONG_BUY"),
            _sig(asset_class="固收资产", signal="TRIGGER_BUY"),
        ]})
        # 期望顺序（_SIGNAL_PRIORITY）：STRONG_BUY(0) → BUY(1) → SELL(2) → HOLD_AND_WAIT(3)
        pos = {n: out.index("◆ " + n) for n in ("A股", "固收", "美股", "港股")}
        assert pos["A股"] < pos["固收"] < pos["美股"] < pos["港股"]

    def test_strong_buy_before_buy(self):
        out = _block({"signals": [
            _sig(asset_class="美股资产", signal="TRIGGER_BUY"),
            _sig(asset_class="A股资产", signal="TRIGGER_STRONG_BUY"),
        ]})
        assert out.index("◆ A股") < out.index("◆ 美股")

    def test_unknown_signal_sorted_last(self):
        """未知 signal 值优先度 9（最低），排在有名单的通类之后。"""
        out = _block({"signals": [
            _sig(asset_class="港股资产", signal="SOMETHING_NEW"),
            _sig(asset_class="美股资产", signal="HOLD_AND_WAIT"),
        ]})
        assert out.index("◆ 美股") < out.index("◆ 港股")

    def test_missing_signal_key_treated_as_unknown(self):
        out = _block({"signals": [
            _sig(asset_class="港股资产", signal=None),
            _sig(asset_class="固收资产", signal="TRIGGER_BUY"),
        ]})
        assert out.index("◆ 固收") < out.index("◆ 港股")


class TestExtrasMerging:
    """override / timing / cooldown 是叠加在信号上的额外约束，必须原样带给 LLM。"""

    def test_all_three_joined(self):
        out = _block({"signals": [_sig(
            override="长期底仓，不因短期偏离卖出",
            timing="距上次买入 1 天",
            cooldown_status="⏳ 冷却期未过",
        )]})
        assert "长期底仓，不因短期偏离卖出" in out
        assert "距上次买入 1 天" in out
        assert "⏳ 冷却期未过" in out

    def test_timing_not_duplicated_when_already_inside_override(self):
        """strategy.judge() 会把 timing 拼进 override 串里；此处不得再追加一遍。

        重复的约束会让 LLM 以为存在两条不同限制，正是 hard_signals
        "消除矛盾解读" 要避免的情况。
        """
        out = _block({"signals": [_sig(
            override="长期底仓，不因短期偏离卖出 | 距上次买入 1 天",
            timing="距上次买入 1 天",
        )]})
        assert out.count("距上次买入 1 天") == 1

    def test_none_extras_produce_no_trailing_text(self):
        out = _block({"signals": [_sig()]})
        assert "\n    " not in out.split("◆")[1].split("<")[0]

    def test_only_cooldown(self):
        out = _block({"signals": [_sig(cooldown_status="⏳ 冷却期未过（2/3 天）")]})
        assert "⏳ 冷却期未过（2/3 天）" in out


class TestOverallAndPriority:
    """段尾的整体判定与增量方向 —— LLM 最常引用的两句。"""

    def test_act_message(self):
        out = _block({"signals": [_sig()], "overall_verdict": "ACT"})
        assert "触发配置" in out
        assert "维持" not in out

    def test_hold_message(self):
        out = _block({"signals": [_sig()], "overall_verdict": "HOLD"})
        assert "维持" in out
        assert "触发配置" not in out

    def test_missing_overall_verdict_defaults_to_hold(self):
        out = _block({"signals": [_sig()]})
        assert "维持" in out

    def test_priority_target_line_present(self):
        out = _block({"signals": [_sig()], "priority_target": "美股资产"})
        assert "增量资金优先方向" in out

    def test_priority_target_line_absent_when_empty(self):
        out = _block({"signals": [_sig()], "priority_target": ""})
        assert "增量资金优先方向" not in out


class TestFormatting:
    """格式契约：标签成对、大类用简称、关键数字都在。"""

    def test_tags_are_balanced(self):
        out = _block({"signals": [_sig()]})
        assert out.startswith("<hard_signals>")
        assert out.endswith("</hard_signals>")
        assert out.count("<hard_signals>") == 1
        assert out.count("</hard_signals>") == 1

    def test_asset_class_shortened(self):
        out = _block({"signals": [_sig(asset_class="固收资产")]})
        assert "◆ 固收" in out
        assert "固收资产" not in out

    def test_unknown_asset_class_passes_through(self):
        out = _block({"signals": [_sig(asset_class="另类资产")]})
        assert "◆ 另类资产" in out

    def test_weights_and_label_rendered(self):
        out = _block({"signals": [_sig()]})
        assert "实占 26.20%" in out
        assert "目标 20%" in out
        assert "偏离 +6.2%" in out
        assert "🔺 建议止盈" in out

    def test_header_mentions_no_contradiction(self):
        """提示词层面的关键约束：LLM 不得与硬信号矛盾。"""
        out = _block({"signals": [_sig()]})
        assert "请勿与之矛盾" in out
