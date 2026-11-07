"""Slot builders for all briefing time slots."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from src.briefing_modules.headers import _is_compact_style
from src.briefing_modules.market_blocks import (
    _build_vix_block,
    _build_us_futures_block,
    _build_asia_pacific_market,
    _build_global_market_snapshot,
    _get_us_dst_reminder,
)
from src.briefing_modules.news import (
    _build_digest_news_block,
    _fmt_news,
    _news_link_block,
    _sanitize_llm_output,
    _truncate_at_sentence_boundary,
)
from src.briefing_modules.sector_blocks import _build_sector_parts, _MACRO_AI_RULE
from src.briefing_modules.signals import _build_hard_signals_block, _short_cls
from src.briefing_modules.snapshots import (
    _diff_against_last,
    _extract_metrics_from_pf,
    _extract_metrics_from_verdict,
    _format_diff_brief,
    _make_signature,
    _save_snapshot,
)
from src.briefing_modules.valuation import (
    _build_trade_summary,
    _build_weekly_return,
    _exchange_rate_footnote,
    _portfolio_value_summary,
)
from src.holiday_gate import holiday_notice, is_us_market_open
from src.macro_calendar import (
    calendar_context_for_prompt,
    fetch_today_calendar,
    filter_portfolio_relevant,
    format_macro_signal_line,
)
from src.news_fetcher import _clean_html, _filter_by_keywords, fetch_all_news

logger = logging.getLogger(__name__)
tz_cn = timezone(timedelta(hours=8))


def _build_morning() -> str:
    """07:30 早间简报 + AI 综合解读（含宏观日历）。"""
    from src import briefing
    load_pf_fn = getattr(briefing, "load_portfolio", None)
    if load_pf_fn is None:
        from src.advisor import load_portfolio as load_pf_fn

    ai_insight_fn = getattr(briefing, "_ai_insight", None)
    if ai_insight_fn is None:
        from src.briefing_modules.narrator import _ai_insight as ai_insight_fn

    save_snap_fn = getattr(briefing, "_save_snapshot", _save_snapshot)
    diff_fn = getattr(briefing, "_diff_against_last", _diff_against_last)

    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    fetch_news_fn = getattr(briefing, "fetch_all_news", fetch_all_news)
    articles = fetch_news_fn(max_results=50)
    pf = load_pf_fn()

    filter_fn = getattr(briefing, "_filter_by_keywords", _filter_by_keywords)
    filtered = filter_fn(articles, pf, top_n=8)

    news_link_fn = getattr(briefing, "_news_link_block", _news_link_block)
    news_link_block = news_link_fn(articles)
    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:8])

    vix_block_fn = getattr(briefing, "_build_vix_block", _build_vix_block)
    vix_block = vix_block_fn()
    vix_line = "\n" + vix_block + "\n" if vix_block else ""

    global_snap_fn = getattr(briefing, "_build_global_market_snapshot", _build_global_market_snapshot)
    market_context = global_snap_fn(prefix="上一交易日收盘")
    market_block = "\n" + market_context + "\n" if market_context else ""

    sector_parts_fn = getattr(briefing, "_build_sector_parts", _build_sector_parts)
    sector_line, sector_for_ai = sector_parts_fn(market_filter="us")
    sector_block = "\n" + sector_line + "\n" if sector_line else ""

    earnings_block = ""
    yday = []
    try:
        from src.earnings_calendar import fetch_yesterdays_earnings, format_yesterdays_earnings
        yday = fetch_yesterdays_earnings()
        if yday:
            earnings_block = "\n" + format_yesterdays_earnings(yday) + "\n"
    except Exception:
        pass

    macro_events = filter_portfolio_relevant(fetch_today_calendar(min_impact="Medium"))
    macro_display = format_macro_signal_line(macro_events, scope="今日")
    macro_prompt = calendar_context_for_prompt(macro_events, pf)
    macro_block = "\n" + macro_display + "\n" if macro_display else ""

    portfolio_val_fn = getattr(briefing, "_portfolio_value_summary", _portfolio_value_summary)
    value_summary = portfolio_val_fn()

    radar_block = ""
    try:
        from src.radar import scan_radar, build_radar_brief
        radar_result = scan_radar(dry_run=False)
        if radar_result["signal_items"]:
            _sig_priority = {"🔵 底部反转": 0, "🟡 关注": 1, "🟢 趋势加速": 2}
            _sorted = sorted(
                radar_result["signal_items"],
                key=lambda s: _sig_priority.get(s.get("buy_signal") or s.get("chase_signal", ""), 9)
            )
            _top = _sorted[:5]
            _more = f"\n（另有 {len(radar_result['signal_items']) - 5} 个信号未列出）" if len(_sorted) > 5 else ""
            radar_raw = build_radar_brief(_top) + _more
            radar_block = "\n" + radar_raw if radar_raw else ""
    except Exception:
        pass

    global_news_block = ""
    try:
        from src.global_news import _build_global_news_brief
        global_news_block = _build_global_news_brief() or ""
    except Exception:
        pass

    is_compact_fn = getattr(briefing, "_is_compact_style", _is_compact_style)
    if is_compact_fn():
        news_heading = "**📰 核心要闻**"
        news_block = _build_digest_news_block(filtered, global_news_block, max_bullets=4)
        global_block = ""
        next_hint = "> 📐 中午 12:00 推送午休速览"
    else:
        news_heading = "**📰 隔夜要闻**"
        news_block = _fmt_news(filtered, max_items=8)
        global_block = "\n" + global_news_block + "\n" if global_news_block else ""
        next_hint = "> 📐 上午 12:00 推送午间快讯"

    earnings_titles = " ".join(f"{e['ticker']} {e.get('name','')}" for e in yday[:5]) if yday else ""
    radar_snippet = radar_block[:800] if radar_block else ""
    global_for_ai = ""
    try:
        from src.global_news import build_global_news_for_ai
        global_for_ai = build_global_news_for_ai()
    except Exception:
        pass

    pf_summary = "\n".join(f"{p.get('name','')[:12]} {p.get('asset_class','')}" for p in pf[:10]) if pf else ""
    trade_sum_fn = getattr(briefing, "_build_trade_summary", _build_trade_summary)
    trades = trade_sum_fn()
    full_context = f"{titles_only} {earnings_titles} {trades} {market_context[:300]} {pf_summary} {radar_snippet} {global_for_ai}"

    hard_signals = ""
    _morning_verdict = {}
    try:
        from src.strategy import judge_from_feishu
        _morning_verdict = judge_from_feishu()
        hard_signals = _build_hard_signals_block(_morning_verdict)
    except Exception as e:
        logger.warning("morning 段 judge_from_feishu 失败，硬信号注入跳过: %s", str(e)[:80])

    metrics = _extract_metrics_from_verdict(_morning_verdict) or _extract_metrics_from_pf(pf)
    info_fingerprint = f"新闻:{titles_only[:400]}|行情:{market_context[:150]}|快讯:{global_for_ai[:250]}"
    diff = diff_fn("morning", _make_signature(info_fingerprint), metrics)
    if diff["has_change"]:
        insight = ai_insight_fn(
            "早间简报——请综合所有信息（隔夜新闻/昨日财报/近5日交易记录/全球市场/持仓/宏观日历/雷达信号/国际快讯），"
            "给出一段对今天持仓的综合解读，必须提及对具体持仓大类的影响。"
            "如果交易记录显示某大类近期已操作过，在建议中提醒'3天内同一大类已经操作过，按纪律等冷却期'。"
            "结尾用一句话说今天最值得关注的1-2件事。",
            full_context, macro_context=macro_prompt,
            hard_signals=hard_signals, diff_brief=_format_diff_brief(diff),
            sector_brief=sector_for_ai,
        )
    else:
        insight = "今日较上次推送无显著变化（信息面与持仓指标均稳定），按纪律维持不动。"
    from src.llm import get_last_completion_info
    model_badge = f"> 🤖 智囊模型：{get_last_completion_info()}\n"
    insight_block = f"\n🧠 **AI 综合解读**\n{insight}\n{model_badge}" if insight else ""

    h_notice_fn = getattr(briefing, "holiday_notice", holiday_notice)
    _holiday_notice = h_notice_fn()
    holiday_line = f"\n{_holiday_notice}" if _holiday_notice else ""

    from src.briefing_modules.pyramid import build_pyramid_action_card
    pyramid_card = build_pyramid_action_card(
        slot="morning",
        verdict=_morning_verdict,
        portfolio=pf,
        vix_val=vix_block,
        compact=is_compact_fn(),
    )

    card = f"""☀️ **{today} 早间简报**　|　{now.strftime('%H:%M')}{holiday_line}

