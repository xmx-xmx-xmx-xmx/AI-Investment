"""
决策日志模块 (#30)
记录每个时段简报推送后的决策结论、建议动作、触发规则、资金盘子与大类偏离度，
全自动持久化至飞书「决策日志表」，为月底对账与规则复盘提供唯一基准。
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _extract_conclusion(
    slot: str, dyn_title: str, dyn_template: str, verdict: Optional[Dict[str, Any]]
) -> str:
    """提取或生成决策结论。"""
    if slot == "closing":
        if dyn_template == "green":
            return "建议买入加仓"
        elif dyn_template == "red":
            return "风控拦截/防御"
        elif dyn_template == "yellow":
            return "维持定投不动"
        if verdict and verdict.get("action"):
            return str(verdict["action"])
        return "收盘常规监控"

    if slot == "morning":
        return "早盘隔夜研判"
    elif slot == "midday":
        return "午间行情扫描"
    elif slot == "evening":
        return "夜盘美股前瞻"
    elif slot == "sat_morning":
        return "全周复盘与同类对比"
    elif slot == "sun_evening":
        return "周日下周仓位前瞻"

    if dyn_title:
        clean = re.sub(r"^[^\w\u4e00-\u9fa5]+", "", dyn_title).strip()
        return clean[:30]
    return "常规监控"


def _extract_action(card: str, verdict: Optional[Dict[str, Any]]) -> str:
    """提取建议动作。"""
    if verdict and verdict.get("suggestion"):
        return str(verdict["suggestion"]).strip()

    # 从卡片正文中正则提取
    m = re.search(r"【增量资金优先方向】([^\n]+)", card)
    if m:
        return m.group(1).strip()

    m2 = re.search(r"(?:建议|优先)(?:将本期新增资金[^\n。]+|投向[^\n。]+)", card)
    if m2:
        return m2.group(0).strip()

    return "维持既定节奏，观察组合偏离度"


def _extract_signals(card: str, rules_alert: str, verdict: Optional[Dict[str, Any]]) -> str:
    """提取触发规则与信号。"""
    signals = []

    # 1. 规则引擎告警 (rules_alert)
    if rules_alert:
        for line in rules_alert.splitlines():
            line = line.strip()
            if line.startswith("·") or line.startswith("-"):
                # 清洗加粗等标记
                clean = re.sub(r"[*_`]", "", line).lstrip("·- ").strip()
                if clean:
                    signals.append(clean[:80])

    # 2. verdict 中的 hard signals
    if verdict and verdict.get("hard_signals"):
        for hs in verdict["hard_signals"]:
            if isinstance(hs, str):
                signals.append(hs[:80])
            elif isinstance(hs, dict):
                msg = hs.get("message") or hs.get("name") or str(hs)
                signals.append(msg[:80])

    # 3. 雷达信号
    radar_m = re.findall(r"([🔵🟢🔴]\s*[^\n|]+)", card)
    if radar_m:
        signals.extend([r.strip() for r in radar_m[:3]])

    if signals:
        return "；".join(signals[:5])
    return "无特殊触发（常规监控）"


def _extract_total_value(
    card: str, verdict: Optional[Dict[str, Any]], portfolio: Optional[List[Dict[str, Any]]]
) -> float:
    """提取组合资金盘子总市值（人民币折算）。"""
    if verdict and verdict.get("total_value"):
        try:
            return round(float(verdict["total_value"]), 2)
        except Exception:
            pass

    if portfolio:
        try:
            from src.advisor import calculate_rebalance
            rb = calculate_rebalance(portfolio)
            if rb and rb.get("total_value"):
                return round(float(rb["total_value"]), 2)
        except Exception:
            pass

    # 从卡片正则提取总市值
    m = re.search(r"总市值[^\d]*¥?([0-9,]+(?:\.[0-9]+)?)", card)
    if m:
        try:
            val_str = m.group(1).replace(",", "")
            return round(float(val_str), 2)
        except Exception:
            pass

    return 0.0


def _extract_deviations(
    card: str, verdict: Optional[Dict[str, Any]], portfolio: Optional[List[Dict[str, Any]]]
) -> str:
    """提取各大类偏离度汇总字符串。"""
    dev_parts = []

    # 1. 从 verdict
    if verdict and verdict.get("signals"):
        for sig in verdict["signals"]:
            cls = sig.get("asset_class", "")
            dev = sig.get("deviation_pct", "")
            if cls and dev:
                dev_parts.append(f"{cls}{dev}")
        if dev_parts:
            return ", ".join(dev_parts)

    # 2. 从 portfolio
    if portfolio:
        try:
            from src.advisor import calculate_rebalance
            rb = calculate_rebalance(portfolio)
            for d in rb.get("deviation_report", []):
                cls = d.get("asset_class", "").replace("资产", "")
                dev = d.get("deviation_pct", 0)
                dev_parts.append(f"{cls}{dev:+.1f}%")
            if dev_parts:
                return ", ".join(dev_parts)
        except Exception:
            pass

    # 3. 从卡片正则提取
    devs = re.findall(r"·\s*([^：:\n]+?)(?:资产|商品)?：[^偏离\n]*偏离([+-]?\d+(?:\.\d+)?%?)", card)
    if devs:
        return ", ".join(f"{k.strip()}{v}" for k, v in devs)

    return ""


def _extract_market_vix(card: str) -> str:
    """提取当期关键行情与 VIX。"""
    items = []

    # VIX 恐慌指数
    vix_m = re.search(r"VIX[^\d]*([0-9]+(?:\.[0-9]+)?(?:[^\n]*\))?)", card)
    if vix_m:
        clean_vix = vix_m.group(0).replace("*", "").strip()
        items.append(clean_vix)

    # 标普/纳指
    spx_m = re.search(r"标普500[：:][^\n]*", card)
    if spx_m:
        items.append(spx_m.group(0).replace("*", "").strip("· "))

    qqq_m = re.search(r"纳斯达克100[：:][^\n]*", card)
    if qqq_m:
        items.append(qqq_m.group(0).replace("*", "").strip("· "))

    # 期货
    fut_m = re.findall(r"·\s*(?:纳指|标普)期货[：:][^\n]+", card)
    if fut_m:
        items.extend([f.strip("· ") for f in fut_m[:2]])

    return " | ".join(items[:4])


def record_briefing_decision(
    slot: str,
    card: str,
    dyn_title: str = "",
    dyn_template: str = "",
    rules_alert: str = "",
    verdict: Optional[Dict[str, Any]] = None,
    portfolio: Optional[List[Dict[str, Any]]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """构建决策日志实体并写入飞书决策日志表。

    Args:
        slot: 时段名称（morning/closing/evening/sat_morning/sun_evening/midday）
        card: 最终生成的简报卡片文本
        dyn_title: 动态锁屏标题
        dyn_template: 卡片模板颜色（green/red/yellow 等）
        rules_alert: 规则引擎产生的告警文本
        verdict: 策略判断字典（如有）
        portfolio: 持仓列表（如有）
        dry_run: 是否为 dry-run 模式

    Returns:
        写入的日志条目字典
    """
    today_str = datetime.now().strftime("%Y-%m-%d")

    conclusion = _extract_conclusion(slot, dyn_title, dyn_template, verdict)
    action = _extract_action(card, verdict)
    signals = _extract_signals(card, rules_alert, verdict)
    total_value = _extract_total_value(card, verdict, portfolio)
    deviations = _extract_deviations(card, verdict, portfolio)
    market_vix = _extract_market_vix(card)

    entry = {
        "日期": today_str,
        "时段": slot,
        "决策结论": conclusion[:100],
        "建议动作": action[:300],
        "触发规则与信号": signals[:300],
        "总市值": round(float(total_value), 2),
        "各大类偏离度": deviations[:200],
        "核心行情与VIX": market_vix[:200],
        "执行状态": "待对账",
        "对账备注": "",
    }

    try:
        from src.feishu_client import write_decision_log
        res = write_decision_log(entry)
        logger.info("[决策日志] 记录成功 slot=%s, result=%s, mv=%.2f", slot, res, total_value)
    except Exception as e:
        logger.warning("[决策日志] 写入失败 (不影响推送): %s", e)

    return entry
