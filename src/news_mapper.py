"""
规则 6：资讯命中重仓主题 —— 新闻 → 主题 → 受影响持仓 映射（docs/ACTION_RULES.md 规则表 #6）。

纯本地关键词匹配：无 LLM、无额外网络调用；fail-silent（任何异常不阻断简报）。

设计原则（与 briefing 展示层/AI 层分离同源，briefing.py §展示层）：
- 映射吃**全量**快讯（articles），不是展示层精选（filtered）——展示要短，判定要全。
- 主题级聚合：同主题 N 条命中合并成**一行**，防刷屏；每卡最多 3 行。
- 行内给"占组合 X%"：用户一眼看出这条新闻的敞口量级，决定要不要往上看。

主题表 THEMES 是本模块唯一需要日常维护的东西：
  news_kw    → 新闻侧关键词（title+snippet 子串匹配，"AI" 用独立正则防误伤）
  holding_kw → 持仓侧关键词（在「底层指数」+「标的名称」中匹配）
  asset_class→ 可选大类约束（None = 不限；如债市主题限定固收资产）

用法（briefing 各时段 builder 内，news_block 构建处就近调用）：
    news_link_block = build_news_links_block(articles)   # 空串 = 无命中/无客户端
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 主题映射表（唯一维护点；顺序即输出优先级备选，实际按命中数+市值排序）
# ═══════════════════════════════════════════════════════════════

# "AI" 独立词（前后不能是英文字母，防 OPENAI/said 误伤；中文紧贴如 "AI芯片" 可命中）
_AI_RE = re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])")

THEMES: list[dict] = [
    {
        "name": "美联储/利率",
        "news_kw": ["美联储", "FOMC", "鲍威尔", "降息", "加息", "利率决议", "点阵图",
                    "联邦基金", "美债收益率", "国债收益率", "非农"],
        "holding_kw": ["纳斯达克", "标普"],
        "asset_class": "美股资产",
    },
    {
        "name": "AI/半导体",
        "news_kw": ["英伟达", "半导体", "芯片", "台积电", "光刻", "算力", "OpenAI"],
        # "AI" 独立词不进子串表（防 OPENAI/said 误伤），用正则匹配
        "news_re": _AI_RE,
        "holding_kw": ["纳斯达克科技", "半导体"],
        "asset_class": None,
    },
    {
        "name": "港股互联网",
        "news_kw": ["港股", "恒生", "腾讯", "阿里", "美团", "平台经济", "南向资金",
                    "港交所", "中概"],
        "holding_kw": ["互联网"],
        "asset_class": None,
    },
    {
        "name": "新能源车",
        "news_kw": ["新能源车", "电动汽车", "锂电", "比亚迪", "特斯拉", "宁德时代",
                    "车企", "汽车"],
        "holding_kw": ["新能源车"],
        "asset_class": None,
    },
    {
        "name": "黄金",
        "news_kw": ["黄金", "金价", "贵金属", "COMEX", "伦敦金", "上海金"],
        "holding_kw": ["上海金", "黄金"],
        "asset_class": None,
    },
    {
        "name": "红利/高股息",
        "news_kw": ["红利", "高股息", "股息率", "分红"],
        "holding_kw": ["红利"],
        "asset_class": None,
    },
    {
        "name": "新兴市场",
        "news_kw": ["新兴市场", "印度", "东南亚", "越南", "韩国", "KOSPI"],
        "holding_kw": ["新兴市场", "KOSPI"],
        "asset_class": None,
    },
    {
        "name": "债市",
        "news_kw": ["债券", "国债", "信用债", "城投", "理财", "LPR", "逆回购",
                    "MLF", "存款利率", "央行"],
        "holding_kw": [],  # 债市按大类整桶匹配，不按指数
        "asset_class": "固收资产",
    },
]


# ═══════════════════════════════════════════════════════════════
# 匹配逻辑
# ═══════════════════════════════════════════════════════════════

def _field_text(value) -> str:
    """飞书字段归一（数组/标量统一转 str）—— 与 rules_engine 同款。"""
    if isinstance(value, list):
        value = "".join(str(x) for x in value if x is not None)
    return str(value or "").strip()


def _text_of(item: dict) -> str:
    """新闻条目可匹配文本：标题 + 摘要。"""
    return f"{item.get('title', '')} {item.get('snippet', '')}"


def _hit_news(theme: dict, text: str) -> bool:
    """新闻是否命中主题：关键词子串 + 可选正则。"""
    if any(kw in text for kw in theme["news_kw"]):
        return True
    rex = theme.get("news_re")
    return bool(rex and rex.search(text))


def _hit_holding(theme: dict, rec: dict) -> bool:
    """持仓是否属于主题：大类约束（若有）与 指数/名称关键词（若有）取交集。"""
    if theme["asset_class"] and theme["asset_class"] not in _field_text(rec.get("资产大类")):
        return False
    kws = theme["holding_kw"]
    if not kws:
        return bool(theme["asset_class"])  # 无关键词 → 大类整桶匹配（债市）
    blob = f"{_field_text(rec.get('底层指数'))} {_field_text(rec.get('标的名称'))}"
    return any(kw in blob for kw in kws)


def _market_value(rec: dict) -> float:
    """市值（公式字段），缺失用 现价×份额 兜底 —— 与 rules_engine 同款。"""
    try:
        mv = float(rec.get("市值") or 0)
    except (TypeError, ValueError):
        mv = 0.0
    if mv > 0:
        return mv
    try:
        return float(rec.get("现价") or 0) * float(rec.get("持仓份额") or 0)
    except (TypeError, ValueError):
        return 0.0


def map_news_to_holdings(news_items: list[dict], holdings: list[dict]) -> list[dict]:
    """
    核心：全量快讯 × 底仓 → 主题级命中结果。

    Returns:
        [{name, hits, holding_names, mv, mv_pct}, ...] 按命中数→市值排序，最多 3 个主题。
        mv_pct 为该主题波及持仓市值占组合比例（0~1）。
    """
    if not news_items or not holdings:
        return []

    total_mv = sum(_market_value(h) for h in holdings)
    results = []
    for theme in THEMES:
        hits = [it for it in news_items if _hit_news(theme, _text_of(it))]
        if not hits:
            continue
        affected = [h for h in holdings if _hit_holding(theme, h)]
        if not affected:
            continue
        mv = sum(_market_value(h) for h in affected)
        results.append({
            "name": theme["name"],
            "hits": len(hits),
            "holding_names": [_field_text(h.get("标的名称")) for h in affected],
            "mv": mv,
            "mv_pct": (mv / total_mv) if total_mv > 0 else 0.0,
        })

    results.sort(key=lambda r: (r["hits"], r["mv"]), reverse=True)
    return results[:3]


def render_news_links(results: list[dict]) -> str:
    """渲染成卡片块（无命中返回空串）。每主题一行，短句。"""
    if not results:
        return ""
    lines = ["📡 **资讯·持仓关联**"]
    for r in results:
        n = len(r["holding_names"])
        lines.append(
            f"· {r['name']} ×{r['hits']} → 波及持仓 {n} 只（占组合 {r['mv_pct']:.1%}）"
        )
    return "\n".join(lines)


def build_news_links_block(news_items: list[dict]) -> str:
    """简报 builder 调用入口。本地（无客户端）/任何异常 → 空串，绝不阻断推送。"""
    if not news_items:
        return ""
    try:
        from src.feishu_client import get_feishu_client_or_none
        client = get_feishu_client_or_none()
        if client is None:
            return ""
        holdings = client.list_records("底仓表") or []
        return render_news_links(map_news_to_holdings(news_items, holdings))
    except Exception as e:  # noqa: BLE001 —— 规则 6 绝不拖垮简报
        logger.warning("资讯·持仓关联生成失败（不阻断推送）：%s", e)
        return ""