{pyramid_card}
{vix_line}
{market_block}{sector_block}
{news_heading}
{news_block}{news_link_block}
{earnings_block}
{macro_block}
{radar_block}
{global_block}{value_summary}
{insight_block}{next_hint}"""

    save_snap_fn("morning", _make_signature(info_fingerprint), metrics)
    return card


def _build_midday() -> str:
    """12:00 亚太午盘收盘快讯（compact=午休百字速览 / full=完整午间快讯）。需要 A 股开市。"""
    from src import briefing
    load_pf_fn = getattr(briefing, "load_portfolio", None)
    if load_pf_fn is None:
        from src.advisor import load_portfolio as load_pf_fn

    ai_insight_fn = getattr(briefing, "_ai_insight", None)
    if ai_insight_fn is None:
        from src.briefing_modules.narrator import _ai_insight as ai_insight_fn

    is_compact_fn = getattr(briefing, "_is_compact_style", _is_compact_style)
    compact = is_compact_fn()

    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    apac_market_fn = getattr(briefing, "_build_asia_pacific_market", _build_asia_pacific_market)
    apac_market = apac_market_fn()
    apac_block = "\n" + apac_market + "\n" if apac_market else ""

    fetch_news_fn = getattr(briefing, "fetch_all_news", fetch_all_news)
    articles = fetch_news_fn(max_results=40)
    pf = load_pf_fn()

    filter_fn = getattr(briefing, "_filter_by_keywords", _filter_by_keywords)
    filtered = filter_fn(articles, pf, top_n=6)

    news_link_fn = getattr(briefing, "_news_link_block", _news_link_block)
    news_link_block = news_link_fn(articles)
    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:6])

    news_block = _build_digest_news_block(filtered, max_bullets=3) if compact else _fmt_news(filtered, max_items=6)

    sector_parts_fn = getattr(briefing, "_build_sector_parts", _build_sector_parts)
    sector_line, sector_for_ai = sector_parts_fn(market_filter="hk_cn")
    sector_block = f"\n{sector_line}\n" if sector_line else ""

    portfolio_val_fn = getattr(briefing, "_portfolio_value_summary", _portfolio_value_summary)
    value_summary = portfolio_val_fn()

    if compact:
        insight = ai_insight_fn(
            "午间速览——请结合上午新闻与亚太盘面，用1-2句大白话点出核心变化及对持仓影响",
            titles_only, max_tokens=200, fast_mode=True, sector_brief=sector_for_ai,
        )
        from src.llm import get_last_completion_info
        model_badge = f"\n> 🤖 智囊模型：{get_last_completion_info()}"
        insight_block = f"\n🧠 **午间快评**\n{insight}{model_badge}" if insight else ""
        return f"""🌤️ **{today} 午间快讯**　|　{now.strftime('%H:%M')}
{apac_block}{sector_block}
**📰 核心要闻**
{news_block}{news_link_block}
{value_summary}{insight_block}"""

    insight = ai_insight_fn(
        "午间要闻——请根据上午新闻和亚太市场表现给出对下午A股走势的1-2点观察",
        titles_only, max_tokens=800, sector_brief=sector_for_ai,
    )
    from src.llm import get_last_completion_info
    model_badge = f"> 🤖 智囊模型：{get_last_completion_info()}\n"
    insight_block = f"\n🧠 **午间快评**\n{insight}\n{model_badge}" if insight else ""
    return f"""🌤️ **{today} 午间快讯**　|　{now.strftime('%H:%M')}

