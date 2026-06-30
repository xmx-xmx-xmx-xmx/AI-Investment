"""
多时段简报系统 —— P1 核心模块。

六个时段，按市场日历智能熔断：
  morning     08:30 美股收盘复盘 + AI 解读
  midday      12:00 亚太午盘收盘快讯（A股/港股/日韩台）
  closing     14:30 A 股收盘前 30 分钟策略防御带
  evening     20:30 夜盘前瞻 + 恒指最终收盘 + AI 解读
  sat_morning 周六 美股周五收盘复盘
  sun_evening 周日 周末宏观总结 + 周一前瞻

全部资讯来自免费源（金十数据 + 华尔街见闻），零成本。

用法：
  python -m src.briefing morning
  python -m src.briefing closing
"""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime, timezone, timedelta

from src.holiday_gate import is_cn_market_open, is_us_market_open
from src.news_fetcher import fetch_all_news, _filter_by_keywords, _clean_html
from src.advisor import load_portfolio, calculate_rebalance
from src.notify import FeishuPusher
from src import market_data
from src.macro_calendar import (
    fetch_today_calendar,
    format_calendar_for_brief,
    calendar_context_for_prompt,
)

logger = logging.getLogger(__name__)
tz_cn = timezone(timedelta(hours=8))


# ═══════════════════════════════════════════════════════════════
# 通用工具
# ═══════════════════════════════════════════════════════════════

def _push(title: str, content: str) -> bool:
    pusher = FeishuPusher()
    if not pusher.is_configured():
        logger.warning("Webhook 未配置，只打印")
        print(f"\n═══ {title} ═══\n{content}")
        return False
    return pusher.send_card(title, content)


def _fmt_news(news_list: list[dict], max_items: int = 8) -> str:
    """格式化新闻列表。短标题保留全文，长标题在词边界截断。"""
    lines = []
    for a in news_list[:max_items]:
        title = _clean_html(a.get("title", ""))
        if not title:
            continue
        if len(title) <= 120:
            display = title   # 短标题不截断
        else:
            display = title[:117] + "…"
        source = a.get("source", "")
        lines.append(f"· {display}  _{source}_")
    return "\n".join(lines) if lines else "（暂无）"


def _sent_truncate(text: str, max_chars: int = 150) -> str:
    """在第一句号处或 max_chars 词边界处截断。"""
    if len(text) <= max_chars:
        return text
    # 找第一个句号
    dot = text[:max_chars].rfind("。")
    if dot > max_chars // 2:
        return text[:dot + 1]
    # 回退到词边界
    cut = text[:max_chars].rstrip()
    last_space = cut.rfind(" ")
    if last_space > max_chars // 2:
        return cut[:last_space] + "…"
    return cut[:max_chars - 3] + "…"


def _should_skip(requires: list[str]) -> str | None:
    """检查是否因节假日闭市需要熔断。

    Returns:
        错误消息字符串（需要推送的），或 None（可以继续）
    """
    for market in requires:
        if market == "cn" and not is_cn_market_open():
            return None  # A 股闭市，静默跳过
        if market == "us" and not is_us_market_open():
            return "美股今日休市，系统暂停晚间简报。明天再见 👋"
    return None


def _build_portfolio_summary() -> str:
    try:
        pf = load_portfolio()
        rb = calculate_rebalance(pf)
    except Exception:
        return "持仓数据暂不可用"
    lines = [f"总市值 ¥{rb['total_value']:,.0f}"]
    for d in rb["deviation_report"]:
        lines.append(
            f"{d['asset_class']}：实占 {d['actual_weight_pct']}（目标 {d['target_weight_pct']}），"
            f"偏离 {d['deviation_pct']}"
        )
    return "\n".join(lines)


def _trading_label() -> str:
    """动态交易日标签：星期一、节后首日 → '上一交易日'，否则 → '今日'。"""
    now = datetime.now(tz_cn)
    return "上一交易日" if now.weekday() == 0 else "今日"


# _build_global_market_snapshot v2 below (line ~490) — v1 deleted
def _build_vix_block() -> str:
    """独立 VIX 恐慌指数区块。"""
    try:
        vix = market_data.fetch_vix()
        if vix and vix.get("vix"):
            return f"😨 **VIX 恐慌指数**: {vix['vix']:.1f}（{vix['level']}）"
    except Exception:
        pass
    return ""


def _is_fund_pos(pos: dict) -> bool:
    """判断持仓是否为场外基金（非场内ETF/股票）。"""
    code = pos.get("code", "")
    if not code:
        return False
    # 场外基金：6位数字 + 非ETF前缀
    if code.isdigit() and len(code) == 6:
        if not code.startswith(("51", "56", "58", "159", "16")):
            return True
    return False


