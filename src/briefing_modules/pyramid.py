"""金字塔结构动作卡模块（#31 "不动日"卡片金字塔重构 / 体验降噪与闲暇优化）。

设计目标：
1. 顶层（3秒扫视区）：
   - 核心指令（红绿灯：绿色=维持观望 / 橙色=有操作信号 / 红色=紧急风控）
   - 一句话定心丸（数据驱动归因：为什么今天完全不用动，固收压舱石+偏离安全+底仓奔跑）
   - 核心底线阈值（行动警戒线：距再平衡还差多少、距逢跌低吸还差多少、VIX安全垫）
2. 中层（选做区）：
   - 闲暇轻度优化建议（若今日刚好有空打理，最推荐的 1~2 项优化：增量打卡以买代卖拉平仓位、观察仓/碎屑归并；没空直接忽略）
3. 底层（结构化数据）：
   - 结合 compact 模式，平淡日折叠收敛次要指标，强信号日深度展开。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 资产大类简称映射
_ASSET_SHORT = {
    "固收资产": "固收",
    "美股资产": "美股",
    "A股资产": "A股",
    "港股资产": "港股",
    "避险商品": "避险商品",
}


def _short_name(cls_name: str) -> str:
    """归一大类名称为简短表达。"""
    return _ASSET_SHORT.get(cls_name, cls_name)


def _extract_vix_val(vix_text_or_val: Any) -> Optional[float]:
    """从文本或数值提取 VIX 浮点值。"""
    if vix_text_or_val is None:
        return None
    if isinstance(vix_text_or_val, (int, float)):
        return float(vix_text_or_val)
    if isinstance(vix_text_or_val, dict):
        v = vix_text_or_val.get("vix")
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    if isinstance(vix_text_or_val, str):
        m = re.search(r"VIX[^\d]*?(\d+(?:\.\d+)?)", vix_text_or_val)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return None


def build_reassurance_sentence(
    verdict: Optional[Dict[str, Any]] = None,
    portfolio: Optional[List[Dict[str, Any]]] = None,
    vix_val: Optional[Any] = None,
    has_action: bool = False,
) -> str:
    """生成「一句话定心丸」：基于硬数据归因，解答'为什么今天彻底不用操作'。"""
    vix = _extract_vix_val(vix_val)

    # 1. 扫描大类偏离度与固收占比
    signals = (verdict.get("signals") if verdict else []) or []
    max_dev = 0.0
    max_cls = ""
    bond_actual_pct = 0.0

    for s in signals:
        cls_name = s.get("asset_class", "")
        dev_str = str(s.get("deviation_pct", "0%")).replace("%", "").replace("+", "")
        act_str = str(s.get("actual_weight", "0%")).replace("%", "")
        try:
            dev = float(dev_str)
            act = float(act_str)
            if "固收" in cls_name:
                bond_actual_pct = act
            if abs(dev) > abs(max_dev):
                max_dev = dev
                max_cls = cls_name
        except (ValueError, TypeError):
            continue

    # 若未从 signals 中拿到固收占比，尝试从 portfolio 现算
    if bond_actual_pct <= 0 and portfolio:
        total_mv = 0.0
        bond_mv = 0.0
        for p in portfolio:
            cls_raw = p.get("asset_class") or p.get("资产大类") or ""
            if isinstance(cls_raw, list) and cls_raw:
                cls_raw = cls_raw[0]
            shares = float(p.get("shares") or p.get("持仓份额") or 0)
            price = float(p.get("latest_price") or p.get("现价") or p.get("cost") or p.get("成本均价") or 0)
            mv = shares * price
            total_mv += mv
            if "固收" in str(cls_raw):
                bond_mv += mv
        if total_mv > 0:
            bond_actual_pct = (bond_mv / total_mv) * 100.0

    # 2. VIX 状态表述
    vix_text = ""
    if vix is not None and vix > 0:
        if vix < 20.0:
            vix_text = f"，VIX={vix:.1f} 处安全舒适区"
        elif vix < 25.0:
            vix_text = f"，VIX={vix:.1f} 处于温和区间"
        else:
            vix_text = f"，VIX={vix:.1f} 提示市场波动中（守住底仓不追涨）"

    # 3. 固收压舱石状态
    bond_text = ""
    if bond_actual_pct >= 40.0:
        bond_text = f"固收底盘占 {bond_actual_pct:.0f}% 稳健防守"
    elif bond_actual_pct > 0:
        bond_text = f"固收底盘占 {bond_actual_pct:.0f}%"

    # 4. 偏离度状态
    if abs(max_dev) < 3.0:
        if max_cls:
            dev_text = f"各大类最大偏离仅 {_short_name(max_cls)} {max_dev:+.1f}%（未达 ±3% 阈值）"
        else:
            dev_text = "各大类偏离度均在安全阈值内"
        lead = "组合处于安全舒适带"
    else:
        dev_text = f"{_short_name(max_cls)} 偏离 {max_dev:+.1f}%，在跟踪观察中"
        lead = "偏离度处于可控范围"

    parts = []
    if bond_text:
        parts.append(bond_text)
    parts.append(dev_text)

    summary_core = "，".join(parts)
    if has_action:
        return f"🛡️ **纪律指引**：{lead}（{summary_core}{vix_text}），按纪律执行再平衡或调仓，锁定利润并控制风险敞口。"
    return f"🛡️ **安心定心丸**：{lead}（{summary_core}{vix_text}），美股核心底仓长期奔跑中，无需任何操作。"


def build_action_thresholds(
    verdict: Optional[Dict[str, Any]] = None,
    portfolio: Optional[List[Dict[str, Any]]] = None,
    vix_val: Optional[Any] = None,
) -> List[str]:
    """生成「核心底线阈值」：量化展示距离下一次动作的触发线与剩余距离。"""
    vix = _extract_vix_val(vix_val)
    thresholds: List[str] = []

    # 1. 再平衡底线（离触发 ±3.0% 最近的大类）
    signals = (verdict.get("signals") if verdict else []) or []
    candidate_cls = None
    candidate_dev = 0.0
    for s in signals:
        cls_name = s.get("asset_class", "")
        dev_str = str(s.get("deviation_pct", "0%")).replace("%", "").replace("+", "")
        try:
            dev = float(dev_str)
            if abs(dev) > abs(candidate_dev):
                candidate_dev = dev
                candidate_cls = cls_name
        except (ValueError, TypeError):
            continue

    if candidate_cls:
        short_c = _short_name(candidate_cls)
        # 目标是 ±3.0% 再平衡线
        if candidate_dev >= 0:
            dist = 3.0 - candidate_dev
            if dist > 0:
                thresholds.append(f"再平衡线：{short_c} 偏离 {candidate_dev:+.1f}%（距 +3.0% 调转线还差 +{dist:.1f}%）")
            else:
                thresholds.append(f"再平衡线：{short_c} 偏离 {candidate_dev:+.1f}%（已触及 ±3.0% 调仓区间）")
        else:
            dist = -3.0 - candidate_dev  # e.g. -3.0 - (-1.8) = -1.2
            if dist < 0:
                thresholds.append(f"再平衡线：{short_c} 偏离 {candidate_dev:+.1f}%（距 -3.0% 补仓线还需偏离 {dist:.1f}%）")
            else:
                thresholds.append(f"再平衡线：{short_c} 偏离 {candidate_dev:+.1f}%（已触及负偏离补仓线）")
    else:
        thresholds.append("再平衡线：各大类距 ±3.0% 触发阈值尚有充裕安全空间")

    # 2. 逢跌低吸线（宽基单日跌幅 >= -2.0% 触发）
    thresholds.append("低吸买点：中证A500 / 沪深300 / 标普 距单日 -2.0% 逢跌买点尚有安全缓冲")

    # 3. 恐慌风控线（VIX >= 25.0 一级防守）
    if vix is not None and vix > 0:
        cushion = 25.0 - vix
        if cushion > 0:
            thresholds.append(f"恐慌风控：VIX={vix:.1f}（距 25.0 恐慌警戒线尚有 {cushion:.1f} 点安全垫）")
        else:
            thresholds.append(f"恐慌风控：VIX={vix:.1f}（已进入警戒区间，执行防御纪律）")
    else:
        thresholds.append("恐慌风控：VIX 处于 25.0 安全警戒线以下")

    # 4. 单只浮盈止盈线（扫描持仓最高浮盈）
    if portfolio:
        best_p = None
        best_rate = -999.0
        for p in portfolio:
            rate = p.get("profit_rate")
            if rate is None:
                cost = float(p.get("cost") or p.get("成本均价") or 0)
                latest = float(p.get("latest_price") or p.get("现价") or 0)
                if cost > 0 and latest > 0:
                    rate = (latest - cost) / cost
            if rate is not None:
                try:
                    r_val = float(rate)
                    r_pct = r_val * 100 if -1.0 < r_val < 2.0 else r_val
                    if r_pct > best_rate:
                        best_rate = r_pct
                        best_p = p
                except (ValueError, TypeError):
                    pass

        if best_p and best_rate > 0:
            p_name = best_p.get("name") or best_p.get("标的名称") or "持仓标的"
            p_name_short = p_name[:16]
            dist_tp = 15.0 - best_rate
            if dist_tp > 0:
                thresholds.append(f"单只止盈：{p_name_short} 浮盈 +{best_rate:.1f}%（距 +15% 止盈警戒线还差 +{dist_tp:.1f}%）")
            else:
                thresholds.append(f"单只止盈：{p_name_short} 浮盈 +{best_rate:.1f}%（已达 +15% 止盈线）")

    return thresholds[:3]


def build_idle_optimization_tips(
    portfolio: Optional[List[Dict[str, Any]]] = None,
    verdict: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """生成「闲暇轻度优化建议」：若今日刚好有空打理，最推荐的 1~2 项微调操作；没空直接忽略。"""
    tips: List[str] = []

    # 1. 扫描是否有观察仓或碎屑仓位需要清理/合并
    if portfolio:
        obs_items = []
        tiny_items = []
        for p in portfolio:
            tags = p.get("tags") or p.get("标签") or []
            if isinstance(tags, str):
                tags = [tags]
            name = p.get("name") or p.get("标的名称") or ""
            shares = float(p.get("shares") or p.get("持仓份额") or 0)
            price = float(p.get("latest_price") or p.get("现价") or p.get("cost") or p.get("成本均价") or 0)
            mv = shares * price

            if any("观察" in str(t) for t in tags):
                obs_items.append((name, mv))
            elif 0 < mv < 500:
                tiny_items.append((name, mv))

        if obs_items:
            target_name, target_mv = obs_items[0]
            mv_str = f"约 ¥{target_mv:,.0f}" if target_mv > 0 else "小额"
            tips.append(
                f"**观察仓瘦身**：标的「{target_name[:16]}」（市值{mv_str}）处于观察期，若今日有空可考虑反弹时择机转换或归并至核心宽基，精简持仓。"
            )
        elif tiny_items:
            target_name, target_mv = tiny_items[0]
            tips.append(
                f"**碎片仓位归并**：标的「{target_name[:16]}」（市值仅约 ¥{target_mv:,.0f}），若有空可并入同类主仓，减少精力消耗。"
            )

    # 2. 增量资金单向打卡（只买不卖，优雅拉平）
    if verdict:
        priority_cls = verdict.get("priority_target")
        priority_dev = verdict.get("priority_deviation", 0)
        if priority_cls and priority_dev < 0:
            dev_pct_str = f"{priority_dev * 100:+.1f}%"
            short_c = _short_name(priority_cls)
            tips.append(
                f"**增量打卡（以买代卖）**：当前最欠配大类为「{short_c}」（偏离 {dev_pct_str}），若手头有闲置零钱，建议定投 ¥100~200 顺手补齐，单向拉平配置。"
            )

    # 3. 纪律兜底备选
    if len(tips) < 2:
        tips.append(
            "**纪律坚守**：专注核心宽基（标普500/纳指100/短债），坚决规避高波动的单一行业概念（如半导体/MLCC细分追高），管住手即是赚钱。"
        )

    return tips[:2]


def build_pyramid_action_card(
    slot: str,
    verdict: Optional[Dict[str, Any]] = None,
    portfolio: Optional[List[Dict[str, Any]]] = None,
    closing_signal: str = "",
    radar_has_reversal: bool = False,
    vix_val: Optional[Any] = None,
    compact: bool = True,
) -> str:
    """组装金字塔顶部动作卡（顶层 3 秒扫视 + 中层闲暇建议）。

    根据时段与信号强度自动适配：
    - 平淡日：展示 核心指令 + 一句话定心丸 + 行动警戒线 + 闲暇轻度建议；
    - 强信号日：展示 紧急动作提示 + 调仓指令，隐藏闲暇建议，促使用户聚焦主指令。
    """
    has_action = bool(closing_signal)
    if verdict and verdict.get("overall_verdict") == "ACT":
        has_action = True

    # ── 1. 核心结论 ──
    if has_action:
        if closing_signal:
            verdict_line = f"🎯 **核心指令**：🟠 出现操作信号 👉 **{closing_signal}**（请看下方具体调仓指引）"
        else:
            verdict_line = "🎯 **核心指令**：🟠 大类偏离触线，建议关注再平衡（详见下方）"
    elif radar_has_reversal:
        verdict_line = "🎯 **核心指令**：🟡 雷达监测到底部企稳信号，供跟踪参考，无强制交易"
    else:
        if slot == "closing":
            verdict_line = "🎯 **核心指令**：🟢 维持观望 · 全天无需操作，安心忙工作"
        elif slot == "morning":
            verdict_line = "🎯 **核心指令**：🟢 保持节奏 · 组合配置健康，正常持有"
        elif slot == "evening":
            verdict_line = "🎯 **核心指令**：🟢 长底仓卧倒 · 享受美股复利，无须熬夜盯盘"
        else:
            verdict_line = "🎯 **核心指令**：🟢 维持现状 · 各项指标处于安全舒适带"

    # ── 2. 一句话定心丸 ──
    reassurance = build_reassurance_sentence(
        verdict=verdict, portfolio=portfolio, vix_val=vix_val, has_action=has_action
    )

    # ── 3. 行动警戒线 ──
    thresholds = build_action_thresholds(verdict=verdict, portfolio=portfolio, vix_val=vix_val)
    thresholds_lines = "\n".join(f"  · {t}" for t in thresholds)
    threshold_block = f"📏 **行动警戒线**：\n{thresholds_lines}"

    # ── 4. 闲暇轻度建议（仅平淡日展示） ──
    idle_block = ""
    if not has_action and not radar_has_reversal:
        idle_tips = build_idle_optimization_tips(portfolio=portfolio, verdict=verdict)
        if idle_tips:
            tips_text = "\n".join(f"  {idx+1}. {t}" for idx, t in enumerate(idle_tips))
            idle_block = f"💡 **闲暇轻度建议**（若今日有空可打理，没空请直接忽略）：\n{tips_text}"

    card_parts = [
        verdict_line,
        reassurance,
        threshold_block,
    ]
    if idle_block:
        card_parts.append(idle_block)

    return "\n\n".join(card_parts)