{apac_block}{sector_block}
**📰 上午要闻**
{news_block}{news_link_block}
{value_summary}
{insight_block}
**💡 下午关注**
· 亚太市场午后走势
· 14:30 收盘前报告（15:00 场外基金截单）"""


def _build_closing() -> str:
    """14:30 A 股收盘前 30 分钟策略防御带 -- 仓位健康 + 雷达扫描 + 市场基准。"""
    from src import briefing
    briefing._last_closing_signal = ""

    load_pf_fn = getattr(briefing, "load_portfolio", None)
    if load_pf_fn is None:
        from src.advisor import load_portfolio as load_pf_fn

    ai_insight_fn = getattr(briefing, "_ai_insight", None)
    if ai_insight_fn is None:
        from src.briefing_modules.narrator import _ai_insight as ai_insight_fn

    save_snap_fn = getattr(briefing, "_save_snapshot", _save_snapshot)
    diff_fn = getattr(briefing, "_diff_against_last", _diff_against_last)

    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    from src.strategy import judge_from_feishu
    verdict = judge_from_feishu()

    _actionable = [
        _short_cls(s.get("asset_class", ""))
        for s in (verdict.get("signals") or [])
        if (
            s.get("signal") == "TRIGGER_SELL"
            or (
                s.get("signal") in ("TRIGGER_BUY", "TRIGGER_STRONG_BUY")
                and not s.get("cooldown_status")
                and "偏离+趋势双确认" in str(s.get("timing") or "")
            )
        )
    ]
    briefing._last_closing_signal = "、".join(_actionable)

    fetch_news_fn = getattr(briefing, "fetch_all_news", fetch_all_news)
    articles = fetch_news_fn(max_results=30)
    pf = load_pf_fn()

    filter_fn = getattr(briefing, "_filter_by_keywords", _filter_by_keywords)
    filtered = filter_fn(articles, pf, top_n=5)

    news_link_fn = getattr(briefing, "_news_link_block", _news_link_block)
    news_link_block = news_link_fn(articles)

    health = verdict.get("health_report", "")
    health_block = f"\n{health}\n" if health else ""

    portfolio_val_fn = getattr(briefing, "_portfolio_value_summary", _portfolio_value_summary)
    value_summary = portfolio_val_fn()

    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:8])
    radar_block = ""
    try:
        from src.radar import scan_radar, build_radar_brief
        radar_result = scan_radar(dry_run=False)
        if radar_result["signal_items"]:
            _sig_priority = {"🔵 底部反转": 0, "🟡 关注": 1, "🟢 趋势加速": 2}
            _sorted = sorted(
                radar_result["signal_items"],
                key=lambda s: _sig_priority.get(s.get("buy_signal") or s.get("chase_signal", ""), 9)
            )
            _top = _sorted[:5]
            _more = f"\n（另有 {len(radar_result['signal_items']) - 5} 个信号未列出）" if len(_sorted) > 5 else ""
            radar_raw = build_radar_brief(_top) + _more
            radar_block = f"\n{radar_raw}\n" if radar_raw else ""
    except Exception:
        pass

    futures_raw_fn = getattr(briefing, "_build_us_futures_block", _build_us_futures_block)
    futures_raw = futures_raw_fn()
    futures_block = f"\n🌙 **美股盘前风向**\n{futures_raw}\n" if futures_raw else ""

    sector_parts_fn = getattr(briefing, "_build_sector_parts", _build_sector_parts)
    sector_line, sector_for_ai = sector_parts_fn(max_ai_items=3)
    sector_block = f"\n{sector_line}\n" if sector_line else ""
    sector_raw = sector_for_ai

    global_news_block = ""
    try:
        from src.global_news import _build_global_news_brief
        global_news_block = _build_global_news_brief() or ""
    except Exception:
        pass

    is_compact_fn = getattr(briefing, "_is_compact_style", _is_compact_style)
    if is_compact_fn():
        news_heading = "**📰 核心要闻**"
        news_block = _build_digest_news_block(filtered, global_news_block, max_bullets=4)
        global_block = ""
    else:
        news_heading = "**📰 午间要闻**"
        news_block = _fmt_news(filtered, max_items=5)
        global_block = f"\n{global_news_block}\n" if global_news_block else ""

    radar_snippet = radar_block[:300] if radar_block else ""
    futures_snippet = futures_raw[:150] if futures_raw else ""
    global_for_ai = ""
    try:
        from src.global_news import build_global_news_for_ai
        global_for_ai = build_global_news_for_ai()[:400]
    except Exception:
        pass
    slim_context = f"{titles_only[:200]} {futures_snippet} {radar_snippet} {global_for_ai}"

    metrics = _extract_metrics_from_verdict(verdict)
    info_fingerprint = f"新闻:{titles_only[:300]}|期货:{futures_raw[:100]}|板块:{sector_raw[:200]}|快讯:{global_for_ai[:150]}"
    diff = diff_fn("closing", _make_signature(info_fingerprint), metrics)
    if diff["has_change"]:
        closing_role = (
            "收盘前30分钟——请快速综合以下信号给出建议"
            if (briefing._last_closing_signal or "🔵 底部反转" in radar_block)
            else "收盘前30分钟（今日无触发动作，按兵不动）——简述盘面并点明等待什么信号"
        )
        insight = ai_insight_fn(
            closing_role,
            slim_context, max_tokens=500, fast_mode=True, diff_brief=_format_diff_brief(diff),
            sector_brief=sector_for_ai)
    else:
        insight = "今日较上次推送无显著变化（信息面与持仓指标均稳定），收盘前按纪律维持不动。"
    from src.llm import get_last_completion_info
    model_badge = f"> 🤖 智囊模型：{get_last_completion_info()}\n"
    insight_block = f"\n🧠 **AI 综合解读**\n{insight}\n{model_badge}" if insight else ""

    fx_tail = "" if "汇率折算基准日" in value_summary else _exchange_rate_footnote(verdict.get('exchange_rates'))

    from src.briefing_modules.pyramid import build_pyramid_action_card
    pyramid_card = build_pyramid_action_card(
        slot="closing",
        verdict=verdict,
        portfolio=pf,
        closing_signal=briefing._last_closing_signal,
        radar_has_reversal=("🔵 底部反转" in radar_block),
        compact=is_compact_fn(),
    )

    card = f"""⚡ **{today} 收盘前指令**　|　{now.strftime('%H:%M')}　⏰ 距 15:00 截单还有 30 分钟