def _portfolio_value_summary(label: str = "auto") -> str:
    """生成持仓市值+收益率一览表。

    Args:
        label: "auto" → 根据当前时间自动选 "今日"/"昨日"；"today" → 强制今日；"yesterday" → 强制昨日
    """
    try:
        pf = load_portfolio()
        rb = calculate_rebalance(pf)
    except Exception:
        return "持仓数据暂不可用"

    # 自动判断：<12:00 用昨日 | ≥12:00且<20:00 用午盘 | ≥20:00 用今日
    if label == "auto":
        now = datetime.now(tz_cn)
        if now.hour < 12:
            label = "yesterday"
        elif now.hour >= 20:
            label = "today"
        else:
            label = "midday"

    lines = ["**💰 当前持仓**"]
    for pos in rb["positions"]:
        pnl = pos["pnl_pct"]
        pnl_arrow = "🔺" if pnl > 0 else "🔻" if pnl < 0 else "➖"
        daily = pos.get("daily_change_pct", 0)
        daily_arrow = "🔺" if daily > 0 else "🔻" if daily < 0 else "➖"

        if label == "yesterday":
            daily_str = f"昨日{daily_arrow}{daily:+.2f}%" if daily != 0 else "暂无"
        elif label == "midday":
            if _is_fund_pos(pos):
                daily_str = f"昨日{daily_arrow}{daily:+.2f}%" if daily != 0 else "暂无"
            else:
                daily_str = f"午盘{daily_arrow}{daily:+.2f}%" if daily != 0 else "暂无"
        else:
            daily_str = f"今日{daily_arrow}{daily:+.2f}%" if daily != 0 else "暂无"

        lines.append(
            f"· {pos['name']}: ¥{pos['market_value']:,.0f}　{daily_str}　持仓{pnl_arrow}{pnl:+.1f}%"
        )
    return "\n".join(lines)


def _ai_insight(context: str, news_titles: str, max_tokens: int = 400,
                macro_context: str = "") -> str:
    """LLM 生成持仓+新闻解读（可结合宏观日历）。D9 重构：引入投资宪法+思维链。"""
    if not news_titles.strip():
        return ""

    pf_summary = _build_portfolio_summary()

    # 拼接市场行情数据（用于 CoT 交叉验证）
    market_text = news_titles[:1000]
    if macro_context:
        market_text = f"宏观日历:\n{macro_context[:500]}\n\n新闻:\n{market_text}"

    extra_rules = (
        "- 只看新闻标题，推测对持仓大类可能的影响\n"
        "- 如果某条新闻明显利好或利空某类资产，直接说\"这对你的XX持仓是机会/风险，因为...\"\n"
        "- 用大白话写，禁止术语。150-250 字\n"
        "- 如果当日有宏观经济日历事件，必须结合该事件分析对持仓的短期影响，标注⚠️波动预警\n"
        "- 如果新闻自相矛盾，指出矛盾并建议\"以不变应万变，按纪律执行\"\n"
        "- 直接输出正文，不要前缀"
    )

    from src.prompt_templates import build_analysis_prompt
    prompt = build_analysis_prompt(
        role=f"你是量化投资顾问。任务：把当天的财经新闻与真实持仓对照，给出有洞察力的解读。当前语境：{context}",
        holdings_text=pf_summary,
        market_text=market_text,
        extra_rules=extra_rules,
    )

    try:
        from src.llm import get_llm_client, get_llm_model
        client = get_llm_client()
        if client is None:
            return ""
        resp = client.chat.completions.create(
            model=get_llm_model(), max_tokens=max_tokens, temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("AI 解读生成失败: %s", str(e)[:100])
        return ""


def _skip_msg(reason: str, slot_name: str) -> str | None:
    """如果闭市，返回一条轻量提示卡片。返回 None 表示不发任何推送。"""
    if reason:
        _push(f"{slot_name} — 休市", reason)
    return reason


# ═══════════════════════════════════════════════════════════════
# 六个时段
# ═══════════════════════════════════════════════════════════════

def _build_morning() -> str:
    """08:30 早间简报 + AI 综合解读（含宏观日历）。"""
    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    articles = fetch_all_news(max_results=50)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=8)
    news_block = _fmt_news(filtered, max_items=8)
    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:8])

    # ── 1. VIX ──
    vix_block = _build_vix_block()
    vix_line = "\n" + vix_block + "\n" if vix_block else ""

    # ── 2. 全球市场 ──
    market_context = _build_global_market_snapshot()
    market_block = "\n" + market_context + "\n" if market_context else ""

    # ── 3. 昨日财报 ──
    earnings_block = ""
    yday = []
    try:
        from src.earnings_calendar import fetch_yesterdays_earnings, format_yesterdays_earnings
        yday = fetch_yesterdays_earnings()
        if yday:
            earnings_block = "\n" + format_yesterdays_earnings(yday) + "\n"
    except Exception:
        pass

    # ── 3. 宏观日历 ──
    macro_events = fetch_today_calendar(min_impact="Medium")
    macro_display = format_calendar_for_brief(macro_events)
    macro_prompt = calendar_context_for_prompt(macro_events, pf)
    macro_block = "\n" + macro_display + "\n" if macro_display else ""

    # ── 4. 持仓 ──
    value_summary = _portfolio_value_summary()

    # ── 5. 雷达 ──
    radar_block = ""
    try:
        from src.radar import scan_radar, build_radar_brief, _radar_insight
        radar_result = scan_radar(dry_run=False)
        if radar_result["signal_items"]:
            radar_raw = build_radar_brief(radar_result["signal_items"])
            radar_ai = _radar_insight(radar_result["signal_items"], titles_only, macro_prompt)
            radar_ai_block = "\n" + radar_ai + "\n" if radar_ai else ""
            radar_block = "\n" + radar_raw + radar_ai_block if radar_raw else ""
    except Exception:
        pass

    # ── 6. 国际快讯（已关联持仓）──
    global_block = ""
    try:
        from src.global_news import _build_global_news_brief
        global_news_block = _build_global_news_brief()
        global_block = "\n" + global_news_block + "\n" if global_news_block else ""
    except Exception:
        pass

    # ── 7. AI 综合解读（所有数据就绪后，一次调用）──
    earnings_titles = " ".join(f"{e['ticker']} {e.get('name','')}" for e in yday[:5]) if yday else ""
    radar_snippet = radar_block[:300] if radar_block else ""
    global_snippet = global_block[:300] if global_block else ""
    pf_summary = "\n".join(f"{p.get('name','')[:12]} {p.get('asset_class','')}" for p in pf[:10]) if pf else ""
    full_context = f"{titles_only} {earnings_titles} {market_context[:300]} {pf_summary} {radar_snippet} {global_snippet}"

    insight = _ai_insight(
        "早间简报——请综合所有信息（隔夜新闻/昨日财报/全球市场/持仓/宏观日历/雷达信号/国际快讯），"
        "给出一段对今天持仓的综合解读，必须提及对具体持仓大类的影响",
        full_context, macro_context=macro_prompt
    )
    insight_block = "\n🧠 **AI 综合解读**\n" + insight + "\n" if insight else ""

    # ── 8. 今日重点关注 ──
    focus = _sent_truncate(
        _ai_insight("早间——请给出今天白天最值得关注的1-2件事，并结合持仓说明影响", titles_only, max_tokens=200, macro_context=macro_prompt),
        max_chars=180,
    )
    focus_block = "\n🔮 **今日重点关注**\n" + focus + "\n" if focus else ""

    return f"""☀️ **{today} 早间简报**　|　{now.strftime('%H:%M')}

{vix_line}
{market_block}
{earnings_block}
{macro_block}
{value_summary}
**📰 隔夜要闻**
{news_block}
{radar_block}
{global_block}
{insight_block}
{focus_block}> 📐 盘中 14:30 推送收盘前报告"""


