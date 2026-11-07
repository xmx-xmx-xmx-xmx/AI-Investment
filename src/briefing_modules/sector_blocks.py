"""Sector rotation blocks and AI prompt rule injection."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ── 板块轮动（2026-09-20 改造：揉进 AI 汇总，仅强信号留一行展示）──
# 温差定义：行业涨跌幅 − 同期大盘涨跌幅（相对强弱，不是绝对涨跌）。
# 阈值与 market_data.fetch_sector_deltas 的 signal 判定保持一致（±2pct）。

_SECTOR_STRONG_PCT = 2.0          # 强信号阈值，仅用于文档与自检断言
_SECTOR_AI_MAX_ITEMS = 8          # 喂给 LLM 的板块条数上限（控 prompt 长度）


def _fetch_sector_safe(market_filter: str = "all") -> list[dict]:
    """抓取板块温差数据，失败/未配置一律返回 []（简报绝不因此中断）。

    Args:
        market_filter: "all" 全部 / "us" 仅美股 / "hk_cn" 仅港股+A股
                       （下划线分隔的多市场并集，"hk" / "cn" 单独亦可）
    """
    from src import briefing
    md = getattr(briefing, "market_data", None)
    if md is None:
        from src import market_data as md

    try:
        deltas = md.fetch_sector_deltas()
    except Exception as e:
        logger.debug("[板块] 温差抓取异常: %s", str(e)[:80])
        return []

    if not deltas:
        return []

    if market_filter != "all":
        wanted = {m for m in market_filter.split("_") if m}
        deltas = [d for d in deltas if d.get("market") in wanted]

    return deltas


def _build_sector_signal_line(deltas: list[dict]) -> str:
    """展示层：仅当出现强信号时返回一行提示，否则空串（平淡日完全折叠）。"""
    strong = [d for d in deltas if d.get("signal")]
    if not strong:
        return ""
    strong.sort(key=lambda d: -abs(d.get("delta") or 0))

    items = []
    for d in strong[:3]:
        arrow = "🔺" if d["delta"] > 0 else "🔻"
        items.append(f"{d['label']} {d['sector_pct']:+.1f}%（温差{arrow}{d['delta']:+.1f}pct）")
    tail = f"　等 {len(strong)} 项异动" if len(strong) > 3 else ""
    return "🔄 **板块异动**：" + "　|　".join(items) + tail


def build_sector_for_ai(deltas: list[dict], max_items: int = _SECTOR_AI_MAX_ITEMS) -> str:
    """AI 层：全量温差文本（强信号排前），供 LLM 解析资金流向。

    与展示层刻意的分层：展示要短（一眼扫过），喂 LLM 要全（有判断空间）。
    fast_mode 时段（closing）传更小的 max_items 以控制 prompt 长度。
    """
    if not deltas:
        return ""

    ordered = sorted(deltas, key=lambda d: (not d.get("signal"), -abs(d.get("delta") or 0)))
    parts = []
    for d in ordered[:max_items]:
        sig = f" {d['signal']}" if d.get("signal") else ""
        parts.append(
            f"{d['label']}[{d.get('market', '')}] 行业{d['sector_pct']:+.1f}% / "
            f"基准{d['benchmark_pct']:+.1f}% / 温差{d['delta']:+.1f}pct{sig}"
        )
    return "[板块温差] " + "；".join(parts)


def _build_sector_parts(market_filter: str = "all",
                        max_ai_items: int = _SECTOR_AI_MAX_ITEMS) -> tuple[str, str]:
    """一次抓取 → (展示行, AI 文本)。同一时段只请求一次网络，避免重复限速等待。"""
    deltas = _fetch_sector_safe(market_filter)
    return _build_sector_signal_line(deltas), build_sector_for_ai(deltas, max_ai_items)


# 喂给 LLM 的板块解析要求。
_SECTOR_AI_RULE = (
    "【板块温差的读法】温差 = 行业涨跌幅 − 同期大盘涨跌幅，衡量的是"
    "资金在行业之间的**相对流向**，不是绝对涨跌——行业在涨但跑输大盘，同样说明资金在离开它。"
    "你必须用大白话把这件事落到持仓上：① 钱正在往哪个方向挪"
    "（科技进攻 / 防御 / 周期 / 避险）；② 这对你哪一个持仓大类意味着什么。"
    "不要把数字念一遍就完事。"
    "若没有温差 ≥2pct 的强信号，就用一句话说「板块间无明显轮动，维持均衡」，不要硬编故事。"
)


# 喂给 LLM 的宏观日历解析要求（2026-09-20）。
_MACRO_AI_RULE = (
    "【宏观日历的用法】只挑**对你持仓有实质影响**的 1-2 件展开，其余一律不写。"
    "每写一件必须包含三要素：① 这是什么（一句话大白话，例："
    "「德法PMI = 欧洲制造业景气度」）；② 数据往哪个方向走会怎样；"
    "③ 对你哪一个持仓大类意味着什么。"
    "禁止只罗列指标名（如「关注德法PMI、失业金、消费者信心」）；"
    "禁止出现未经翻译的英文指标名；"
    "禁止把★级/预期值/前值照抄进正文。"
    "若没有值得展开的事件，直接说「本周无关键宏观事件」并转向持仓纪律，不要硬凑。"
)