{pyramid_card}
{health_block}
{value_summary}
{news_heading}
{news_block}{news_link_block}
{futures_block}{sector_block}{radar_block}
{global_block}
{insight_block}
🔔 总市值 ¥{verdict['total_value']:,.2f}　|　长底仓只买不卖{fx_tail}

> 以上结论由量化系统计算，仅供参考，不构成投资建议"""

    save_snap_fn("closing", _make_signature(info_fingerprint), metrics)
    return card


def _build_evening() -> str:
    """夜盘前瞻 + AI 综合解读。需要美股开市。"""
    from src import briefing
    us_open_fn = getattr(briefing, "is_us_market_open", is_us_market_open)
    if not us_open_fn():
        return "SKIP"
    load_pf_fn = getattr(briefing, "load_portfolio", None)
    if load_pf_fn is None:
        from src.advisor import load_portfolio as load_pf_fn

    ai_insight_fn = getattr(briefing, "_ai_insight", None)
    if ai_insight_fn is None:
        from src.briefing_modules.narrator import _ai_insight as ai_insight_fn

    save_snap_fn = getattr(briefing, "_save_snapshot", _save_snapshot)
    diff_fn = getattr(briefing, "_diff_against_last", _diff_against_last)

    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    fetch_news_fn = getattr(briefing, "fetch_all_news", fetch_all_news)
    articles = fetch_news_fn(max_results=40)
    pf = load_pf_fn()

    filter_fn = getattr(briefing, "_filter_by_keywords", _filter_by_keywords)
    filtered = filter_fn(articles, pf, top_n=8)

    news_link_fn = getattr(briefing, "_news_link_block", _news_link_block)
    news_link_block = news_link_fn(articles)
    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:8])

    vix_block_fn = getattr(briefing, "_build_vix_block", _build_vix_block)
    vix_block = vix_block_fn()
    vix_line = f"\n{vix_block}\n" if vix_block else ""

    futures_raw_fn = getattr(briefing, "_build_us_futures_block", _build_us_futures_block)
    futures_raw = futures_raw_fn()
    futures_block = f"\n📡 **美股期货实时**\n{futures_raw}\n" if futures_raw else ""

    sector_parts_fn = getattr(briefing, "_build_sector_parts", _build_sector_parts)
    sector_line, sector_for_ai = sector_parts_fn()
    sector_block = f"\n{sector_line}\n" if sector_line else ""

    global_snap_fn = getattr(briefing, "_build_global_market_snapshot", _build_global_market_snapshot)
    market_context = global_snap_fn()
    market_block = f"\n{market_context}\n" if market_context else ""

    earnings_block = ""
    today_earnings = []
    try:
        from src.earnings_calendar import fetch_weekly_earnings, format_weekly_earnings
        today_earnings = fetch_weekly_earnings(days_ahead=1)
        if today_earnings:
            earnings_block = "\n" + format_weekly_earnings(today_earnings) + "\n"
    except Exception:
        pass

    portfolio_val_fn = getattr(briefing, "_portfolio_value_summary", _portfolio_value_summary)
    value_summary = portfolio_val_fn()

    radar_block = ""
    try:
        from src.radar import scan_radar, build_radar_brief
        radar_result = scan_radar(dry_run=False)
        if radar_result["signal_items"]:
            _sig_priority = {"🔵 底部反转": 0, "🟡 关注": 1, "🟢 趋势加速": 2}
            _sorted = sorted(
                radar_result["signal_items"],
                key=lambda s: _sig_priority.get(s.get("buy_signal") or s.get("chase_signal", ""), 9)
            )
            _top = _sorted[:5]
            _more = f"\n（另有 {len(radar_result['signal_items']) - 5} 个信号未列出）" if len(_sorted) > 5 else ""
            radar_raw = build_radar_brief(_top) + _more
            radar_block = f"\n{radar_raw}\n" if radar_raw else ""
    except Exception:
        pass

    global_news_block = ""
    try:
        from src.global_news import _build_global_news_brief
        global_news_block = _build_global_news_brief() or ""
    except Exception:
        pass

    is_compact_fn = getattr(briefing, "_is_compact_style", _is_compact_style)
    if is_compact_fn():
        news_heading = "**📰 核心要闻**"
        news_block = _build_digest_news_block(filtered, global_news_block, max_bullets=4)
        global_block = ""
    else:
        news_heading = "**📰 今日要闻**"
        news_block = _fmt_news(filtered, max_items=8)
        global_block = f"\n{global_news_block}\n" if global_news_block else ""

    earnings_titles = " ".join(f"{e['ticker']}{e.get('name','')}" for e in today_earnings[:5]) if today_earnings else ""
    market_snippet = market_context[:300] if market_context else ""
    radar_snippet = radar_block[:800] if radar_block else ""
    global_for_ai = ""
    try:
        from src.global_news import build_global_news_for_ai
        global_for_ai = build_global_news_for_ai()
    except Exception:
        pass
    pf_summary = "\n".join(f"{p.get('name','')[:12]} {p.get('asset_class','')}" for p in pf[:10]) if pf else ""
    trade_sum_fn = getattr(briefing, "_build_trade_summary", _build_trade_summary)
    trades = trade_sum_fn()
    futures_snippet = futures_raw[:200] if futures_raw else ""
    full_context = f"{titles_only} {trades} {earnings_titles} {futures_snippet} {market_snippet} {pf_summary} {radar_snippet} {global_for_ai}"

    hard_signals = ""
    _evening_verdict = {}
    try:
        from src.strategy import judge_from_feishu
        _evening_verdict = judge_from_feishu()
        hard_signals = _build_hard_signals_block(_evening_verdict)
    except Exception as e:
        logger.warning("evening 段 judge_from_feishu 失败，硬信号注入跳过: %s", str(e)[:80])

    metrics = _extract_metrics_from_verdict(_evening_verdict) or _extract_metrics_from_pf(pf)
    info_fingerprint = f"新闻:{titles_only[:400]}|期货:{futures_raw[:100]}|快讯:{global_for_ai[:250]}"
    diff = diff_fn("evening", _make_signature(info_fingerprint), metrics)
    if diff["has_change"]:
        insight = ai_insight_fn(
            "今晚夜盘前瞻——请综合以下所有信息（国内新闻/近5日交易记录/国际快讯/全球市场/持仓/雷达信号/近期财报），"
            "给出一段对今晚美股和明天持仓的综合解读，必须提及对具体持仓大类的影响。"
            "结尾用一句话说今晚/明天最值得关注的1-2件事",
            full_context, hard_signals=hard_signals, diff_brief=_format_diff_brief(diff),
            sector_brief=sector_for_ai,
        )
    else:
        insight = "今日较上次推送无显著变化（信息面与持仓指标均稳定），夜盘按纪律维持不动。"
    from src.llm import get_last_completion_info
    model_badge = f"> 🤖 智囊模型：{get_last_completion_info()}\n"
    insight_block = f"\n🧠 **AI 综合解读**\n{insight}\n{model_badge}" if insight else ""

    dst_fn = getattr(briefing, "_get_us_dst_reminder", _get_us_dst_reminder)
    dst_reminder = dst_fn(now)

    from src.briefing_modules.pyramid import build_pyramid_action_card
    pyramid_card = build_pyramid_action_card(
        slot="evening",
        verdict=_evening_verdict,
        portfolio=pf,
        vix_val=vix_block,
        compact=is_compact_fn(),
    )

    card = f"""🌆 **{today} 夜盘前瞻**　|　{now.strftime('%H:%M')}