def _build_asia_pacific_market() -> str:
    """亚太市场中午 12:00 实时快照（使用实时/盘中数据源）。"""
    lines = ["**🌏 亚太午盘**"]
    label = "（上午盘收盘）"

    # ── A 股（11:30 上午盘收盘，用新浪实时数据）──
    cn_lines = []
    try:
        import os as _os
        for _k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','all_proxy','ALL_PROXY'):
            _os.environ.pop(_k, None)
        import akshare as _ak
        df = _ak.stock_zh_index_spot_sina()
        target_names = {'上证指数': 'sh000001', '深证成指': 'sz399001', '创业板指': 'sz399006'}
        if '名称' in df.columns:
            for name in target_names:
                rows = df[df['名称'] == name]
                if len(rows) > 0:
                    r = rows.iloc[0]
                    price = float(r['最新价'])
                    pct = float(r['涨跌幅'])
                    arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                    cn_lines.append(f"· {name}: {price:,.2f}　{arrow}{pct:+.2f}%")
    except Exception:
        pass
    if cn_lines:
        lines.append("\n**A 股（上午盘收盘）**")
        lines.extend(cn_lines)

    # ── 港股（12:00 上午盘收盘，用新浪实时数据）──
    hk_lines = []
    try:
        import akshare as _ak
        df = _ak.stock_hk_index_spot_sina()
        target_names = {'恒生指数': 'HSI', '恒生科技指数': 'HSTECH'}
        if '名称' in df.columns:
            for name in target_names:
                rows = df[df['名称'] == name]
                if len(rows) > 0:
                    r = rows.iloc[0]
                    price = float(r['最新价'])
                    pct = float(r['涨跌幅'])
                    arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                    hk_lines.append(f"· {name}: {price:,.2f}　{arrow}{pct:+.2f}%")
    except Exception:
        pass
    if hk_lines:
        lines.append("\n**港股（上午盘收盘）**")
        lines.extend(hk_lines)

    # ── 日经/KOSPI/台湾（盘中实时，yfinance 优先）──
    apac_lines = []
    for ticker, name in [('^N225','日经225'), ('^KS11','韩国KOSPI'), ('^TWII','台湾加权')]:
        try:
            import yfinance as yf
            df = yf.Ticker(ticker).history(period='5d')
            if len(df) >= 2:
                prev = float(df['Close'].iloc[-2])
                today = float(df['Close'].iloc[-1])
                pct = round((today-prev)/prev*100,2)
                arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                apac_lines.append(f"· {name}: {today:,.2f}　{arrow}{pct:+.2f}%")
        except Exception:
            pass
    if apac_lines:
        lines.append("\n**亚太其他（日内）**")
        lines.extend(apac_lines)

    if len(lines) == 1:
        return ""
    return "\n".join(lines)

