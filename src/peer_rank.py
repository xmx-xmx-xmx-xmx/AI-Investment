"""#33 规则 2 落地：主动型基金 vs 同类均值/分位对比（近 1 年口径）。2026-10-01。

规则唯一来源：`docs/ACTION_RULES.md`；数据源验证：`docs/RULE2_SPIKE.md`。

**口径（spike 修订版）**：不是"收益率跑输同类 N pp"（持仓期收益混入了买入
时点效应——买在高点的基金本身可以很优秀），而是 **近 1 年收益在同类中的
分位 ≤30%**（即跑输 70% 以上的同类基金）才提醒。分位天然排除了买入时点。

**数据源**：akshare `fund_open_fund_rank_em(symbol=类别)`，类别子集即"同类"
（债券型/QDII/混合型/股票型四类全体，东财口径）。一次全量抓取 ~3s/类，
经 `net_guard.import_ak()` 超时保护；任何失败 → fail-silent 空串。

**节奏**：周更（仅周六 sat_morning 简报注入），不做每时段——同类分位是
慢变量，日更只会刷屏。
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# 近 1 年分位 ≤ 此值 → 提醒（跑输 70%+ 同类）
WORST_PERCENTILE = 30.0

# akshare 排行接口的类别名 → 只抓用到的四类（混合型备用）
_CATEGORIES = ("债券型", "QDII", "股票型", "混合型")

# 底层指数「无（主动…）」中的关键词 → akshare 类别
_CLASSIFY: list[tuple[str, str]] = [
    ("债", "债券型"),     # 主动债券 / 主动短债 / 主动中短债
    ("QDII", "QDII"),     # 主动QDII（含 ·全球新能源车 等后缀）
    ("股票", "股票型"),   # 主动股票
]


def _field_text(v) -> str:
    """飞书字段归一：数组取首元素，None → ''。"""
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v) if v is not None else ""


def classify_holding(rec: dict) -> str | None:
    """主动型持仓 → akshare 类别名；非主动型 / 无法判断 → None。"""
    idx = _field_text(rec.get("底层指数"))
    if not idx.startswith("无（主动"):
        return None
    for kw, cat in _CLASSIFY:
        if kw in idx:
            return cat
    return "混合型"  # 主动但不含以上关键词


def _pct_num(v) -> float | None:
    """'3.45%' / '3.45' / '-' → float（百分比数值）；无效 → None。"""
    if v is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group()) if m else None


def _fetch_category_frame(ak, category: str):
    """抓一个类别的全量排行；失败返回 None。"""
    try:
        return ak.fund_open_fund_rank_em(symbol=category)
    except Exception as e:  # noqa: BLE001 —— 单类失败不影响其他类
        logger.warning("规则2：抓取 %s 排行失败：%s", category, e)
        return None


def evaluate_peer_rank(holdings: list[dict], frames: dict) -> list[dict]:
    """对主动型持仓算近 1 年同类分位。返回 alerts（建议级）。

    frames: {类别名: DataFrame 或 None}（调用方预取，便于测试注入）。
    """
    alerts: list[dict] = []
    for rec in holdings:
        code = _field_text(rec.get("标的代码")).strip()
        cat = classify_holding(rec)
        if not code or cat is None or cat not in frames:
            continue
        df = frames[cat]
        if df is None:
            continue
        try:
            row = df[df["基金代码"].astype(str).str.zfill(6) == code.zfill(6)]
            if row.empty:
                continue
            r = row.iloc[0]
            name = str(r.get("基金简称", ""))[:20]
            v = _pct_num(r.get("近1年"))
            if v is None:
                continue  # 成立不满 1 年的新基金：无口径，跳过（宁漏勿误）
            series = df["近1年"].map(_pct_num).dropna()
            if series.empty:
                continue
            pctile = (series < v).mean() * 100  # 优于同类 X%
            if pctile <= WORST_PERCENTILE:
                alerts.append({
                    "rule": "R2",
                    "level": "提醒",
                    "text": (
                        f"「{name}」近1年 {v:+.1f}%，只跑赢同类 {pctile:.0f}%"
                        f"——若非买在高点而是基金本身持续落后，考虑换同类更强者"
                    ),
                })
        except Exception as e:  # noqa: BLE001 —— 单只失败不影响其他
            logger.warning("规则2：%s 分位计算失败：%s", code, e)
    return alerts


def render_peer_alerts(alerts: list[dict]) -> str:
    if not alerts:
        return ""
    lines = [f"· {a['text']}" for a in alerts]
    return "📡 **同类对比（近1年）**\n" + "\n".join(lines)


def build_peer_alert(client) -> str:
    """周六简报注入入口。client 为 None（本地）→ 恒空串。fail-silent。"""
    if client is None:
        return ""
    try:
        holdings = client.list_records("底仓表") or []
        # 只有存在主动型持仓时才值得出门抓数据（省 CI 时长）
        cats = {classify_holding(r) for r in holdings} - {None}
        if not cats:
            return ""
        ak = _import_ak_guarded()
        if ak is None:
            return ""
        frames = {c: _fetch_category_frame(ak, c) for c in cats}
        alerts = evaluate_peer_rank(holdings, frames)
        return render_peer_alerts(alerts)
    except Exception as e:  # noqa: BLE001 —— 告警绝不拖垮简报
        logger.warning("规则2执行失败（不阻断推送）：%s", e)
        return ""


def _import_ak_guarded():
    """net_guard 包装的 akshare；未安装返回 None。"""
    try:
        from src.net_guard import import_ak
        return import_ak()
    except ImportError:
        logger.warning("规则2：akshare 未安装，跳过")
        return None
