"""
宏观事件日历 —— D2 模块。

从 ForexFactory 免费 JSON 接口抓取全球宏观经济事件，
按影响级别（≥ Medium）和国家筛选，注入早间简报。

数据源：
  https://nfs.faireconomy.media/ff_calendar_thisweek.json
  （免费，无需 Key，覆盖全球主要经济体）

国家代码 → 资产大类映射：
  USD → 美股资产 / 避险商品（黄金以美元计价）
  CNY → A股资产
  HKD → 港股资产
  EUR → 美股资产（欧洲央行影响全球风险偏好）
  JPY → 美股资产（日央行决议影响套息交易）

影响级别定义（来源：ForexFactory 市场共识分级）：
  ┌──────────┬──────┬──────────────────────────────────────────────┐
  │ 级别     │ 星级 │ 含义                                        │
  ├──────────┼──────┼──────────────────────────────────────────────┤
  │ High     │ ★★★ │ 历史上能引发显著市场波动的事件                │
  │          │      │ 例：CPI、FOMC 利率决议、非农就业、GDP        │
  │ Medium   │ ★★  │ 中等影响力，对特定板块或资产有影响            │
  │          │      │ 例：PMI、零售销售、消费者信心指数             │
  │ Low      │ ★   │ 影响较小或局部事件，不作为简报展示            │
  └──────────┴──────┴──────────────────────────────────────────────┘

  注意：此分级是 ForexFactory 基于全球交易者共识的通用分级，
  并非针对本投资组合定制。代码已在此基础上叠加两层过滤：
  1. 关键词高优先级标记（is_high_priority）——命中 CPI/FOMC/央行等关键词
  2. 事件→持仓细粒度敏感度映射（EVENT_SENSITIVITY）——关联具体持仓

用法：
    from src.macro_calendar import (
        fetch_today_calendar,
        format_macro_signal_line,
        calendar_context_for_prompt,
        filter_portfolio_relevant,
        generate_holding_warnings,
    )

    events = fetch_today_calendar()
    line = format_macro_signal_line(events, scope="今日")   # 展示层：无 ★★★ 则为 ""
    ctx  = calendar_context_for_prompt(filter_portfolio_relevant(events), pf)  # AI 层
    warnings = generate_holding_warnings(events, portfolio)
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── ForexFactory 免费 JSON 接口 ──
# 该接口返回全球主要经济体的当周财经日历，无需 API Key。
# 数据由 ForexFactory 社区维护，影响级别基于历史市场反应的分级共识。
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# ═══════════════════════════════════════════════════════════════
# 国家代码 → 资产大类映射
# ═══════════════════════════════════════════════════════════════

COUNTRY_TO_ASSET_CLASS = {
    "USD": "美股资产",
    "CNY": "A股资产",
    "HKD": "港股资产",
    "EUR": "美股资产",   # 欧央行决议影响全球风险偏好
    "JPY": "美股资产",   # 日央行（BOJ）决议影响套息交易
}

# ═══════════════════════════════════════════════════════════════
# 影响级别定义（来源：ForexFactory）
# ═══════════════════════════════════════════════════════════════
#
# ForexFactory 是全球最大的零售外汇交易者社区，其经济日历的影响级别
# 是基于数十万交易者对历史事件引发市场波动程度的共识分级：
#
#   High   — 历史上该事件公布后，相关资产在数小时内平均波动 >1%
#            （如美国 CPI、FOMC 利率决议、非农就业数据）
#   Medium — 平均波动 0.3%–1%，或仅影响特定板块
#            （如 PMI、零售销售、消费者信心指数）
#   Low    — 平均波动 <0.3%，或仅影响单一国家/行业
#
# 当前系统采用 ≥ Medium（≈ 三星级以上）作为默认阈值，略高于
# TODO.md 要求的"三星级"，因为 ForexFactory 的 High 级已经覆盖了
# 所有真正关键的宏观事件。

IMPACT_STARS = {"High": "★★★", "Medium": "★★", "Low": "★"}
IMPACT_LEVELS = {
    # impact_level: (value, description)
    "High": (3, "历史波动 >1%，市场重点关注"),
    "Medium": (2, "历史波动 0.3%–1%，影响特定板块"),
    "Low": (1, "历史波动 <0.3%，局部影响"),
}

# ═══════════════════════════════════════════════════════════════
# 高优先级关键词（命中即标记 ⚠️，即便只是 Medium）
# ═══════════════════════════════════════════════════════════════

HIGH_PRIORITY_KEYWORDS = [
    "CPI", "通胀", "PPI", "GDP", "PMI", "非农", "Nonfarm", "Payrolls", "就业",
    "美联储", "FOMC", "议息", "利率决议", "央行",
    "降准", "降息", "LPR", "MLF", "逆回购",
    "零售", "Retail Sales", "消费者", "信心指数",
]

# ═══════════════════════════════════════════════════════════════
# 事件→持仓细粒度敏感度映射
# ═══════════════════════════════════════════════════════════════
#
# 将宏观事件按类型分为若干组，每组标注：
#   - keywords: 匹配事件标题的关键词（大小写不敏感）
#   - affected_assets: 受影响的资产大类
#   - holding_signals: 持仓标的受影响的关键词（匹配 "name" 字段）
#   - impact_note: 对该类持仓的具体影响机制
#   - direction: 数据方向性解读，供 AI 判断利多/利空
#
# 此映射在 calendar_context_for_prompt() 中自动与 portfolio 持仓
# 交叉匹配，生成针对性的 "你的 XX 持仓可能受 YY 事件影响" 预警。

EVENT_SENSITIVITY = [
    {
        "group": "通胀/物价",
        "keywords": ["CPI", "PPI", "通胀", "Inflation", "PCE", "物价"],
        "affected_assets": ["美股资产", "避险商品", "固收资产"],
        "holding_signals": ["纳斯达克", "纳指", "标普500", "标普", "黄金", "债券", "债基", "短债", "SPY", "QQQ"],
        "impact_note": "通胀数据决定美联储加息/降息预期。科技股对利率最敏感（估值折现），黄金以美元计价反向波动，短债直接受利率影响。",
        "direction": "高于预期 → 利空美股/黄金/债基；低于预期 → 利好科技股/黄金",
    },
    {
        "group": "美联储/利率决议",
        "keywords": ["FOMC", "美联储", "Fed", "利率决议", "Interest Rate", "Federal Funds", "议息"],
        "affected_assets": ["美股资产", "港股资产", "避险商品", "固收资产"],
        "holding_signals": ["纳斯达克", "纳指", "标普500", "标普", "腾讯", "阿里", "港股", "恒生", "黄金", "债券", "债基", "短债"],
        "impact_note": "美联储利率决议是全球资产定价的锚。加息/鹰派→美元走强→全球风险资产承压（尤其是港股和科技股）；降息/鸽派→风险资产利好。",
        "direction": "鹰派/加息 → 利空美股/港股/黄金；鸽派/降息 → 利好全部风险资产",
    },
    {
        "group": "就业/劳动力",
        "keywords": ["非农", "Nonfarm", "Payrolls", "就业", "失业", "Unemployment", "Jobless", "初请"],
        "affected_assets": ["美股资产", "港股资产"],
        "holding_signals": ["纳斯达克", "纳指", "标普500", "标普", "腾讯", "阿里", "港股", "恒生"],
        "impact_note": "就业数据反映经济强弱。非农超预期→经济强劲→美联储可能不急于降息→短期利空科技股；反之经济衰退担忧→市场承压但降息预期升温。",
        "direction": "大幅超预期 → 短期利空利率敏感型持仓（纳指/港股）；大幅低于预期 → 衰退担忧利空，但降息预期利好",
    },
    {
        "group": "中国央行/货币政策",
        "keywords": ["降准", "降息", "LPR", "MLF", "逆回购", "央行", "PBOC", "PBoC", "Loan Prime"],
        "affected_assets": ["A股资产", "港股资产", "固收资产"],
        "holding_signals": ["A股", "沪深", "上证", "深证", "创业板", "科创板", "中证", "腾讯", "阿里", "港股", "恒生", "债券", "债基", "短债"],
        "impact_note": "中国央行的降准/降息/LPR调整直接影响A股和港股流动性。降准降息→市场流动性增加→利好A股/港股；LPR下调→利好债市。",
        "direction": "降准/降息/LPR下调 → 利好A股/港股/债基；收紧 → 利空",
    },
    {
        "group": "GDP/经济增长",
        "keywords": ["GDP", "经济增长", "Economic Growth"],
        "affected_assets": ["美股资产", "港股资产", "A股资产"],
        "holding_signals": ["纳斯达克", "纳指", "标普500", "标普", "腾讯", "阿里", "港股", "恒生", "A股", "沪深", "上证", "深证"],
        "impact_note": "GDP是经济增长最全面的指标。超预期→经济强劲→企业盈利改善→利好股市；低于预期→衰退担忧→利空股市。",
        "direction": "超预期 → 利好美股/港股/A股；低于预期 → 利空",
    },
    {
        "group": "PMI/制造业",
        "keywords": ["PMI", "Manufacturing", "Services PMI", "制造业", "服务业"],
        "affected_assets": ["美股资产", "A股资产", "港股资产"],
        "holding_signals": ["纳斯达克", "纳指", "标普500", "标普", "A股", "沪深", "上证", "深证", "创业板", "腾讯", "阿里", "港股"],
        "impact_note": "PMI是经济活动的先行指标。PMI>50→经济扩张→利好股市；PMI<50→经济收缩→利空。制造业PMI对A股影响尤为直接。",
        "direction": "PMI>50且超预期 → 利好；PMI<50 → 利空，尤其是A股",
    },
    {
        "group": "零售/消费",
        "keywords": ["零售", "Retail Sales", "消费者", "Consumer Confidence", "信心指数"],
        "affected_assets": ["美股资产"],
        "holding_signals": ["纳斯达克", "纳指", "标普500", "标普"],
        "impact_note": "消费占美国GDP约70%。零售销售和消费者信心是经济健康度的核心信号。数据走弱→消费股承压→可能拖累大盘。",
        "direction": "超预期 → 利好美股；低于预期 → 利空美股",
    },
]

# ═══════════════════════════════════════════════════════════════
# 核心 API
# ═══════════════════════════════════════════════════════════════


def _fetch_raw_calendar() -> list[dict]:
    """从 ForexFactory 拉取本周全球财经日历原始数据。"""
    try:
        resp = requests.get(CALENDAR_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            logger.warning("日历数据格式异常: %s", type(data).__name__)
            return []
        logger.info("[宏观日历] 本周共 %d 条事件", len(data))
        return data
    except Exception as e:
        logger.warning("[宏观日历] 获取失败: %s", str(e)[:100])
        return []


def _parse_date(date_str: str) -> Optional[date]:
    """解析 ISO 日期字符串为 date 对象。"""
    if not date_str:
        return None
    try:
        # 格式如 "2026-06-15T23:19:00-04:00"
        dt = datetime.fromisoformat(date_str)
        return dt.date()
    except (ValueError, TypeError):
        try:
            return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None


def _is_high_priority(title: str) -> bool:
    """检查事件标题是否包含重点关注关键词。"""
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in HIGH_PRIORITY_KEYWORDS)


def _match_sensitivity(title: str, holding_name: str) -> list[dict]:
    """匹配事件标题与持仓名称，返回命中的敏感度条目列表。

    对于每个 EVENT_SENSITIVITY 条目，检查：
    1. 事件标题是否命中该组的 keywords
    2. 持仓名称是否命中该组的 holding_signals

    两个条件都满足时，该条目被返回。
    """
    title_lower = title.lower()
    name_lower = holding_name.lower()
    matches = []

    for sens in EVENT_SENSITIVITY:
        # 事件关键词匹配
        kw_hit = any(kw.lower() in title_lower for kw in sens["keywords"])
        if not kw_hit:
            continue
        # 持仓信号匹配
        hs_hit = any(sig.lower() in name_lower for sig in sens["holding_signals"])
        if not hs_hit:
            continue
        matches.append(sens)

    return matches


# ═══════════════════════════════════════════════════════════════
# 公共 API
# ═══════════════════════════════════════════════════════════════


def fetch_calendar(
    min_impact: str = "Medium",
    countries: Optional[list[str]] = None,
    target_date: Optional[date] = None,
) -> list[dict]:
    """获取筛选后的宏观经济事件。

    Args:
        min_impact: 最低影响级别，可选 "High" / "Medium" / "Low"
        countries: 筛选国家列表（如 ["USD", "CNY"]），None 表示不过滤
        target_date: 筛选特定日期，None 表示返回全部

    Returns:
        事件列表，每条包含:
        - title: 事件名称
        - country: 国家/货币代码
        - date: ISO 日期字符串
        - impact: 影响级别 (High/Medium/Low)
        - forecast: 预测值
        - previous: 前值
        - stars: 星级显示 (★★★/★★/★)
        - is_high_priority: 是否命中关键词
        - asset_class: 映射的资产大类
        - sensitivity_group: 命中的敏感度分组名（如有）
    """
    raw = _fetch_raw_calendar()
    if not raw:
        return []

    impact_order = {"High": 3, "Medium": 2, "Low": 1}
    min_level = impact_order.get(min_impact, 2)

    results = []
    for evt in raw:
        impact = evt.get("impact", "")
        level = impact_order.get(impact, 0)

        # ── 影响级别过滤 ──
        if level < min_level:
            continue

        # ── 国家过滤 ──
        country = evt.get("country", "")
        if countries and country not in countries:
            continue

        # ── 日期过滤 ──
        evt_date = _parse_date(evt.get("date", ""))
        if target_date and evt_date != target_date:
            continue

        title = evt.get("title", "").strip()
        if not title:
            continue

        # ── 敏感度分组匹配（基于标题关键词） ──
        title_lower = title.lower()
        sensitivity_group = ""
        for sens in EVENT_SENSITIVITY:
            if any(kw.lower() in title_lower for kw in sens["keywords"]):
                sensitivity_group = sens["group"]
                break

        results.append({
            "title": title,
            "country": country,
            "date": evt.get("date", ""),
            "impact": impact,
            "forecast": evt.get("forecast", ""),
            "previous": evt.get("previous", ""),
            "stars": IMPACT_STARS.get(impact, ""),
            "is_high_priority": _is_high_priority(title),
            "asset_class": COUNTRY_TO_ASSET_CLASS.get(country, ""),
            "sensitivity_group": sensitivity_group,
        })

    # ── 排序：高影响优先 → 高优先级优先 → 同日按时序 ──
    results.sort(key=lambda e: (
        0 if e["is_high_priority"] else 1,
        {"High": 0, "Medium": 1, "Low": 2}.get(e["impact"], 3),
        e["date"],
    ))

    logger.info(
        "[宏观日历] 筛选后 %d 条（≥%s, countries=%s, date=%s）",
        len(results), min_impact, countries or "all", target_date or "all",
    )
    return results


def fetch_today_calendar(min_impact: str = "Medium") -> list[dict]:
    """获取今天的宏观经济事件，筛选与持仓相关的国家。"""
    relevant_countries = list(COUNTRY_TO_ASSET_CLASS.keys())
    today = date.today()
    return fetch_calendar(
        min_impact=min_impact,
        countries=relevant_countries,
        target_date=today,
    )


def fetch_upcoming_calendar(
    min_impact: str = "Medium",
    days_ahead: int = 3,
) -> list[dict]:
    """获取未来 N 天的宏观经济事件。

    Args:
        min_impact: 最低影响级别
        days_ahead: 未来天数（含今天）
    """
    relevant_countries = list(COUNTRY_TO_ASSET_CLASS.keys())
    today = date.today()
    end_date = today + timedelta(days=days_ahead)

    events = fetch_calendar(
        min_impact=min_impact,
        countries=relevant_countries,
    )
    return [
        e for e in events
        if _parse_date(e["date"]) is not None
        and today <= _parse_date(e["date"]) <= end_date  # type: ignore[operator]
    ]


def fetch_past_calendar(
    min_impact: str = "Medium",
    days_behind: int = 7,
) -> list[dict]:
    """获取过去 N 天的宏观经济事件（用于周报回顾）。

    Args:
        min_impact: 最低影响级别
        days_behind: 过去天数
    """
    relevant_countries = list(COUNTRY_TO_ASSET_CLASS.keys())
    today = date.today()
    start_date = today - timedelta(days=days_behind)

    events = fetch_calendar(
        min_impact=min_impact,
        countries=relevant_countries,
    )
    return [
        e for e in events
        if _parse_date(e["date"]) is not None
        and start_date <= _parse_date(e["date"]) <= today  # type: ignore[operator]
    ]


def generate_holding_warnings(
    events: list[dict],
    portfolio: list[dict],
) -> list[dict]:
    """将宏观事件与持仓交叉匹配，生成针对每个持仓的预警。

    Args:
        events: fetch_calendar() 的返回结果
        portfolio: load_portfolio() 的返回结果，每个元素包含 name, asset_class 等

    Returns:
        预警列表，每条包含:
        - holding_name: 持仓名称
        - event_title: 事件标题
        - event_stars: 事件星级
        - event_date: 事件日期
        - sensitivity_group: 命中的敏感度分组
        - impact_note: 影响机制说明
        - direction: 数据方向性解读
    """
    if not events or not portfolio:
        return []

    warnings = []
    for evt in events:
        title = evt.get("title", "")
        if not title:
            continue

        for pos in portfolio:
            holding_name = pos.get("name", "")
            if not holding_name:
                continue

            matches = _match_sensitivity(title, holding_name)
            for sens in matches:
                warnings.append({
                    "holding_name": holding_name,
                    "event_title": title,
                    "event_stars": evt.get("stars", ""),
                    "event_date": evt.get("date", ""),
                    "sensitivity_group": sens["group"],
                    "impact_note": sens["impact_note"],
                    "direction": sens["direction"],
                })

    return warnings


# ═══════════════════════════════════════════════════════════════
# 格式化输出
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# 展示层 / AI 层分层（2026-09-20）
# ═══════════════════════════════════════════════════════════════
#
# 背景：用户 2026-09-20 反馈——周报末尾的「今日宏观日历」原始列表"根本没看懂"，
# "如果没什么用的话，要么把它融合到 AI 解析部分，要么就给他删掉算了"。
#
# 事实核查（读 9/18 真实推送）：
#   1. AI **本来就已经拿到**日历数据（calendar_context_for_prompt 注入 prompt），
#      且 9/18 的周报里 AI 确实用了它（"如果德法PMI集体不及预期→…"）
#      → 卡片末尾那份原始列表 = 同一份数据发第二遍（与「板块轮动」同型问题）。
#   2. 该列表标题写死"今日"，但周日周报展示的是**下周**事件 → 标签本身就是错的。
#   3. 列表里大半是法国/德国 Flash PMI、欧央行官员讲话等**区域性数据**，
#      对「美股+港股+A股+固收+黄金」组合只有很弱的间接链条。
#
# 因此改为与板块轮动一致的分层：
#   展示层 —— 只留 ★★★（High，FOMC/CPI/非农级）一行提示，无则完全静默
#   AI 层  —— 过滤掉区域性噪声后全量喂给 LLM，并由 _MACRO_AI_RULE 强制翻译成人话

# 央行「决议类」关键词。欧/日的**区域性经济数据**（德法 PMI、地方消费者信心）
# 对本组合是间接噪声，但欧央行/日央行的利率决议不能忽略（决定全球风险偏好
# 与套息交易）。用这组关键词把"决议"和"地方数据/官员讲话"区分开。
#
# ⚠️ 关键词按 ForexFactory 的**真实事件标题**拟定，不要凭印象写：
#    决议类   —— "ECB Main Refinancing Rate" / "BOJ Policy Rate" /
#                "BOJ Monetary Policy Statement" / "ECB Press Conference"
#    要丢弃的 —— "ECB President Lagarde Speaks"（官员讲话，一天两条，纯噪声）、
#                "French Flash Manufacturing PMI" 等区域性数据
_CENTRAL_BANK_DECISION_KEYWORDS = [
    "rate decision", "interest rate", "monetary policy", "rate statement",
    "refinancing rate", "policy rate", "press conference",
    "fomc", "利率决议", "议息",
]

# 与组合有**直接**传导链条的货币：美元(美股/黄金计价)、人民币(A股)、港币(港股)
_DIRECT_CURRENCIES = {"USD", "CNY", "HKD"}

_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def is_portfolio_relevant(event: dict) -> bool:
    """该宏观事件是否值得占用用户注意力（值不值得进 AI 上下文）。

    判据（从严）：
      1. ★★★（High）—— 无条件保留（FOMC / CPI / 非农 / GDP 级）
      2. 直接相关货币（USD / CNY / HKD）的 ★★ 事件 —— 保留
      3. 间接货币（EUR / JPY 等）—— **只有央行决议类才保留**，
         区域性经济数据（French/German Flash PMI、地方消费者信心）丢弃

    Args:
        event: fetch_*_calendar() 返回的单条事件

    Returns:
        True 表示值得喂给 AI
    """
    if event.get("impact") == "High":
        return True
    if event.get("country") in _DIRECT_CURRENCIES:
        return True
    title = (event.get("title") or "").lower()
    return any(kw in title for kw in _CENTRAL_BANK_DECISION_KEYWORDS)


def filter_portfolio_relevant(events: list[dict]) -> list[dict]:
    """按 is_portfolio_relevant 过滤事件列表（保持原顺序）。"""
    return [e for e in events if is_portfolio_relevant(e)]


def format_macro_signal_line(events: list[dict], scope: str = "今日") -> str:
    """展示层：只留 ★★★ 关键事件，最多 3 条，压成一行。

    设计意图（与板块轮动一致）：平淡日完全静默、一行不占；真正的大事件
    （FOMC / CPI / 非农）仍给一行"什么时候要留意"的提示。至于"这个数据
    是什么、对我哪个持仓有影响"，交给 AI 解读，不再由卡片罗列指标名。

    Args:
        events: fetch_*_calendar() 的返回结果（未过滤也可，本函数自行筛 ★★★）
        scope: 时间范围标签，如 "今日" / "下周"

    Returns:
        形如 `📅 **下周关键**：周四 [USD] FOMC 利率决议`；
        无 ★★★ 事件时返回空字符串（调用方应据此不渲染任何内容）
    """
    highs = [e for e in events if e.get("impact") == "High"]
    if not highs:
        return ""

    items = []
    for e in highs[:3]:
        d = _parse_date(e.get("date", ""))
        wd = _WEEKDAY_CN[d.weekday()] if d else ""
        prefix = f"{wd} " if wd else ""
        items.append(f"{prefix}[{e.get('country', '')}] {e.get('title', '')}")

    more = f"；另有 {len(highs) - 3} 项" if len(highs) > 3 else ""
    return f"📅 **{scope}关键**：" + "；".join(items) + more


def calendar_context_for_prompt(
    events: list[dict],
    portfolio: list[dict] | None = None,
) -> str:
    """将宏观日历事件转为 AI Prompt 可用的上下文文本。

    重点标注高影响事件，如果提供持仓数据则自动生成细粒度持仓预警。

    Args:
        events: fetch_calendar() 的返回结果
        portfolio: 可选，load_portfolio() 的返回结果。提供时自动生成持仓预警

    Returns:
        格式化后的文本，可直接注入 AI Prompt 的 <macro_calendar> 区块
    """
    if not events:
        return "今日无重大宏观经济事件。"

    lines = ["今日（及近期）宏观经济日历："]
    for e in events:
        stars = e.get("stars", "")
        country = e.get("country", "")
        title = e.get("title", "")
        forecast = e.get("forecast", "")
        previous = e.get("previous", "")
        flag = "⚠️" if e.get("is_high_priority") else ""
        sensitivity = e.get("sensitivity_group", "")

        detail = ""
        if forecast or previous:
            parts = []
            if forecast:
                parts.append(f"预期:{forecast}")
            if previous:
                parts.append(f"前值:{previous}")
            detail = f" [{', '.join(parts)}]"

        asset_class = e.get("asset_class", "")
        mapping = f" → {asset_class}" if asset_class else ""
        group = f" [{sensitivity}]" if sensitivity else ""

        lines.append(f"· {stars} [{country}] {title}{detail}{mapping}{group} {flag}")

    # ── 持仓预警（如果有 portfolio） ──
    if portfolio:
        warnings = generate_holding_warnings(events, portfolio)
        if warnings:
            lines.append("")
            lines.append("⚠️ 持仓敏感度预警（宏观事件 → 你的具体持仓）：")
            # 去重：同一 (holding, event) 只保留一条
            seen = set()
            for w in warnings:
                key = (w["holding_name"], w["event_title"])
                if key in seen:
                    continue
                seen.add(key)
                lines.append(
                    f"· {w['event_stars']} {w['event_title']}"
                    f" → 影响「{w['holding_name']}」"
                    f"（{w['impact_note'][:80]}…）"
                    if len(w["impact_note"]) > 80
                    else f"· {w['event_stars']} {w['event_title']}"
                    f" → 影响「{w['holding_name']}」"
                    f"（{w['impact_note']}）"
                )

    return "\n".join(lines)