def _build_global_market_snapshot() -> str:
    """全球市场收盘快照（早间/晚间用，使用日线数据）。"""
    lines = ["**📊 全球市场**"]

    # ── 亚太收盘 ──
    apac_lines = []
    try:
        import os as _os
        for _k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','all_proxy','ALL_PROXY'):
            _os.environ.pop(_k, None)
        import akshare as _ak
        for sym, name in [('sh000001','上证指数'), ('sz399001','深证成指'), ('sz399006','创业板指')]:
            try:
                df = _ak.stock_zh_index_daily_tx(symbol=sym)
                if len(df) >= 2:
                    prev = float(df['close'].iloc[-2])
                    today = float(df['close'].iloc[-1])
                    pct = round((today-prev)/prev*100,2)
                    arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                    apac_lines.append(f"· {name}: {today:,.2f}　{arrow}{pct:+.2f}%")
            except Exception:
                pass
    except Exception:
        pass
    for sym, name in [('HSI','恒生指数'), ('HSTECH','恒生科技')]:
        try:
            import akshare as _ak
            # 优先用实时 spot（晚间/午间都是当前盘面）
            df = _ak.stock_hk_index_spot_sina()
            rows = df[df['名称']==name]
            if len(rows)>0:
                r = rows.iloc[0]
                price = float(r['最新价'])
                pct = float(r['涨跌幅'])
                arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                apac_lines.append(f"· {name}: {price:,.2f}　{arrow}{pct:+.2f}%")
                continue
        except Exception:
            pass
        try:
            df = _ak.stock_hk_index_daily_sina(symbol=sym)
            if len(df) >= 2:
                prev = float(df['close'].iloc[-2])
                today = float(df['close'].iloc[-1])
                pct = round((today-prev)/prev*100,2)
                arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                apac_lines.append(f"· {name}: {today:,.2f}　{arrow}{pct:+.2f}%")
        except Exception:
            pass
    for ticker, name in [('^N225','日经225'), ('^KS11','韩国KOSPI'), ('^TWII','台湾加权')]:
        try:
            import yfinance as yf
            df = yf.Ticker(ticker).history(period='5d')
            if len(df) >= 2:
                prev = float(df['Close'].iloc[-2])
                today = float(df['Close'].iloc[-1])
                pct = round((today-prev)/prev*100,2)
                arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                apac_lines.append(f"· {name}: {today:,.2f}　{arrow}{pct:+.2f}%")
        except Exception:
            pass
    if apac_lines:
        lines.append("\n**亚太收盘**")
        lines.extend(apac_lines)

    # ── 美股 ──
    us_lines = []
    for ticker, name in [('^GSPC','标普500'), ('^IXIC','纳斯达克')]:
        try:
            data = market_data.fetch_us_index(ticker)
            if data:
                a = "🔺" if data['change_pct'] > 0 else "🔻" if data['change_pct'] < 0 else "➖"
                us_lines.append(f"· {name}: {data['close']:,.2f}　{a}{data['change_pct']:+.2f}%")
        except Exception:
            pass
    for ticker, name in [('^DJI','道琼斯'), ('^SOX','费城半导体')]:
        try:
            data = market_data.fetch_us_index(ticker)
            if data:
                a = "🔺" if data['change_pct'] > 0 else "🔻" if data['change_pct'] < 0 else "➖"
                us_lines.append(f"· {name}: {data['close']:,.2f}　{a}{data['change_pct']:+.2f}%")
        except Exception:
            pass
    if us_lines:
        lines.append("\n**美股收盘**")
        lines.extend(us_lines)

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _build_midday() -> str:
    """12:00 亚太午盘收盘快讯。需要 A 股开市。"""
    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    # ── 亚太市场 ──
    apac_market = _build_asia_pacific_market()
    apac_block = "\n" + apac_market + "\n" if apac_market else ""

    articles = fetch_all_news(max_results=40)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=6)
    news_block = _fmt_news(filtered, max_items=6)
    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:6])

    # AI 快评 (increased token budget to prevent truncation)
    insight = _ai_insight("午间要闻——请根据上午新闻和亚太市场表现给出对下午A股走势的1-2点观察", titles_only, max_tokens=300)
    insight_block = f"\n🧠 **午间快评**\n{insight}\n" if insight else ""

    value_summary = _portfolio_value_summary()

    return f"""🌤️ **{today} 午间快讯**　|　{now.strftime('%H:%M')}

{apac_block}
**📰 上午要闻**
{news_block}
{insight_block}
{value_summary}
**💡 下午关注**
· A 股午后走势
· 14:30 收盘前报告
· 若上午大幅异动，提前检查飞书底仓表"""


