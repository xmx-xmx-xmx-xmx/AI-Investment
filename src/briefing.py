"""
多时段简报系统 —— P1 核心模块。

六个时段，按市场日历智能熔断：
  morning     08:30 美股收盘复盘 + AI 解读
  midday      11:30 午间快讯（A股异动 + 要闻）
  closing     14:30 收盘前全资产偏离度 + Python 死结论
  evening     20:00 夜盘前瞻 + AI 解读
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


def _portfolio_value_summary() -> str:
    """生成持仓市值+收益率一览表。"""
    try:
        pf = load_portfolio()
        rb = calculate_rebalance(pf)
    except Exception:
        return "持仓数据暂不可用"

    lines = ["**💰 当前持仓**"]
    for pos in rb["positions"]:
        pnl = pos["pnl_pct"]
        arrow = "🔺" if pnl > 0 else "🔻" if pnl < 0 else "➖"
        lines.append(
            f"· {pos['name']}: ¥{pos['market_value']:,.0f}　{arrow} {pnl:+.1f}%"
        )
    return "\n".join(lines)


def _ai_insight(context: str, news_titles: str, max_tokens: int = 400,
                macro_context: str = "") -> str:
    """LLM 生成持仓+新闻解读（可结合宏观日历）。"""
    if not news_titles.strip():
        return ""

    pf_summary = _build_portfolio_summary()

    # ── 宏观日历区块 ──
    macro_block = ""
    if macro_context:
        macro_block = f"""
<macro_calendar>
{macro_context}
</macro_calendar>"""

    prompt = f"""<system_role>
你是一位量化投资顾问。你的任务不是预测市场，而是把当天的财经新闻
与投资者的真实持仓对照，给出有洞察力的解读。
</system_role>

<hard_rules>
- 只看新闻标题，推测对持仓大类可能的影响
- 如果某条新闻明显利好或利空某类资产，直接说出来，并标注"机会"或"风险"
- 必须将新闻精准映射到下方持有的具体大类：美股资产、A股资产、港股资产、避险商品、固收资产
- 用大白话写，禁止术语。像在给不懂金融的朋友发微信。
- 150-200 字。
- 如果当日有宏观经济日历事件（见 macro_calendar），必须结合该事件分析对持仓的短期影响，
  并标注⚠️波动预警。例如：今晚CPI数据公布 → "今晚CPI数据可能引发美股波动，你的纳指持仓短期承压"
</hard_rules>

<holdings_summary>
{pf_summary}
</holdings_summary>
{macro_block}
<news context="{context}">
{news_titles[:1000]}
</news>

<output_instruction>
输出 150-200 字的中文解读。直接输出正文，不要前缀。
如果你的判断是利空某类资产——直接说"这对你的XX持仓是风险，因为..."。
如果利好——直接说"这对你的XX持仓是机会，因为..."。
如果新闻互相矛盾（比如一边说美联储要加息、一边说可能要降息），指出这个矛盾，
并建议"以不变应万变，按纪律执行"。
</output_instruction>"""

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
    """08:30 美股收盘复盘 + AI 解读（含宏观日历）。"""
    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    vix = market_data.fetch_vix()
    vix_str = f"{vix['vix']:.1f}（{vix['level']}）" if vix and vix.get("vix") else "获取失败"
    spx = market_data.fetch_us_etf("SPY")
    qqq = market_data.fetch_us_etf("QQQ")
    spx_str = f"${spx['close']:.2f}（{spx['change_pct']:+.2f}%）" if spx else "获取失败"
    qqq_str = f"${qqq['close']:.2f}（{qqq['change_pct']:+.2f}%）" if qqq else "获取失败"

    articles = fetch_all_news(max_results=50)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=8)
    news_block = _fmt_news(filtered, max_items=8)
    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:8])

    # ── 宏观日历 ──
    macro_events = fetch_today_calendar(min_impact="Medium")
    macro_display = format_calendar_for_brief(macro_events)
    macro_prompt = calendar_context_for_prompt(macro_events, pf)

    insight = _ai_insight("隔夜要闻", titles_only, macro_context=macro_prompt)
    focus = _sent_truncate(
        _ai_insight(
            "隔夜要闻——请给出今天白天最值得关注的1-2件事",
            titles_only, max_tokens=200, macro_context=macro_prompt,
        ),
        max_chars=180,
    )

    insight_block = f"\n🧠 **AI 解读**\n{insight}\n" if insight else ""
    focus_block = f"\n🔮 **今日重点关注**\n{focus}\n" if focus else ""
    macro_block = f"\n{macro_display}\n" if macro_display else ""

    return f"""☀️ **{today} 早间简报**　|　{now.strftime('%H:%M')}

**🇺🇸 美股收盘**
· 标普500：{spx_str}
· 纳斯达克100：{qqq_str}
· VIX：{vix_str}