{pyramid_card}
{value_summary}
{vix_line}
{futures_block}{sector_block}{market_block}
{news_heading}
{news_block}{news_link_block}
{earnings_block}
{radar_block}
{global_block}{insight_block}{dst_reminder}> ☀️ 明早 07:30 推送美股隔夜收盘复盘"""

    save_snap_fn("evening", _make_signature(info_fingerprint), metrics)
    return card


def _build_sat_morning() -> str:
    """周六 09:00 周五美股收盘复盘。"""
    from src import briefing
    load_pf_fn = getattr(briefing, "load_portfolio", None)
    if load_pf_fn is None:
        from src.advisor import load_portfolio as load_pf_fn

    ai_insight_fn = getattr(briefing, "_ai_insight", None)
    if ai_insight_fn is None:
        from src.briefing_modules.narrator import _ai_insight as ai_insight_fn

    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    md = getattr(briefing, "market_data", None)
    if md is None:
        from src import market_data as md

    vix = md.fetch_vix()
    vix_str = f"{vix['vix']:.1f}（{vix['level']}）" if vix and vix.get("vix") else "获取失败"
    spx = md.fetch_us_etf("SPY")
    qqq = md.fetch_us_etf("QQQ")
    spx_tag = f" [{spx['date'][5:]}]" if spx and spx.get("date") else ""
    qqq_tag = f" [{qqq['date'][5:]}]" if qqq and qqq.get("date") else ""
    spx_str = f"${spx['close']:.2f}（{spx['change_pct']:+.2f}%）{spx_tag}" if spx else "获取失败"
    qqq_str = f"${qqq['close']:.2f}（{qqq['change_pct']:+.2f}%）{qqq_tag}" if qqq else "获取失败"

    fetch_news_fn = getattr(briefing, "fetch_all_news", fetch_all_news)
    articles = fetch_news_fn(max_results=40)
    pf = load_pf_fn()

    filter_fn = getattr(briefing, "_filter_by_keywords", _filter_by_keywords)
    filtered = filter_fn(articles, pf, top_n=6)

    is_compact_fn = getattr(briefing, "_is_compact_style", _is_compact_style)
    news_block = (
        _build_digest_news_block(filtered, max_bullets=4)
        if is_compact_fn()
        else _fmt_news(filtered, max_items=6)
    )

    news_link_fn = getattr(briefing, "_news_link_block", _news_link_block)
    news_link_block = news_link_fn(articles)
    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:6])

    insight = ai_insight_fn(
        "周五美股收盘总结——本周美股表现如何？对下周持仓有什么影响？",
        titles_only, max_tokens=800,
    )

    from src.llm import get_last_completion_info
    model_badge = f"> 🤖 智囊模型：{get_last_completion_info()}\n"
    insight_block = f"\n🧠 **本周美股回顾**\n{insight}\n{model_badge}" if insight else ""

    from src.peer_rank import build_peer_alert
    from src.feishu_client import get_feishu_client_or_none

    peer_block = build_peer_alert(get_feishu_client_or_none())
    peer_section = f"\n{peer_block}\n" if peer_block else ""

    sat_verdict = None
    try:
        from src.strategy import judge_from_feishu
        sat_verdict = judge_from_feishu()
    except Exception:
        pass

    from src.briefing_modules.pyramid import build_pyramid_action_card
    pyramid_card = build_pyramid_action_card(
        slot="sat_morning",
        verdict=sat_verdict,
        portfolio=pf,
        vix_val=vix_str,
        compact=is_compact_fn(),
    )

    return f"""📅 **{today} 周末复盘**　|　{now.strftime('%H:%M')}