def _build_closing() -> str:
    """14:30 A 股收盘前 30 分钟策略防御带 —— 仓位健康 + 雷达扫描 + 市场基准。"""
    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    from src.strategy import judge_from_feishu
    verdict = judge_from_feishu()

    articles = fetch_all_news(max_results=30)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=5)
    news_block = _fmt_news(filtered, max_items=5)

    # ── 仓位健康报告（只做偏离度展示）──
    health = verdict.get("health_report", "")
    health_block = f"\n{health}\n" if health else ""

    # ── 持仓市值 ──
    value_summary = _portfolio_value_summary()

    # ── 雷达扫描（底仓全部标的 + 雷达观测表）──
    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:8])
    radar_block = ""
    try:
        from src.radar import scan_radar, build_radar_brief, _radar_insight
        radar_result = scan_radar(dry_run=False)
        if radar_result["signal_items"]:
            radar_raw = build_radar_brief(radar_result["signal_items"])
            radar_ai = _radar_insight(radar_result["signal_items"], titles_only)
            radar_ai_block = f"\n{radar_ai}\n" if radar_ai else ""
            radar_block = f"\n{radar_raw}\n{radar_ai_block}" if radar_raw else ""
    except Exception:
        pass

    # ── 市场基准 ──
    market_context = _build_global_market_snapshot()
    market_block = f"\n{market_context}\n" if market_context else ""

    # ── 国际 RSS ──
    global_block = ""
    try:
        from src.global_news import _build_global_news_brief
        global_news_block = _build_global_news_brief()
        global_block = f"\n{global_news_block}\n" if global_news_block else ""
    except Exception:
        pass

    # ── AI 综合解读（所有数据就绪后再调 LLM）──
    radar_snippet = radar_block[:300] if radar_block else ""
    global_snippet = global_block[:300] if global_block else ""
    full_context = f"{titles_only} {health[:300]} {market_context[:300]} {radar_snippet} {global_snippet}"
    insight = _ai_insight("午间收盘前——请综合所有信息（仓位偏离度/市场基准/雷达信号/国际快讯），给出一段收盘前的综合建议", full_context, max_tokens=250)
    insight_block = f"\n🧠 **AI 综合解读**\n{insight}\n" if insight else ""

    focus = _sent_truncate(
        _ai_insight("收盘前——请给出今天剩下的时间最值得关注的1件事，并结合持仓说明", titles_only, max_tokens=150),
        max_chars=150,
    )
    focus_block = f"\n🔮 **收盘前关注**\n{focus}\n" if focus else ""

    return f"""⚡ **{today} 收盘前报告**　|　{now.strftime('%H:%M')}

{health_block}
{value_summary}
{market_block}
**📰 午间要闻**
{news_block}
{radar_block}
{global_block}
{insight_block}
{focus_block}
🔔 总市值 ¥{verdict['total_value']:,.2f}　|　买入参考 100-200 元/次　|　长底仓只买不卖

> 以上结论由量化系统计算，仅供参考，不构成投资建议"""


