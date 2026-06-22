"""
策略中枢 —— Python 硬编码核心护栏（防 AI 幻觉）。

核心理念：
  Python 死算数字，LLM 只负责翻译和安抚——绝不反过来。

四条小资金实战纪律：
  1. 阶梯式阈值（非死板 3%）
  2. 增量资金优先（每次 100-200 元，指向最有价值的加仓方向）
  3. 长底仓永不卖出（代码锁死）+ 自然稀释策略
  4. 动作冷却期（读飞书交易流水表，3 天内同大类不重复建议加仓）

用法：
    from src.strategy import judge_from_feishu
    verdict = judge_from_feishu()  →  { overall_verdict, signals, command, ... }
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from src.constants import TARGET_WEIGHTS

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 雷达大类映射：strategy 大类 → 雷达表大类
# ═══════════════════════════════════════════════════════════════

_RADAR_CLASS_MAP = {
    "A股资产": "A股资产",
    "港股资产": "港股资产",
    "美股资产": "美股资产",
    "避险商品": "避险商品",
    "固收资产": "固收资产",
}


def _fetch_radar_signals() -> dict[str, list[str]]:
    """读取雷达表有信号的标的，按资产大类分组。

    Returns:
        {"美股资产": ["🟢 趋势加速: QQQ"], "A股资产": ["🟡 关注: 515080"], ...}
    """
    try:
        from src.feishu_client import FeishuClient
        client = FeishuClient()
        records = client.list_records("雷达观测表")
        by_class: dict[str, list[str]] = {}
        for r in records:
            cls = r.get("资产大类", "")
            if isinstance(cls, list):
                cls = cls[0] if cls else ""
            cls = str(cls).strip()
            buy = r.get("抄底信号", "")
            chase = r.get("追涨信号", "")
            name = r.get("标的名称", "")
            if not cls or (not buy and not chase):
                continue
            sig = buy or chase
            by_class.setdefault(cls, []).append(f"{sig}: {name}")
        return by_class
    except Exception:
        return {}

# ═══════════════════════════════════════════════════════════════
# 阶梯阈值配置
# ═══════════════════════════════════════════════════════════════

THRESHOLD_STRONG_BUY = -0.10   # < -10%：极度低估，大额买入
THRESHOLD_BUY       = -0.05   # -10% ~ -5%：机会区间，加倍定投
THRESHOLD_HOLD_HIGH =  0.05   # -5% ~ +5%：正常，维持节奏
THRESHOLD_SOFT_WARN =  0.10   # +5% ~ +10%：轻度超配，观望
                               # > +10%：严重超配 → SELL（但标签可覆盖）

COOLDOWN_DAYS = 3  # 冷却期


# ═══════════════════════════════════════════════════════════════
# 信号字典
# ═══════════════════════════════════════════════════════════════

_SIGNAL_META = {
    "TRIGGER_STRONG_BUY": {"label": "🔴 强烈买入", "intensity": "strong",
        "explanation": "极度低估，建议大额买入或开启新仓（如投入 300-500 元）"},
    "TRIGGER_BUY":       {"label": "🟡 加倍定投", "intensity": "normal",
        "explanation": "机会区间，建议将日常定投金额翻倍（如从 100→200 元）"},
    "HOLD_AND_WAIT":     {"label": "✅ 维持节奏", "intensity": "mild",
        "explanation": "在正常波动范围内，维持现有小额定投节奏"},
    "TRIGGER_SELL":      {"label": "🔺 建议止盈", "intensity": "strong",
        "explanation": "严重超配，建议停止定投、适度止盈（受标签约束）"},
}


# ═══════════════════════════════════════════════════════════════
# 1. 阶梯阈值判定
# ═══════════════════════════════════════════════════════════════

def _determine_signal(deviation: float) -> str:
    """阶梯阈值——纯数学，不给 LLM 留余地。"""
    if deviation < THRESHOLD_STRONG_BUY:
        return "TRIGGER_STRONG_BUY"
    if deviation < THRESHOLD_BUY:
        return "TRIGGER_BUY"
    if deviation <= THRESHOLD_HOLD_HIGH:
        return "HOLD_AND_WAIT"
    if deviation <= THRESHOLD_SOFT_WARN:
        return "HOLD_AND_WAIT"
    return "TRIGGER_SELL"


# ═══════════════════════════════════════════════════════════════
# 2. 长底仓永不卖出 + 自然稀释策略
# ═══════════════════════════════════════════════════════════════

def _apply_long_bottom_override(
    signal: str,
    tags: list[str],
    deviation_pct: str,
    name: str,
    asset_class: str,
) -> tuple[str, str | None]:
    """长底仓永不卖出——代码锁死，LLM 无法推翻。

    极端不平衡处理：
      如果该大类标记了长底仓且正偏离严重（如固收 +50%），
      不卖出，改为 HOLD + 自然稀释提示：
      「用每月新增的 100-200 元只投到低配大类，不再往这里加钱，等别的慢慢追上来」
    """
    if "长期底仓" not in tags:
        return signal, None

    # BUY 类信号不变——长底仓在低配时可以也建议加仓
    if signal in ("TRIGGER_BUY", "TRIGGER_STRONG_BUY"):
        return signal, (
            f"🛡️「{name}」长期底仓，当前处于低配/低估区间，可以逢低买入"
        )

    # SELL → HOLD：长底仓不卖，永远不卖
    if signal == "TRIGGER_SELL":
        return "HOLD_AND_WAIT", (
            f"🛡️长底仓锁定，不卖。自然稀释：不再往{asset_class}投钱，"
            f"每月100-200元全部投向低配大类即可。"
        )

    return signal, None


# ═══════════════════════════════════════════════════════════════
# 2.5 防飞刀拦截（右侧交易保护）
# ═══════════════════════════════════════════════════════════════

def _apply_left_side_intercept(
    signal: str,
    tags: list[str],
    trend: str,
    name: str,
) -> tuple[str, str | None]:
    """防飞刀：自动趋势检测（替代手动标签）。

    读取 price_updater 写入的「趋势」字段：
    - "左侧下跌" → 拦截所有买入信号，等待右侧企稳
    - "右侧企稳" → 解除拦截，允许正常买入，并提示可以右侧入场
    - "横盘震荡" / ""  → 不拦截，按正常信号执行

    趋势由 price_updater 每天自动更新，无需手动维护标签。
    """
    if trend != "左侧下跌":
        if trend == "右侧企稳":
            # 刚解除下跌趋势，提示可以入场
            if signal in ("TRIGGER_BUY", "TRIGGER_STRONG_BUY"):
                return signal, (
                    f"✅「{name}」短期趋势已企稳（连续上涨），右侧入场信号出现，可以按计划买入"
                )
        return signal, None

    # 左侧下跌：拦截买入
    if signal in ("TRIGGER_BUY", "TRIGGER_STRONG_BUY"):
        return "HOLD_AND_WAIT", (
            f"🛑「{name}」当前处于下跌通道（5日趋势为左侧下跌），"
            f"虽然偏离度触发买入信号，但自动拦截以保护本金。"
            f"等待趋势转为「右侧企稳」后自动解除拦截。"
        )

    return signal, None


# ═══════════════════════════════════════════════════════════════
# 3. 冷却期检查 —— 读飞书交易流水表（无状态运行）
# ═══════════════════════════════════════════════════════════════

def _check_cooldown(client, asset_class: str) -> str | None:
    """从飞书交易流水表检查该大类最近是否真实买入过。

    无状态运行——每次从飞书直接查，不依赖本地文件。
    GitHub Actions 上照常工作。
    """
    if client is None:
        return None

    try:
        records = client.list_records("交易流水表")
    except Exception as e:
        logger.warning("冷却期检查失败（飞书不可用），跳过: %s", e)
        return None

    tz_cn = timezone(timedelta(hours=8))
    today = datetime.now(tz_cn)
    recent_buys = []

    for rec in records:
        # 只关心买入记录
        direction = rec.get("买卖方向", "")
        if isinstance(direction, list):
            direction = direction[0] if direction else ""
        if direction != "buy":
            continue

        # 大类匹配
        cls = rec.get("资产大类", "")
        if isinstance(cls, list):
            cls = cls[0] if cls else ""
        if cls != asset_class:
            continue

        # 时间检查
        trade_time_str = rec.get("交易时间")
        if not trade_time_str:
            continue
        try:
            if isinstance(trade_time_str, str):
                trade_time = datetime.fromisoformat(trade_time_str)
            elif isinstance(trade_time_str, (int, float)):
                trade_time = datetime.fromtimestamp(trade_time_str / 1000, tz=tz_cn)
            else:
                continue
        except (ValueError, OSError):
            continue

        days_diff = (today - trade_time.astimezone(tz_cn)).days
        if days_diff <= COOLDOWN_DAYS:
            recent_buys.append({
                "product": rec.get("产品名称", "未知"),
                "amount": rec.get("交易金额", 0),
                "days_ago": days_diff,
            })

    if recent_buys:
        b = recent_buys[0]
        return (
            f"⏳ 冷却期：{b['days_ago']} 天前刚买入「{b['product']}」"
            f"（¥{b['amount']}），同大类建议至少间隔 {COOLDOWN_DAYS} 天再加仓"
        )

    return None


# ═══════════════════════════════════════════════════════════════
# 4. 核心判定引擎
# ═══════════════════════════════════════════════════════════════

def judge(portfolio: list[dict], client=None) -> dict:
    """策略中枢主函数。

    Args:
        portfolio: 来自 advisor.load_portfolio() 的持仓列表
        client: 可选的 FeishuClient 实例（用于冷却期查询）

    Returns:
        {
            overall_verdict, priority_target, signals, command, psyche_facts
        }
    """
    # ── 1. 汇总大类市值 ──
    total_value = 0.0
    class_data: dict[str, dict[str, Any]] = {}

    for p in portfolio:
        mv = float(p.get("shares", 0)) * float(p.get("latest_price", 0))
        total_value += mv
        cls = p.get("asset_class", "未知")
        if cls not in class_data:
            class_data[cls] = {"market_value": 0.0, "positions": []}
        class_data[cls]["market_value"] += mv
        class_data[cls]["positions"].append(p)

    total_value = max(total_value, 0.01)

    # ── 2. 逐大类判定 ──
    signals = []
    has_action = False
    most_negative_class = None
    most_negative_dev = 0.0

    for cls, target_weight in TARGET_WEIGHTS.items():
        data = class_data.get(cls, {"market_value": 0.0, "positions": []})
        actual_weight = data["market_value"] / total_value
        deviation = actual_weight - target_weight
        deviation_pct_str = f"{deviation * 100:+.1f}%"

        # 阶梯阈值判定
        signal = _determine_signal(deviation)

        # 对该大类下的标的逐条检查长底仓标签 + 左侧观望拦截
        overrides = []
        effective_signal = signal
        any_position_free_to_buy = False  # 是否有任何仓位未被锁定

        for pos in data["positions"]:
            pos_tags = pos.get("tags", [])
            pos_name = pos.get("name", "")
            pos_free = True  # 这个仓位是否可以自由买入

            # 长底仓覆盖
            pos_sig, msg = _apply_long_bottom_override(
                signal, pos_tags, deviation_pct_str, pos_name, cls,
            )
            if msg:
                overrides.append(msg)
            # SELL → HOLD：长底仓不卖，且该仓位不参与自由买入判断
            if signal == "TRIGGER_SELL" and pos_sig == "HOLD_AND_WAIT":
                pos_free = False
            if signal == "TRIGGER_SELL" and pos_sig != "HOLD_AND_WAIT":
                any_position_free_to_buy = True  # 存在可以卖出的，保持 SELL

            # 防飞刀：自动趋势检测（price_updater 每天写入「趋势」字段）
            # 长底仓不拦截（已在 _apply_long_bottom_override 中处理）
            if "长期底仓" not in pos_tags and pos_sig in ("TRIGGER_BUY", "TRIGGER_STRONG_BUY"):
                pos_trend = pos.get("trend", "")
                inter_sig, inter_msg = _apply_left_side_intercept(
                    pos_sig, pos_tags, pos_trend, pos_name,
                )
                if inter_msg:
                    overrides.append(inter_msg)
                if inter_sig == "HOLD_AND_WAIT":
                    pos_free = False  # 被拦截了，这个仓位不能买

            if pos_free and pos_sig in ("TRIGGER_BUY", "TRIGGER_STRONG_BUY", "TRIGGER_SELL"):
                any_position_free_to_buy = True

        # 整类降级：只有当该类所有仓位都被锁定、且没有任何一个可以自由行动时，才降为 HOLD
        if signal in ("TRIGGER_SELL", "TRIGGER_BUY", "TRIGGER_STRONG_BUY"):
            if not any_position_free_to_buy and len(data["positions"]) > 0:
                effective_signal = "HOLD_AND_WAIT"

        # ── 时机闸门：偏离是水位，趋势才是时机 ──
        timing_msg = None
        if effective_signal in ("TRIGGER_BUY", "TRIGGER_STRONG_BUY"):
            # 检查该大类持仓的趋势：如果多数在左侧下跌，拦截买入
            left_count = sum(1 for p in data["positions"] if p.get("trend") == "左侧下跌")
            right_count = sum(1 for p in data["positions"] if p.get("trend") == "右侧企稳")
            total_with_trend = left_count + right_count + sum(
                1 for p in data["positions"] if p.get("trend") in ("横盘震荡", "")
            )
            # 如果有趋势数据，且大多数在左侧 → 降级
            if total_with_trend > 0 and left_count > right_count:
                effective_signal = "HOLD_AND_WAIT"
                timing_msg = f"⏸️偏离到位但趋势左侧，等企稳再动手（{left_count}只左侧/{total_with_trend}只）"
            elif right_count > 0:
                timing_msg = f"✅趋势右侧企稳（{right_count}只），偏离+趋势双确认"

        # ── 雷达联动：同大类有信号时加分 ──
        radar_signals = _fetch_radar_signals()
        radar_cls = _RADAR_CLASS_MAP.get(cls, cls)
        radar_hits = radar_signals.get(radar_cls, [])
        if radar_hits and effective_signal in ("TRIGGER_BUY", "TRIGGER_STRONG_BUY", "HOLD_AND_WAIT"):
            radar_line = "🔭雷达同步：" + "、".join(radar_hits[:2])
            if timing_msg:
                timing_msg = timing_msg + " | " + radar_line
            else:
                timing_msg = radar_line

        # 冷却期检查（从飞书交易流水表读真实买入记录）
        cooldown_msg = None
        if effective_signal in ("TRIGGER_BUY", "TRIGGER_STRONG_BUY"):
            cooldown_msg = _check_cooldown(client, cls)

        # 信号元数据
        meta = _SIGNAL_META.get(effective_signal, _SIGNAL_META["HOLD_AND_WAIT"])

        signals.append({
            "asset_class":  cls,
            "target_weight": f"{target_weight * 100:.0f}%",
            "actual_weight": f"{actual_weight * 100:.2f}%",
            "deviation":     round(deviation, 6),
            "deviation_pct": deviation_pct_str,
            "market_value":  round(data["market_value"], 2),
            "signal":        effective_signal,
            "signal_label":  meta["label"],
            "explanation":   meta["explanation"],
            "intensity":     meta["intensity"],
            "cooldown_status": cooldown_msg,
            "override":      (
                (" | ".join(overrides) if overrides else "")
                + ((" | " + timing_msg) if timing_msg and overrides else (timing_msg or ""))
            ) or None,
            "timing":         timing_msg,
            "positions": [
                {
                    "name": p.get("name", ""),
                    "shares": p.get("shares", 0),
                    "latest_price": p.get("latest_price", 0),
                    "market_value": round(p.get("shares", 0) * p.get("latest_price", 0), 2),
                    "tags": p.get("tags", []),
                }
                for p in data["positions"]
            ],
        })

        # 全局状态
        if effective_signal in ("TRIGGER_STRONG_BUY", "TRIGGER_BUY", "TRIGGER_SELL"):
            has_action = True
        if deviation < most_negative_dev:
            most_negative_dev = deviation
            most_negative_class = cls

    # ── 3. 全局判定 ──
    overall = "ACT" if has_action else "HOLD"

    # ── 4. 增量资金优先 ──
    priority_line = ""
    if most_negative_class and most_negative_dev < THRESHOLD_BUY:
        priority_line = (
            f"【增量资金优先方向】负偏离最大的是「{most_negative_class}」"
            f"（{most_negative_dev * 100:+.1f}%），"
            f"建议将本期新增资金（100-200 元）优先投向该大类。"
        )

    # ── 5. 组装全局指令 ──
    strong_buys = [s for s in signals if s["signal"] == "TRIGGER_STRONG_BUY"]
    buys       = [s for s in signals if s["signal"] == "TRIGGER_BUY"]
    sells      = [s for s in signals if s["signal"] == "TRIGGER_SELL"]
    holds      = [s for s in signals if s["signal"] == "HOLD_AND_WAIT"]

    if overall == "ACT":
        reasons = []
        for cls, target_weight in TARGET_WEIGHTS.items():
            for s in signals:
                if s["asset_class"] == cls and s["signal"] != "HOLD_AND_WAIT":
                    reasons.append(f"{cls}{s['deviation_pct']}")
                    break
        reason_str = "、".join(reasons)
        parts = [f"必须行动！原因：至少有一类资产偏离目标权重超过±5%（{reason_str}）"]
    else:
        parts = ["可以装死。所有大类偏离均在±5%以内，暂无需要操作的资产。"]

    if strong_buys:
        parts.append(f"强烈买入：{'、'.join(s['asset_class'] for s in strong_buys)}")
    if buys:
        parts.append(f"加倍定投：{'、'.join(s['asset_class'] for s in buys)}")
    if sells:
        parts.append(f"建议止盈：{'、'.join(s['asset_class'] for s in sells)}")
    if holds:
        parts.append(f"维持不变：{'、'.join(s['asset_class'] for s in holds)}")
    if priority_line:
        parts.append(priority_line)

    command = "今日判定：" + "\n".join(parts)

    # ── 6. 心理防御数据 ──
    psyche_facts = (
        "当前投入均为绝对闲钱，不影响日常生活质量。"
        "所有投资均无杠杆、无借贷。"
        "历史偏离度回归周期平均为 2-3 个月。"
        "纪律执行的历史胜率高于情绪化操作。"
    )

    return {
        "overall_verdict":   overall,
        "priority_target":    most_negative_class,
        "priority_deviation": round(most_negative_dev, 4),
        "signals":            signals,
        "command":            command,
        "psyche_facts":       psyche_facts,
        "total_value":        round(total_value, 2),
    }


# ═══════════════════════════════════════════════════════════════
# 便捷入口
# ═══════════════════════════════════════════════════════════════

def judge_from_feishu(client=None) -> dict:
    """从飞书底仓表读取持仓 → 运行策略判定。"""
    from src.feishu_client import FeishuClient
    from src.advisor import load_portfolio

    if client is None:
        try:
            client = FeishuClient()
        except Exception:
            client = None

    portfolio = load_portfolio(client)
    return judge(portfolio, client=client)
