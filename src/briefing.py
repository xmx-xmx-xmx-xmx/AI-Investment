"""
多时段简报系统 —— P1 核心模块。

四个时段，四种内容：
  09:00  美股昨夜收盘复盘 + 亚太今日前瞻
  12:00  午间快讯（A股/港股午盘异动 + 要闻）
  14:30  收盘前快讯 + Python 硬核操作指令
  19:00  夜盘前瞻（美股期货 + 晚间重大事件）

全部资讯来自免费源（金十数据 + 华尔街见闻），零成本。

用法：
  python -m src.briefing 09:00    # 早间复盘
  python -m src.briefing 12:00    # 午间快讯
  python -m src.briefing 14:30    # 收盘前指令
  python -m src.briefing 19:00    # 夜盘前瞻
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone, timedelta

from src.news_fetcher import fetch_all_news, _filter_by_keywords
from src.advisor import load_portfolio
from src.notify import FeishuPusher
from src import market_data

logger = logging.getLogger(__name__)

tz_cn = timezone(timedelta(hours=8))


# ═══════════════════════════════════════════════════════════════
# 通用：推送到飞书群
# ═══════════════════════════════════════════════════════════════

def _push(title: str, content: str) -> bool:
    pusher = FeishuPusher()
    if not pusher.is_configured():
        logger.warning("Webhook 未配置，只打印")
        print(f"\n═══ {title} ═══")
        print(content)
        return False
    return pusher.send_card(title, content)


# ═══════════════════════════════════════════════════════════════
# 时段简报生成
# ═══════════════════════════════════════════════════════════════

def _build_morning_briefing() -> str:
    """09:00 美股昨夜收盘复盘 + 亚太今日前瞻。"""
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
    news_lines = "\n".join(f"- {a['title'][:90]}" for a in filtered[:8])

    return f"""☀️ **{today} 早间简报**　|　{now.strftime('%H:%M')}

**🇺🇸 美股昨夜收盘**
· 标普500 ETF：{spx_str}
· 纳斯达克100 ETF：{qqq_str}
· VIX 恐惧温度计：{vix_str}

**📰 隔夜要闻**
{news_lines or '（暂无）'}

**🌏 今日关注**
· {today} 亚太市场开盘走势
· 关注美股指期货亚洲盘动向
· 若昨夜美股大跌，开盘前做好心理准备

> 📐 盘中 14:30 会推送收盘前操作指令"""


def _build_midday_briefing() -> str:
    """12:00 午间快讯。"""
    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    articles = fetch_all_news(max_results=40)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=8)
    news_lines = "\n".join(f"- {a['title'][:90]}" for a in filtered[:8])

    return f"""🌤️ **{today} 午间快讯**　|　{now.strftime('%H:%M')}

**📰 上午要闻**
{news_lines or '（暂无）'}

**💡 下午关注**
· A 股午后走势、是否有异动
· 2:30 前会推送收盘前终极操作指令
· 如果上午有大幅波动，提前打开飞书底仓表检查偏离度"""


def _build_preclose_briefing() -> str:
    """14:30 收盘前快讯 + Python 硬核操作指令。

    这是全天最重要的简报——调用 strategy.py 拿死结论。
    """
    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    # Python 策略中枢
    from src.strategy import judge_from_feishu
    verdict = judge_from_feishu()

    articles = fetch_all_news(max_results=30)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=5)
    news_lines = "\n".join(f"- {a['title'][:90]}" for a in filtered[:5])

    # 逐类信号
    signal_lines = ""
    for s in verdict["signals"]:
        cooldown = f" ⏳{s['cooldown_status'][:50]}" if s.get("cooldown_status") else ""
        override = f" ⚠️{s['override'][:60]}" if s.get("override") else ""
        signal_lines += f"· {s['signal_label']} **{s['asset_class']}**（{s['deviation_pct']}）{override}{cooldown}\n"

    return f"""⚡ **{today} 收盘前操作指令**　|　{now.strftime('%H:%M')}

**🐍 Python 策略中枢死结论——不可推翻**
{verdict['command']}

**📊 逐类指令**
{signal_lines}

**📰 午间要闻**
{news_lines or '（暂无）'}

**🔔 操作提醒**
· 总市值：¥{verdict['total_value']:,.2f}
· VIX 恐惧温度计已在上方早间简报中推送
· 买入参考金额：每次 100-200 元
· 长底仓标的：只买不卖

> ⚠️ 以上为 Python 硬核裁定，AI 只负责翻译，不负责决策"""


def _build_evening_briefing() -> str:
    """19:00 夜盘前瞻。"""
    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    # 美股期货
    spx = market_data.fetch_us_etf("SPY")
    qqq = market_data.fetch_us_etf("QQQ")
    spx_str = f"${spx['close']:.2f}" if spx else "获取失败"
    qqq_str = f"${qqq['close']:.2f}" if qqq else "获取失败"

    vix = market_data.fetch_vix()
    vix_str = f"{vix['vix']:.1f}（{vix['level']}）" if vix and vix.get("vix") else "获取失败"

    articles = fetch_all_news(max_results=40)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=8)
    news_lines = "\n".join(f"- {a['title'][:90]}" for a in filtered[:8])

    return f"""🌆 **{today} 夜盘前瞻**　|　{now.strftime('%H:%M')}

**🇺🇸 美股盘前**
· 标普500 ETF 昨日收盘：{spx_str}
· 纳斯达克100 ETF 昨日收盘：{qqq_str}
· VIX 恐惧温度计：{vix_str}

**📰 晚间要闻**
{news_lines or '（暂无）'}

**🔮 今晚关注**
· 是否有重磅数据公布（CPI、非农、美联储讲话等）
· 美股期货夜盘走势
· 若 VIX > 25，为明天亚太市场波动做好心理预期

> ☀️ 明早 09:00 会推送美股收盘复盘"""


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

BRIEFINGS = {
    "09:00": ("☀️ 早间简报", _build_morning_briefing),
    "12:00": ("🌤️ 午间快讯", _build_midday_briefing),
    "14:30": ("⚡ 收盘前指令", _build_preclose_briefing),
    "19:00": ("🌆 夜盘前瞻", _build_evening_briefing),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in BRIEFINGS:
        print("用法: python -m src.briefing [09:00|12:00|14:30|19:00]")
        sys.exit(1)

    slot = sys.argv[1]
    title, builder = BRIEFINGS[slot]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    print()
    print("=" * 50)
    print(f"   📨 {title}")

    logger.info("加载持仓 + 抓取新闻...")
    card = builder()

    logger.info("推送到飞书群...")
    _push(title, card)

    print()
    print("   ✅ 推送完成")
    print("=" * 50)


if __name__ == "__main__":
    main()