def _build_evening() -> str:
    """20:30 夜盘前瞻 + AI 综合解读（含港股终盘）。需要美股开市。"""

    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    # 港股收盘（16:00 定盘，晚间用实时 spot 拿最终数据）
    hsi_close = ""
    try:
        import akshare as _ak
        df_spot = _ak.stock_hk_index_spot_sina()
        for name, label in [('恒生指数', '恒生指数'), ('恒生科技指数', '恒生科技')]:
            rows = df_spot[df_spot['名称']==name]
            if len(rows)>0:
                r = rows.iloc[0]
                price = float(r['最新价'])
                pct = float(r['涨跌幅'])
                arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                hsi_close += f"\n· {label}: {price:,.2f}　{arrow}{pct:+.2f}%（今日收盘）"
    except Exception:
        # fallback: daily data
        try:
            for sym, label in [('HSI','恒生指数'), ('HSTECH','恒生科技')]:
                df = _ak.stock_hk_index_daily_sina(symbol=sym)
                if len(df) >= 2:
                    prev = float(df['close'].iloc[-2])
                    today = float(df['close'].iloc[-1])
                    pct = round((today-prev)/prev*100,2)
                    arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                    hsi_close += f"\n· {label}: {today:,.2f}　{arrow}{pct:+.2f}%（今日收盘）"
        except Exception:
            pass

    close_block = f"\n**🇭🇰 港股终盘**\n{hsi_close}\n" if hsi_close else ""

    articles = fetch_all_news(max_results=40)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=8)
    news_block = _fmt_news(filtered, max_items=8)
    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:8])

    # ── 1. VIX 恐慌指数 ──
    vix_block = _build_vix_block()
    vix_line = f"\n{vix_block}\n" if vix_block else ""

    # ── 2. 全球市场 ──
    market_context = _build_global_market_snapshot()
    market_block = f"\n{market_context}\n" if market_context else ""

    # ── 2. 近期财报提示 ──
    earnings_block = ""
    today_earnings = []
    try:
        from src.earnings_calendar import fetch_weekly_earnings, format_weekly_earnings
        today_earnings = fetch_weekly_earnings(days_ahead=1)
        if today_earnings:
            earnings_block = "\n" + format_weekly_earnings(today_earnings) + "\n"
    except Exception:
        pass

    # ── 3. 持仓市值 ──
    value_summary = _portfolio_value_summary()

    # ── 4. 雷达扫描 ──
    radar_block = ""
    try:
        from src.radar import scan_radar, build_radar_brief, _radar_insight
        radar_result = scan_radar(dry_run=False)
        if radar_result["signal_items"]:
            radar_raw = build_radar_brief(radar_result["signal_items"])
            radar_ai = _radar_insight(radar_result["signal_items"], titles_only)
            radar_ai_block = f"\n{radar_ai}\n" if radar_ai else ""
            radar_block = f"\n{radar_raw}\n{radar_ai_block}" if radar_raw else ""
    except Exception:
        pass

    # ── 5. 国际快讯（已关联持仓）──
    global_block = ""
    try:
        from src.global_news import _build_global_news_brief
        global_news_block = _build_global_news_brief()
        global_block = f"\n{global_news_block}\n" if global_news_block else ""
    except Exception:
        pass

    # ── 6. AI 综合解读（汇总所有信息，结合持仓）──
    earnings_titles = " ".join(f"{e['ticker']}{e.get('name','')}" for e in today_earnings[:5]) if today_earnings else ""
    market_snippet = market_context[:300] if market_context else ""
    radar_snippet = radar_block[:300] if radar_block else ""
    global_snippet = global_block[:300] if global_block else ""
    pf_summary = "\n".join(f"{p.get('name','')[:12]} {p.get('asset_class','')}" for p in pf[:10]) if pf else ""
    full_context = f"{titles_only} {earnings_titles} {market_snippet} {pf_summary} {radar_snippet} {global_snippet}"

    insight = _ai_insight(
        "今晚夜盘前瞻——请综合以下所有信息（国内新闻/国际快讯/全球市场/持仓/雷达信号/近期财报），"
        "给出一段对今晚美股和明天持仓的综合解读，必须提及对具体持仓大类的影响",
        full_context
    )
    insight_block = f"\n🧠 **AI 综合解读**\n{insight}\n" if insight else ""

    # ── 7. 今晚关注 ──
    focus = _sent_truncate(
        _ai_insight("今晚——请给出今晚/明天最值得关注的1-2件事，并结合持仓说明影响", titles_only, max_tokens=200),
        max_chars=180,
    )
    focus_block = f"\n🔮 **今晚关注**\n{focus}\n" if focus else ""

    return f"""🌆 **{today} 夜盘前瞻**　|　{now.strftime('%H:%M')}

{vix_line}
{close_block}
{market_block}
{earnings_block}
{value_summary}
**📰 今日要闻**
{news_block}
{radar_block}
{global_block}
{insight_block}
{focus_block}> ☀️ 明早 08:30 推送美股隔夜收盘复盘（⏰ 恒生指数已于 16:00 收盘，A 股已于 15:00 收盘）"""