{pyramid_card}

**🇺🇸 周五美股收盘**
· 标普500：{spx_str}
· 纳斯达克100：{qqq_str}
· VIX：{vix_str}

**📰 本周要闻**
{news_block}{news_link_block}{peer_section}
{insight_block}> ☀️ 周日 20:00 推送下周前瞻"""


def _build_sun_evening() -> str:
    """周日 20:00 周报 —— 仓位盘点 + 周末要闻 + 宏观回顾 + 下周关注。"""
    from src import briefing
    load_pf_fn = getattr(briefing, "load_portfolio", None)
    if load_pf_fn is None:
        from src.advisor import load_portfolio as load_pf_fn

    save_snap_fn = getattr(briefing, "_save_snapshot", _save_snapshot)

    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    pf = load_pf_fn()

    weekly_ret_fn = getattr(briefing, "_build_weekly_return", _build_weekly_return)
    weekly_return = weekly_ret_fn(pf)

    from src.strategy import judge_from_feishu
    verdict = judge_from_feishu()
    health = verdict.get("health_report", "")
    health = health.replace("【增量资金优先方向】", "").strip()

    fetch_news_fn = getattr(briefing, "fetch_all_news", fetch_all_news)
    weekend_articles = fetch_news_fn(max_results=40)

    filter_fn = getattr(briefing, "_filter_by_keywords", _filter_by_keywords)
    weekend_filtered = filter_fn(weekend_articles, pf, top_n=6)
    weekend_titles = " ".join(_clean_html(a.get("title", "")) for a in weekend_filtered[:6])

    weekend_news_summary = ""
    if weekend_titles.strip():
        try:
            from src.llm import chat_completion_with_fallback
            raw_summary, _ = chat_completion_with_fallback(
                messages=[{"role": "user", "content": f"""你是量化投资顾问。以下是周末两天的财经新闻标题。
