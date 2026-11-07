"""Briefing snapshot persistence, diff calculation, and metric extraction."""

from __future__ import annotations

import hashlib
import logging

from src.briefing_modules.signals import _short_cls

logger = logging.getLogger(__name__)

# diff 阈值：超过这些值才视为"有变化"
_METRIC_THRESHOLDS = {
    "total_value": 50,        # ¥50（原 100：7 万组合日常波动常在阈值下）
    "deviation_us": 0.3,      # 0.3%（原 0.5：偏离度日波动 0.1-0.2%，原阈值几乎不可触发）
    "deviation_cn": 0.3,
    "deviation_hk": 0.3,
    "deviation_bond": 0.3,
    "deviation_safe": 0.3,
}


def _make_signature(card_text: str) -> str:
    """根据 card 文本生成短签名。前 800 字符足够区分（数据卡基本稳定）。"""
    sample = card_text[:800]
    return hashlib.md5(sample.encode("utf-8")).hexdigest()[:12]


def _load_prev_snapshot(slot: str) -> dict | None:
    """读某 slot 上次推送的快照。封装 feishu_client，本地/生产自动分发。"""
    from src.feishu_client import read_briefing_snapshot
    return read_briefing_snapshot(slot)


def _save_snapshot(slot: str, signature: str, key_metrics: dict) -> None:
    """写本时段快照。"""
    from src.feishu_client import write_briefing_snapshot
    write_briefing_snapshot(slot, key_metrics, signature)


def _diff_against_last(slot: str, current_signature: str | None,
                       current_metrics: dict) -> dict:
    """对比当前 vs 上次快照。

    Args:
        slot: 时段名
        current_signature: 本次信息面指纹（新闻标题+行情+快讯的 hash）。
            传 None 表示"调用点拿不到信息面"（极端容错），此时只按 metrics 判定。
        current_metrics: 本次关键指标

    Returns:
        {
            "has_change": bool,            # 总判定：是否有显著变化
            "signature_changed": bool,     # 信息面（新闻/行情）是否更新
            "metric_changes": [str, ...],  # 人类可读的变化列表
            "prev_signature": str,
            "prev_metrics": dict,
            "is_first_run": bool,          # 是否首次推送（无历史）
        }
    """
    from src import briefing
    load_fn = getattr(briefing, "_load_prev_snapshot", _load_prev_snapshot)
    prev = load_fn(slot)
    if prev is None:
        # 首次推送（或上次推送失败）—— 视为有变化，避免错过第一次 LLM
        return {
            "has_change": True,
            "signature_changed": True,
            "metric_changes": ["首次推送，无历史对比"],
            "prev_signature": "",
            "prev_metrics": {},
            "is_first_run": True,
        }

    prev_sig = prev.get("signature", "")
    prev_metrics = prev.get("payload", {})

    sig_changed = current_signature is not None and prev_sig != current_signature

    metric_thresholds = getattr(briefing, "_METRIC_THRESHOLDS", _METRIC_THRESHOLDS)
    metric_changes: list[str] = []
    for k, v in current_metrics.items():
        old = prev_metrics.get(k)
        if old is None:
            metric_changes.append(f"新增指标 {k}={v}")
            continue
        try:
            delta = abs(float(v) - float(old))
        except (TypeError, ValueError):
            continue
        thr = metric_thresholds.get(k, 0.5)
        if delta >= thr:
            metric_changes.append(f"{k}: {old} → {v} (Δ{delta:+.2f})")

    return {
        "has_change": sig_changed or bool(metric_changes),
        "signature_changed": sig_changed,
        "metric_changes": metric_changes,
        "prev_signature": prev_sig,
        "prev_metrics": prev_metrics,
        "is_first_run": False,
    }


def _format_diff_brief(diff: dict) -> str:
    """把 diff 转成给 LLM 看的"上下文卡"。"""
    if diff["is_first_run"]:
        return "（首次推送，无历史对比）"
    lines = []
    if diff["signature_changed"]:
        lines.append("- 信息面（新闻/行情/快讯）较上次推送有更新，请解读当日新信息")
    for ch in diff["metric_changes"][:5]:
        lines.append(f"- 持仓指标变化：{ch}")
    if not lines:
        lines.append("- 信息面与持仓指标均无显著变化")
    return "\n".join(lines)


def _extract_metrics_from_verdict(verdict: dict) -> dict:
    """从 judge() verdict 提取 diff 用的 key_metrics。

    Returns:
        {"total_value": float, "deviation_us": float, "deviation_cn": float, ...}
    """
    metrics: dict = {}
    total = verdict.get("total_value", 0)
    if total:
        metrics["total_value"] = round(float(total), 2)
    for sig in verdict.get("signals", []) or []:
        cls_short = _short_cls(sig.get("asset_class", ""))
        dev_text = sig.get("deviation_pct", "")
        try:
            if dev_text:
                dev = float(str(dev_text).rstrip("%"))
            else:
                actual = float(str(sig.get("actual_weight", "0")).rstrip("%"))
                target = float(str(sig.get("target_weight", "0")).rstrip("%"))
                dev = actual - target
            metrics[f"deviation_{cls_short}"] = round(dev, 2)
        except Exception:
            continue
    return metrics


def _extract_metrics_from_pf(pf: list[dict] | None) -> dict:
    """无 verdict 时 fallback：从 pf + 默认 target 算 metrics。"""
    if not pf:
        return {}
    from src.advisor import calculate_rebalance
    try:
        rb = calculate_rebalance(pf)
    except Exception:
        return {"total_value": 0}
    total = rb.get("total_value", 0)
    metrics = {"total_value": round(float(total), 2)} if total else {}
    for d in rb.get("deviation_report", []) or []:
        cls = d.get("asset_class", "")
        cls_short = _short_cls(cls)
        try:
            dev = float(str(d.get("deviation_pct", "0")).rstrip("%"))
            metrics[f"deviation_{cls_short}"] = round(dev, 2)
        except Exception:
            continue
    return metrics