def _build_sat_morning() -> str:
    """周六 08:30 周五美股收盘复盘。"""
    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    vix = market_data.fetch_vix()
    vix_str = f"{vix['vix']:.1f}（{vix['level']}）" if vix and vix.get("vix") else "获取失败"
    spx = market_data.fetch_us_etf("SPY")
    qqq = market_data.fetch_us_etf("QQQ")
    spx_str = f"${spx['close']:.2f}（{spx['change_pct']:+.2f}%）" if spx else "获取失败"
    qqq_str = f"${qqq['close']:.2f}（{qqq['change_pct']:+.2f}%）" if qqq else "获取失败"

    articles = fetch_all_news(max_results=40)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=6)
    news_block = _fmt_news(filtered, max_items=6)
    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:6])

    insight = _sent_truncate(
        _ai_insight("周五美股收盘总结——本周美股表现如何？对下周持仓有什么影响？", titles_only, max_tokens=300),
        max_chars=250,
    )

    insight_block = f"\n🧠 **本周美股回顾**\n{insight}\n" if insight else ""

    return f"""📅 **{today} 周末复盘**　|　{now.strftime('%H:%M')}

**🇺🇸 周五美股收盘**
· 标普500：{spx_str}
· 纳斯达克100：{qqq_str}
· VIX：{vix_str}

**📰 本周要闻**
{news_block}
{insight_block}> ☀️ 周日 20:00 推送下周前瞻"""


def _build_weekly_return(pf: list[dict]) -> str:
    """计算本周持仓收益 vs 基准。"""
    from src.radar import _fetch_historical_prices

    total_start = 0.0
    total_end = 0.0
    lines = []

    for p in pf:
        shares = float(p.get("shares", 0))
        latest = float(p.get("latest_price", 0))
        code = p.get("code", "")
        name = p.get("name", "")
        if shares <= 0 or not code:
            continue
        mv_end = shares * latest
        total_end += mv_end

        hist = _fetch_historical_prices(code, days=10)
        if hist and len(hist["prices"]) >= 8:
            # 约 7 天前价格
            price_7d_ago = hist["prices"][-min(8, len(hist["prices"]))]
            mv_start = shares * price_7d_ago
            total_start += mv_start
        else:
            total_start += mv_end  # 无历史数据，假设不变

    if total_start <= 0:
        return ""

    week_pnl = total_end - total_start
    week_pct = (week_pnl / total_start) * 100
    arrow = "🔺" if week_pnl > 0 else "🔻" if week_pnl < 0 else "➖"
    lines.append(f"**📊 本周仓位盘点**")
    lines.append(f"总市值 ¥{total_end:,.0f}　本周 {arrow} ¥{week_pnl:+,.0f}（{week_pct:+.1f}%）")

    return "\n".join(lines)


