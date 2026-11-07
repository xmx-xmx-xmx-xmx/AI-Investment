"""Hard quantitative signals block formatting for LLM prompt injection."""

from __future__ import annotations

# 信号优先级排序：触发行动的排前，HOLD 排后
_SIGNAL_PRIORITY = {
    "TRIGGER_STRONG_BUY": 0,
    "TRIGGER_BUY":        1,
    "TRIGGER_SELL":       2,
    "HOLD_AND_WAIT":      3,
}

# 简称映射（与 prompt_templates.py 保持一致：固收→美股→A股→港股→避险）
_ASSET_SHORT = {
    "固收资产": "固收",
    "美股资产": "美股",
    "A股资产":  "A股",
    "港股资产": "港股",
    "避险商品": "避险商品",
}


def _short_cls(cls: str) -> str:
    return _ASSET_SHORT.get(cls, cls)


def _build_hard_signals_block(verdict: dict | None) -> str:
    """把 judge() 的 signals 列表转成 LLM 可读的结构化硬信号。

    设计目标：
      - D9 的投资宪法是"软"约束（讲理念），这里补"硬"约束（具体判定）
      - LLM 拿到新闻解读时，必须与量化系统的硬信号对齐，不能自相矛盾
      - 例如：硬信号说"美股 TRIGGER_BUY，但冷却期未过" → LLM 就不该建议本周加仓美股

    Args:
        verdict: judge() / judge_from_feishu() 返回的完整 dict（含 signals 列表）

    Returns:
        格式化的 <hard_signals> 段字符串。verdict 为空时返回空串（注入位置不显示该段）。
    """
    if not verdict or not isinstance(verdict, dict):
        return ""

    signals = verdict.get("signals") or []
    if not signals:
        return ""

    # 按信号优先级排序
    sorted_sigs = sorted(
        signals,
        key=lambda s: _SIGNAL_PRIORITY.get(s.get("signal", ""), 9),
    )

    lines = ["<hard_signals>"]
    lines.append("【量化系统硬判定】以下是各持仓大类的偏离度与系统建议（请勿与之矛盾）：")
    lines.append("")

    for s in sorted_sigs:
        cls = s.get("asset_class", "")
        target = s.get("target_weight", "")
        actual = s.get("actual_weight", "")
        dev = s.get("deviation_pct", "")
        label = s.get("signal_label", "")
        override = s.get("override") or ""
        timing = s.get("timing") or ""
        cooldown = s.get("cooldown_status") or ""

        # 偏离度状态 emoji
        try:
            dev_val = float(str(dev).replace("%", "").replace("+", ""))
            if dev_val > 5:
                status = "⚠️ 超配"
            elif dev_val < -5:
                status = "🔻 低配"
            else:
                status = "✅ 正常"
        except (ValueError, AttributeError):
            status = ""

        # 额外约束（override / timing / cooldown）合并
        extras_parts = [override]
        if timing and not (override and timing in str(override)):
            extras_parts.append(timing)
        extras_parts.append(cooldown)
        extras = " ｜ ".join(p for p in extras_parts if p)
        extras_short = f"\n    {extras}" if extras else ""

        lines.append(f"◆ {_short_cls(cls)}：实占 {actual}（目标 {target}）偏离 {dev}　{status}　{label}{extras_short}")

    lines.append("")

    # 整体判定
    action = verdict.get("overall_verdict", "HOLD")
    overall = (
        "整体判定：触发配置 — 至少 1 大类需行动"
        if action == "ACT"
        else "整体判定：维持 — 所有大类偏离均在 ±5% 阈值内，长底仓按兵不动"
    )
    lines.append(overall)

    # 增量资金优先方向（如果有）
    priority = verdict.get("priority_target", "")
    if priority:
        lines.append(f"增量资金优先方向：{priority}")

    lines.append("</hard_signals>")

    return "\n".join(lines)