提炼最重要的 2-3 条高价值资讯，每条约 20 字，用大白话写。

<weekend_news>
{weekend_titles[:800]}
</weekend_news>

直接输出，每条一行，格式：· xxx。不要前缀。"""}],
                max_tokens=200,
                temperature=0.3,
                thinking=False,
            )
            weekend_news_summary = _sanitize_llm_output(raw_summary)
        except Exception:
            pass
    weekend_block = f"\n📅 **周末要闻复盘**\n{weekend_news_summary}\n" if weekend_news_summary else ""

    news_link_fn = getattr(briefing, "_news_link_block", _news_link_block)
    weekend_link_block = news_link_fn(weekend_articles)

    from src.macro_calendar import (
        fetch_past_calendar,
        fetch_upcoming_calendar,
    )
    past_events = fetch_past_calendar(min_impact="Medium", days_behind=7)
    future_events = filter_portfolio_relevant(
        fetch_upcoming_calendar(min_impact="Medium", days_ahead=7)
    )
    future_macro_display = format_macro_signal_line(future_events, scope="下周")

    global_news_text = ""
    try:
        from src.global_news import _build_global_news_brief
        gnb = _build_global_news_brief()
        if gnb:
            global_news_text = gnb
    except Exception:
        pass

    earnings_block = ""
    try:
        from src.earnings_calendar import fetch_weekly_earnings, format_weekly_earnings
        wk_earnings = fetch_weekly_earnings(days_ahead=7)
        if wk_earnings:
            earnings_block = format_weekly_earnings(wk_earnings)
    except Exception:
        pass

    past_summary = "\n".join(
        f"· {e.get('date','')} {e.get('title','')} [{e.get('stars','')}]"
        for e in past_events[:8]
    ) if past_events else "(本周无重大宏观事件)"

    future_summary = calendar_context_for_prompt(future_events, pf) if future_events else ""

    llm_block = ""
    if past_events or future_events or global_news_text or weekend_news_summary:
        try:
            from src.llm import chat_completion_with_fallback
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
                "  · （推演2：触发条件→影响→方向）\n"
                + _MACRO_AI_RULE
            )

            from src.prompt_templates import build_analysis_prompt
            prompt = build_analysis_prompt(
                role="你是量化投资顾问。请根据以下信息产出周报的宏观回顾和下周防守要点。",
                holdings_text=f"{weekly_return}\n\n仓位安全垫:\n{health[:300]}",
                macro_text=f"已发生事件:\n{past_summary}\n\n未来日历:\n{future_summary}",
                news_text=f"周末要闻:\n{weekend_news_summary[:300] if weekend_news_summary else '(无)'}\n\n"
                          f"下周财报:\n{earnings_block[:300] if earnings_block else '(无)'}\n\n"
                          f"国际快讯:\n{global_news_text[:400] if global_news_text else '(无)'}",
                extra_rules=extra_rules,
            )
            raw_llm_block, _ = chat_completion_with_fallback(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1500,
                temperature=0.3,
                thinking=True,
            )
            llm_block = _sanitize_llm_output(raw_llm_block.strip())
        except Exception:
            pass

    from src.llm import get_last_completion_info
    model_badge = f"\n> 🤖 智囊模型：{get_last_completion_info()}" if llm_block else ""

    card = f"""📅 **{today} 周报**

{weekly_return}

**🛡️ 当前仓位安全垫分布**
{health}

{weekend_block}{weekend_link_block}
{llm_block}{model_badge}

{earnings_block}

{future_macro_display}"""

    metrics = _extract_metrics_from_verdict(verdict) if verdict else {}
    _body_for_sig = "\n".join(card.split("\n")[1:60])
    save_snap_fn("sun_evening", _make_signature(_body_for_sig), metrics)
    return card


BRIEFINGS = {
    "morning":      ("☀️ 早间简报", _build_morning),
    "midday":       ("🌤️ 午间快讯", _build_midday),
    "closing":      ("⚡ 收盘前指令", _build_closing),
    "evening":      ("🌆 夜盘前瞻", _build_evening),
    "sat_morning":  ("📅 周末复盘", _build_sat_morning),
    "sun_evening":  ("📅 下周前瞻", _build_sun_evening),
    # 兼容历史（已合流到 morning，不再有独立 asia_open 入口）
    "asia_open":    ("🌏 亚太早盘", _build_morning),
}

_CN_GATED = ["midday", "closing"]