def _build_sun_evening() -> str:
    """周日 20:00 周报 —— 仓位盘点 + 周末要闻 + 宏观回顾 + 下周关注。"""
    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    pf = load_portfolio()

    # ── 1. 仓位盘点 ──
    weekly_return = _build_weekly_return(pf)

    # ── 2. 仓位安全垫分布（静态，不引导操作）──
    from src.strategy import judge_from_feishu
    verdict = judge_from_feishu()
    health = verdict.get("health_report", "")
    # 剔除「增量资金优先方向」这类无法在周日执行的动态话术
    health = health.replace("【增量资金优先方向】", "").strip()

    # ── 3. 周末要闻复盘：专门拉周六+周日的新闻 ──
    weekend_articles = fetch_all_news(max_results=40)
    weekend_filtered = _filter_by_keywords(weekend_articles, pf, top_n=6)
    weekend_titles = " ".join(_clean_html(a.get("title", "")) for a in weekend_filtered[:6])

    weekend_news_summary = ""
    if weekend_titles.strip():
        try:
            from src.llm import get_llm_client, get_llm_model
            client = get_llm_client()
            if client:
                resp = client.chat.completions.create(
                    model=get_llm_model(), max_tokens=150, temperature=0.3,
                    messages=[{"role": "user", "content": f"""你是量化投资顾问。以下是周末两天的财经新闻标题。
提炼最重要的 2-3 条高价值资讯，每条约 20 字，用大白话写。

<weekend_news>
{weekend_titles[:800]}
</weekend_news>

直接输出，每条一行，格式：· xxx。不要前缀。"""}],
                )
                weekend_news_summary = resp.choices[0].message.content.strip()
        except Exception:
            pass
    weekend_block = f"\n📅 **周末要闻复盘**\n{weekend_news_summary}\n" if weekend_news_summary else ""

    # ── 4. 宏观日历 ──
    from src.macro_calendar import (
        fetch_past_calendar,
        fetch_upcoming_calendar,
        format_calendar_for_brief,
        calendar_context_for_prompt,
    )
    past_events = fetch_past_calendar(min_impact="Medium", days_behind=7)
    future_events = fetch_upcoming_calendar(min_impact="Medium", days_ahead=7)
    future_macro_display = format_calendar_for_brief(future_events)

    # ── 5. 国际要闻 ──
    global_news_text = ""
    try:
        from src.global_news import _build_global_news_brief
        gnb = _build_global_news_brief()
        if gnb:
            global_news_text = gnb
    except Exception:
        pass

    # ── 6. 下周财报 ──
    earnings_block = ""
    try:
        from src.earnings_calendar import fetch_weekly_earnings, format_weekly_earnings
        wk_earnings = fetch_weekly_earnings(days_ahead=7)
        if wk_earnings:
            earnings_block = format_weekly_earnings(wk_earnings)
    except Exception:
        pass

    # ── 7. LLM 综合：宏观回顾 + 下周关注（交叉推演周末要闻+日历）──
    past_summary = "\n".join(
        f"· {e.get('date','')} {e.get('title','')} [{e.get('stars','')}]"
        for e in past_events[:8]
    ) if past_events else "(本周无重大宏观事件)"

    future_summary = calendar_context_for_prompt(future_events, pf) if future_events else ""

    llm_block = ""
    if past_events or future_events or global_news_text or weekend_news_summary:
        try:
            from src.llm import get_llm_client, get_llm_model
            client = get_llm_client()
            if client:
                extra_rules = (
                    "- 第一部分「本周宏观回顾」：从已发生宏观事件中挑最重要的2-3件，每件1句话+对持仓大类的影响\n"
                    "- 第二部分「下周防守与狙击要点」：必须结合周末要闻复盘+下周宏观日历+下周财报，交叉推演2-3条方向性提示\n"
                    "  格式：如果X发生→Y大类会怎样→你应该Z。禁止\"适当关注\"这类废话\n"
                    "- 总共200-250字\n"
                    "- 输出格式：\n"
                    "  📅 本周宏观回顾\n"
                    "  · （事件1 + 对持仓的影响）\n"
                    "  · （事件2 + 对持仓的影响）\n\n"
                    "  🛡️ 下周防守与狙击要点\n"
                    "  · （推演1：触发条件→影响→方向）\n"
                    "  · （推演2：触发条件→影响→方向）"
                )

                from src.prompt_templates import build_analysis_prompt
                prompt = build_analysis_prompt(
                    role="你是量化投资顾问。请根据以下信息产出周报的宏观回顾和下周防守要点。",
                    holdings_text=f"{weekly_return}\n\n仓位安全垫:\n{health[:300]}",
                    market_text=f"已发生事件:\n{past_summary}\n\n未来日历:\n{future_summary}",
                    news_text=f"周末要闻:\n{weekend_news_summary[:300] if weekend_news_summary else '(无)'}\n\n"
                              f"下周财报:\n{earnings_block[:300] if earnings_block else '(无)'}\n\n"
                              f"国际快讯:\n{global_news_text[:400] if global_news_text else '(无)'}",
                    extra_rules=extra_rules,
                )
                resp = client.chat.completions.create(
                    model=get_llm_model(), max_tokens=500, temperature=0.3,
                    messages=[{"role": "user", "content": prompt}],
                )
                llm_block = resp.choices[0].message.content.strip()
        except Exception:
            pass

    return f"""📅 **{today} 周报**

{weekly_return}

**🛡️ 当前仓位安全垫分布**
{health}

{weekend_block}
{llm_block}

{earnings_block}

{future_macro_display}"""


# ═══════════════════════════════════════════════════════════════
# 路由与入口
# ═══════════════════════════════════════════════════════════════

BRIEFINGS = {
    "morning":      ("☀️ 早间简报", _build_morning),
    "midday":       ("🌤️ 午间快讯", _build_midday),
    "closing":      ("⚡ 收盘前指令", _build_closing),
    "evening":      ("🌆 夜盘前瞻", _build_evening),
    "sat_morning":  ("📅 周末复盘", _build_sat_morning),
    "sun_evening":  ("📅 周末前瞻", _build_sun_evening),
}

# 需要 A 股开市才运行的时段
_CN_GATED = {"midday", "closing"}


def main():
    from dotenv import load_dotenv
    load_dotenv()

    if len(sys.argv) < 2 or sys.argv[1] not in BRIEFINGS:
        print("用法: python -m src.briefing [morning|midday|closing|evening|sat_morning|sun_evening]")
        sys.exit(1)

    mode = sys.argv[1]
    title, builder = BRIEFINGS[mode]

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",
    )

    # ── 节假日熔断 ──
    if mode in _CN_GATED and not is_cn_market_open():
        logger.info("A 股今日休市，跳过 %s", title)
        print(f"\n   ⛔ A 股休市，{title} 跳过\n")
        return

    print(f"\n{'='*50}\n   📨 {title}")

    # ── 现价强制刷新（所有模式通用） ──
    logger.info("先刷新现价…")
    try:
        from src.price_updater import update_all_prices
        update_all_prices(dry_run=False)
    except Exception as e:
        logger.warning("现价更新失败（不影响后续）: %s", e)

    logger.info("加载持仓 + 抓取新闻…")
    card = builder()

    if card == "SKIP":
        print(f"\n   ⛔ 休市，{title} 跳过\n{'='*50}")
        return

    logger.info("推送到飞书群…")
    _push(title, card)
    print(f"\n   ✅ 推送完成\n{'='*50}")


if __name__ == "__main__":
    main()