**📰 隔夜要闻**
{news_block}{macro_block}{insight_block}{focus_block}> 📐 盘中 14:30 推送收盘前操作指令"""


def _build_midday() -> str:
    """11:30 午间快讯。需要 A 股开市。"""
    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    articles = fetch_all_news(max_results=40)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=8)
    news_block = _fmt_news(filtered, max_items=8)

    value_summary = _portfolio_value_summary()

    return f"""🌤️ **{today} 午间快讯**　|　{now.strftime('%H:%M')}

**📰 上午要闻**
{news_block}

{value_summary}
**💡 下午关注**
· A 股午后走势
· 14:30 收盘前终极操作指令
· 若上午大幅异动，提前检查飞书底仓表"""


def _build_closing() -> str:
    """14:30 收盘前全资产偏离度 + Python 策略中枢死结论。"""
    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    from src.strategy import judge_from_feishu
    verdict = judge_from_feishu()

    articles = fetch_all_news(max_results=30)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=5)
    news_block = _fmt_news(filtered, max_items=5)

    signal_lines = ""
    for s in verdict["signals"]:
        cooldown = f" ⏳{s['cooldown_status'][:60]}" if s.get("cooldown_status") else ""
        override_raw = s.get("override") or ""
        override = _sent_truncate(override_raw, max_chars=120) if override_raw else ""
        override = f" ⚠️{override}" if override else ""
        signal_lines += f"· {s['signal_label']} **{s['asset_class']}**（{s['deviation_pct']}）{override}{cooldown}\n"

    value_summary = _portfolio_value_summary()

    return f"""⚡ **{today} 收盘前操作指令**　|　{now.strftime('%H:%M')}

**📊 Python 策略中枢结论**
{verdict['command']}

**逐类指令**
{signal_lines}
{value_summary}

**📰 午间要闻**
{news_block}

🔔 总市值 ¥{verdict['total_value']:,.2f}　|　买入参考 100-200 元/次　|　长底仓只买不卖

> 以上结论由量化系统计算，仅供参考，不构成投资建议"""


def _build_evening() -> str:
    """20:00 夜盘前瞻 + AI 解读晚间新闻。需要美股开市。"""
    skip_reason = _should_skip(["us"])
    if skip_reason is not None:
        _skip_msg(skip_reason, "夜盘前瞻")
        return "SKIP"

    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    vix = market_data.fetch_vix()
    vix_str = f"{vix['vix']:.1f}（{vix['level']}）" if vix and vix.get("vix") else "获取失败"

    articles = fetch_all_news(max_results=40)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=8)
    news_block = _fmt_news(filtered, max_items=8)
    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:8])

    insight = _ai_insight("今日晚间要闻", titles_only)
    focus = _sent_truncate(
        _ai_insight("今日晚间要闻——请给出今晚最值得关注的1-2件事", titles_only, max_tokens=200),
        max_chars=180,
    )

    insight_block = f"\n🧠 **AI 解读**\n{insight}\n" if insight else ""
    focus_block = f"\n🔮 **今晚关注**\n{focus}\n" if focus else ""

    return f"""🌆 **{today} 夜盘前瞻**　|　{now.strftime('%H:%M')}

**🇺🇸 盘前快照**
· VIX：{vix_str}

**📰 今日要闻**
{news_block}
{insight_block}{focus_block}> ☀️ 明早 08:30 推送美股收盘复盘"""


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


def _build_sun_evening() -> str:
    """周日 20:00 周末宏观总结 + 周一前瞻（含本周宏观日历）。"""
    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    articles = fetch_all_news(max_results=50)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=8)
    news_block = _fmt_news(filtered, max_items=8)
    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:8])

    # ── 本周宏观日历（未来7天） ──
    from src.macro_calendar import fetch_upcoming_calendar, format_calendar_for_brief
    macro_events = fetch_upcoming_calendar(min_impact="High", days_ahead=7)
    macro_display = format_calendar_for_brief(macro_events)
    macro_prompt = calendar_context_for_prompt(macro_events, pf)

    insight = _sent_truncate(
        _ai_insight(
            "周末重大宏观新闻总结——请提炼出对下周市场影响最大的1-2个事件，并给出周一持仓建议",
            titles_only, max_tokens=300, macro_context=macro_prompt,
        ),
        max_chars=250,
    )

    insight_block = f"\n🧠 **周末宏观总结**\n{insight}\n" if insight else ""
    macro_block = f"\n{macro_display}\n" if macro_display else (
        "\n**🔮 周一关注**\n· 本周重磅数据（CPI、非农、央行决议等）\n"
    )

    return f"""📅 **{today} 周末前瞻**　|　{now.strftime('%H:%M')}

**📰 周末要闻**
{news_block}{macro_block}{insight_block}**🔮 周一关注**
· 亚太市场开盘走势
· 当前全市场处于左侧下跌通道，按纪律执行即可

> ☀️ 明早 08:30 推送美股收盘复盘"""


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
