"""
多时段简报系统 —— P1 核心模块。

六个时段，按市场日历智能熔断：
  morning     08:30 美股收盘复盘 + AI 解读
  midday      12:00 亚太午盘收盘快讯（A股/港股/日韩台）
  closing     14:30 A 股收盘前 30 分钟策略防御带
  evening     21:00 夜盘前瞻 + 恒指最终收盘 + AI 解读
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
    """格式化新闻列表。短标题保留全文；英文标题自动翻译为中文。"""
    items = news_list[:max_items]
    if not items:
        return "（暂无）"

    # ── 批量检测 & 翻译英文标题 ──
    _translate_english_titles(items)

    lines = []
    for a in items:
        title = _clean_html(a.get("title", ""))
        if not title:
            continue
        if len(title) <= 200:
            display = title
        else:
            display = title[:197] + "…"
        source = a.get("source", "")
        lines.append(f"· {display}  _{source}_")
    return "\n".join(lines)


def _needs_chinese_translation(text: str) -> bool:
    """检测标题是否以非中文为主（需翻译为中文）。"""
    cleaned = _clean_html(text)
    if len(cleaned) < 8:
        return False
    # 统计 CJK 字符和 ASCII 字母
    cjk = sum(1 for c in cleaned if '一' <= c <= '鿿')
    ascii_alpha = sum(1 for c in cleaned if c.isascii() and c.isalpha())
    total = len(cleaned)
    # 大量 ASCII 字母 + 极少 CJK → 英文标题
    return ascii_alpha > total * 0.35 and cjk < 4


def _translate_english_titles(items: list[dict]) -> None:
    """检测并批量翻译英文标题（原地修改 items 的 title 字段）。"""
    to_translate: list[int] = []
    texts: list[str] = []
    for i, a in enumerate(items):
        title = _clean_html(a.get("title", ""))
        if _needs_chinese_translation(title):
            to_translate.append(i)
            texts.append(title)

    if not to_translate:
        return

    try:
        # 🔥 2026-07-07 容灾改造：翻译切到 Qwen3-32B（更轻更快），60s 短超时
        from src.llm import get_translation_client, get_translation_model
        client = get_translation_client()
        if client is None:
            return

        joined = "\n".join(f"[{j+1}] {t}" for j, t in enumerate(texts))
        prompt = (
            "将以下英文新闻标题翻译为简洁的中文（20-40字），保留编号格式 [N] 中文：\n"
            + joined
        )
        resp = client.chat.completions.create(
            model=get_translation_model(), max_tokens=300, temperature=0.1,
            messages=[{"role": "user", "content": prompt}],
        )
        translated = resp.choices[0].message.content.strip()
        # 解析 [N] 中文格式
        import re
        for line in translated.split("\n"):
            m = re.match(r'\[(\d+)\]\s*(.+)', line.strip())
            if m:
                idx = int(m.group(1)) - 1
                cn = m.group(2).strip()
                if 0 <= idx < len(to_translate):
                    i = to_translate[idx]
                    items[i]["title"] = f"[译] {cn}（{items[i].get('title', '')[:40]}）"
    except Exception:
        pass  # 翻译失败不影响主流程，保留原标题


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


def _build_trade_summary() -> str:
    """读取最近 5 天的交易流水，供 AI 判断是否近期已操作。"""
    try:
        from src.feishu_client import FeishuClient
        client = FeishuClient()
        records = client.list_records("交易流水表")
        from datetime import datetime, timezone, timedelta
        tz_cn = timezone(timedelta(hours=8))
        now = datetime.now(tz_cn)
        recent = []
        for r in records:
            ts = r.get("交易时间", "")
            try:
                ts = float(ts)
                if ts > 1e12:
                    ts /= 1000
                dt = datetime.fromtimestamp(ts, tz=tz_cn)
            except (ValueError, TypeError):
                continue
            if (now - dt).days <= 5:
                product = r.get("产品名称", "未知")
                amount = r.get("交易金额", 0)
                action = r.get("买卖方向", "")
                if isinstance(action, list):
                    action = action[0] if action else ""
                recent.append(f"{dt.strftime('%m/%d')} {action} {product} ¥{amount}")
        if recent:
            return "近5日交易记录:\n" + "\n".join(recent[-10:])
    except Exception:
        pass
    return ""


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


def _build_us_futures_block() -> str:
    """美股期货实时行情区块。14:30 盘前风向 + 21:00 实时期货。"""
    lines = []
    for sym, name in [("NQ", "纳指期货"), ("ES", "标普期货")]:
        try:
            data = market_data.fetch_nq_futures(sym)
            if data and data.get("change_pct") is not None:
                a = "🔺" if data["change_pct"] > 0 else "🔻" if data["change_pct"] < 0 else "➖"
                time_str = f" {data['time']}" if data.get("time") else ""
                lines.append(f"· {name}: {data['price']:,.2f}　{a}{data['change_pct']:+.2f}%{time_str}")
        except Exception:
            pass
    if not lines:
        return ""
    return "\n".join(lines)


def _build_sector_rotation_block(market_filter: str = "all") -> str:
    """板块轮动追踪区块。温差 = 行业涨跌幅 - 大盘涨跌幅。

    Args:
        market_filter: "all" 全部 / "hk_cn" 仅港股+A股（午间用，US为隔夜数据）
    """
    try:
        deltas = market_data.fetch_sector_deltas()
    except Exception:
        return ""

    if not deltas:
        return ""

    lines = ["🔄 **板块轮动**"]

    # 分组: 美股 → 港股 → A股
    groups = [("美股阵营", "us"), ("港股阵营", "hk"), ("A股阵营", "cn")]
    for group_name, mk in groups:
        if market_filter == "hk_cn" and mk == "us":
            continue
        items = [d for d in deltas if d["market"] == mk]
        if not items:
            continue
        # 只展示有温差或信号的
        shown = [d for d in items if abs(d["delta"]) >= 0.5 or d["signal"]]
        if not shown:
            continue
        for d in shown:
            da = "🔺" if d["delta"] > 0 else "🔻" if d["delta"] < 0 else "➖"
            sig = f" {d['signal']}" if d["signal"] else ""
            lines.append(
                f"· {d['label']}：行业{d['sector_pct']:+.1f}%　|　"
                f"大盘{d['benchmark_pct']:+.1f}%　|　温差 {da}{d['delta']:+.1f}%{sig}"
            )

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _is_fund_pos(pos: dict) -> bool:
    """判断持仓是否为场外基金（优先用 investment_vehicle 字段）。"""
    vehicle = pos.get("investment_vehicle", "")
    if vehicle:
        return vehicle == "场外基金"
    # 兜底：代码格式推断
    code = pos.get("code", "")
    if not code:
        return False
    if code.isdigit() and len(code) == 6:
        if not code.startswith(("51", "56", "58", "159", "16")):
            return True
    return False


# ═══════════════════════════════════════════════════════════════
# 场外基金→指数实时穿透映射（白天用指数涨跌估算基金变动）
# ═══════════════════════════════════════════════════════════════

# 关键词 → (数据源, 代码, 折扣系数)
# 折扣系数：联接基金通常有跟踪误差，按 0.95 折算
_FUND_INDEX_MAP: list[tuple[list[str], str, str, float]] = [
    (["纳斯达克", "纳指"], "us_index", "^IXIC", 0.95),
    (["标普500", "标普"], "us_index", "^GSPC", 0.95),
    (["港股通互联网", "恒生互联网"], "hk_spot", "HSTECH", 0.90),
    (["港股通红利", "恒生红利"], "hk_spot", "HSI", 0.85),
    (["沪港深"], "hk_spot", "HSI", 0.80),
    (["上海金", "黄金"], "us_etf", "GLD", 0.90),
    (["红利低波", "红利"], "cn_index", "000922", 0.90),
]

# 系数	含义
# 0.95	跟踪误差 ~5%。联接基金持有现金、外汇波动、管理费损耗
# 0.90	跟踪误差 ~10%。港股通有汇率+额度限制，红利策略偏离度更大
# 0.85	跟踪误差 ~15%。红利策略跟恒生不完全同向
# 0.80	跟踪误差 ~20%。沪港深含 A 股，恒生仅是部分参考

# 缓存：基金代码 → 估算涨跌幅（当天有效）
_fund_estimate_cache: dict[str, float] = {}
_ESTIMATE_CACHE_DATE = ""


def _estimate_fund_realtime_pct(code: str, name: str) -> float | None:
    """根据基金名称关键词，映射到对应指数，获取实时涨跌作为穿透估算。

    Returns:
        估算涨跌幅（%），无匹配返回 None
    """
    global _fund_estimate_cache, _ESTIMATE_CACHE_DATE
    today_str = datetime.now(tz_cn).strftime("%Y%m%d")
    if _ESTIMATE_CACHE_DATE != today_str:
        _fund_estimate_cache = {}
        _ESTIMATE_CACHE_DATE = today_str

    if code in _fund_estimate_cache:
        return _fund_estimate_cache[code]

    for keywords, source, ticker, ratio in _FUND_INDEX_MAP:
        if any(kw in name for kw in keywords):
            try:
                pct = None
                if source == "us_index":
                    data = market_data.fetch_us_index(ticker)
                    if data:
                        pct = data["change_pct"]
                elif source == "us_etf":
                    data = market_data.fetch_us_etf(ticker)
                    if data:
                        pct = data["change_pct"]
                elif source == "hk_spot":
                    import akshare as _ak
                    df = _ak.stock_hk_index_spot_sina()
                    target_name = {"HSTECH": "恒生科技指数", "HSI": "恒生指数"}.get(ticker, ticker)
                    rows = df[df['名称']==target_name]
                    if len(rows)>0:
                        pct = float(rows.iloc[0]['涨跌幅'])
                elif source == "cn_index":
                    import akshare as _ak
                    import os as _os
                    for _k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','all_proxy','ALL_PROXY'):
                        _os.environ.pop(_k, None)
                    df = _ak.stock_zh_index_daily_tx(symbol=f'sh{ticker}')
                    if len(df) >= 2:
                        prev = float(df['close'].iloc[-2])
                        today = float(df['close'].iloc[-1])
                        pct = round((today-prev)/prev*100, 2)

                if pct is not None:
                    estimate = round(pct * ratio, 2)
                    _fund_estimate_cache[code] = estimate
                    return estimate
            except Exception:
                pass
            break  # 匹配到一个映射就停，不继续尝试

    _fund_estimate_cache[code] = None  # 标记已查过
    return None


def _exchange_rate_footnote(exchange_rates: dict | None = None) -> str:
    """生成汇率折算脚注，仅当有非 CNY 持仓时显示。"""
    if not exchange_rates or len(exchange_rates) <= 1:  # 只有 CNY 时跳过
        return ""
    today_str = datetime.now(tz_cn).strftime("%Y-%m-%d")
    parts = []
    for cur, rate in sorted(exchange_rates.items()):
        if cur == "CNY":
            continue
        parts.append(f"{cur}/CNY={rate:.4f}")
    if not parts:
        return ""
    return f"\n\n*汇率折算基准日：{today_str}　({'　'.join(parts)})"


def _portfolio_value_summary(label: str = "auto") -> str:
    """生成持仓市值+收益率一览表（按投资载体分组，HKD/USD 自动换算为 CNY）。

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

    # ── 盘中：为 ETF/个股抓取实时涨跌（避免用飞书缓存的昨日数据）──
    if label in ("midday", "today"):
        from src import market_data as _md
        for pos in rb["positions"]:
            vehicle = pos.get("investment_vehicle", "")
            if vehicle not in ("场内ETF", "个股"):
                continue
            code = pos.get("code", "")
            if not code:
                continue
            try:
                if code.isdigit() and len(code) == 5:
                    data = _md.fetch_hk_stock(code)
                elif code.isdigit() and len(code) == 6:
                    data = _md.fetch_cn_etf(code)
                elif code.isalpha():
                    data = _md.fetch_us_etf(code)
                else:
                    continue
                if data and data.get("change_pct") is not None:
                    pos["daily_change_pct"] = data["change_pct"]
            except Exception:
                pass

    # ── 按投资载体分组 ──
    VEHICLE_ORDER = [
        ("场外基金", "📦 场外基金"),
        ("场内ETF", "📊 场内ETF"),
        ("个股", "🏢 个股"),
    ]
    VEHICLE_ICONS = {
        "场外基金": "📦",
        "场内ETF": "📊",
        "个股": "🏢",
    }

    by_vehicle: dict[str, list[dict]] = {}
    other_positions: list[dict] = []
    for pos in rb["positions"]:
        vehicle = pos.get("investment_vehicle", "")
        if not vehicle or vehicle == "未知":
            other_positions.append(pos)
        else:
            by_vehicle.setdefault(vehicle, []).append(pos)

    has_groups = len(by_vehicle) >= 1

    lines = ["**💰 当前持仓**"]

    for vkey, vlabel in VEHICLE_ORDER:
        positions = by_vehicle.get(vkey, [])
        if not positions:
            continue

        if has_groups:
            # 计算该载体小计
            vtotal = sum(p["market_value"] for p in positions)
            lines.append(f"\n{vlabel}　(小计 ¥{vtotal:,.0f})")

        for pos in positions:
            pnl = pos["pnl_pct"]
            pnl_arrow = "🔺" if pnl > 0 else "🔻" if pnl < 0 else "➖"
            daily = pos.get("daily_change_pct", 0)
            daily_arrow = "🔺" if daily > 0 else "🔻" if daily < 0 else "➖"

            # 场外基金白天穿透估算
            fund_estimate = None
            if label == "midday" and _is_fund_pos(pos):
                fund_estimate = _estimate_fund_realtime_pct(
                    pos.get("code", ""), pos.get("name", "")
                )

            if label == "yesterday":
                daily_str = f"昨日{daily_arrow}{daily:+.2f}%" if daily != 0 else "暂无"
            elif label == "midday":
                if fund_estimate is not None:
                    ea = "🔺" if fund_estimate > 0 else "🔻" if fund_estimate < 0 else "➖"
                    daily_str = f"午盘{ea}{fund_estimate:+.2f}%（≈¥{pos['market_value'] * fund_estimate / 100:+.0f}）[穿透估算]"
                elif _is_fund_pos(pos):
                    daily_str = f"昨日{daily_arrow}{daily:+.2f}%" if daily != 0 else "暂无"
                else:
                    daily_amt = pos['market_value'] * daily / 100
                    daily_str = f"午盘{daily_arrow}{daily:+.2f}%（¥{daily_amt:+.0f}）" if daily != 0 else "暂无"
            else:
                daily_amt = pos['market_value'] * daily / 100
                daily_str = f"今日{daily_arrow}{daily:+.2f}%（¥{daily_amt:+.0f}）" if daily != 0 else "暂无"

            # 非 CNY 持仓显示原始币种金额
            currency = pos.get("currency", "CNY") or "CNY"
            if currency != "CNY":
                original = pos.get("market_value_original", pos["market_value"])
                sym = {"HKD": "HK$", "USD": "$", "CNY": "¥"}.get(currency, currency)
                value_line = f"¥{pos['market_value']:,.0f}（{sym}{original:,.0f}）"
            else:
                value_line = f"¥{pos['market_value']:,.0f}"

            # 资产大类标签
            cls_tag = f" [{pos['asset_class']}]" if pos.get("asset_class") else ""

            lines.append(
                f"· {pos['name']}{cls_tag}: {value_line}　{daily_str}　持仓{pnl_arrow}{pnl:+.1f}%"
            )

    # 兜底：无载体分类的持仓（如旧数据未迁移）
    if other_positions:
        if has_groups:
            lines.append("\n❓ 未分类")
        for pos in other_positions:
            pnl = pos["pnl_pct"]
            pnl_arrow = "🔺" if pnl > 0 else "🔻" if pnl < 0 else "➖"
            currency = pos.get("currency", "CNY") or "CNY"
            if currency != "CNY":
                original = pos.get("market_value_original", pos["market_value"])
                sym = {"HKD": "HK$", "USD": "$"}.get(currency, currency)
                value_line = f"¥{pos['market_value']:,.0f}（{sym}{original:,.0f}）"
            else:
                value_line = f"¥{pos['market_value']:,.0f}"
            cls_tag = f" [{pos['asset_class']}]" if pos.get("asset_class") else ""
            lines.append(f"· {pos['name']}{cls_tag}: {value_line}　持仓{pnl_arrow}{pnl:+.1f}%")

    # ── 当日总盈亏 ──
    if label != "yesterday":
        total_daily_pnl = 0.0
        for vkey, _ in VEHICLE_ORDER:
            for pos in by_vehicle.get(vkey, []):
                dcp = pos.get("daily_change_pct", 0) or 0
                total_daily_pnl += pos["market_value"] * dcp / 100
        if total_daily_pnl != 0:
            pnl_arrow = "🔺" if total_daily_pnl > 0 else "🔻"
            lines.append(f"\n💵 今日浮动盈亏：{pnl_arrow} ¥{total_daily_pnl:+,.0f}")

    # 汇率脚注
    footnote = _exchange_rate_footnote(rb.get("exchange_rates"))
    if footnote:
        lines.append(footnote)

    return "\n".join(lines)


def _build_fallback_insight(context: str, news_titles: str) -> str:
    """🔥 2026-07-07 容灾降级：LLM 超时/不可用时，用纯文本脱水摘要替代 AI 解读。

    不调任何外部 API，直接从 context 和 news_titles 中提取关键数字，
    拼成一个可读的纯数据摘要推给飞书。确保「通道必达」——宁可推少，不能不推。
    """
    # 提取 context 中 <market_data> 段的前 500 字（包含 VIX/涨跌等硬数据）
    import re
    market_snippet = ""
    if context:
        match = re.search(r"<market_data>(.*?)</market_data>", context, re.DOTALL)
        if match:
            raw = match.group(1).strip()
            market_snippet = raw[:500]

    # 提取中文新闻标题前 8 条
    cn_headlines = []
    for line in news_titles.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith("·") and len(stripped) > 10:
            cn_headlines.append(stripped[:120])
    headlines_text = "\n".join(f"  · {h}" for h in cn_headlines[:8])

    # 提取板块温差信号（context 中 🔥 或 ⚠️ 开头的行）
    sector_lines = []
    for line in context.split("\n"):
        if "⚠️" in line or "🔥" in line:
            sector_lines.append(line.strip()[:120])
    sector_text = "\n".join(sector_lines[:5])

    # 用 str.join 拼装，避免 Python 3.12+ f-string 内嵌 \n 的 SyntaxError
    parts = ["⚠️ **AI 解读暂时不可用（超时/服务忙），以下为系统自动生成的脱水数据摘要**", ""]
    if market_snippet:
        parts.append(f"**市场行情**:")
        parts.append(market_snippet)
        parts.append("")
    else:
        parts.append("*(市场数据暂缺)*")
        parts.append("")
    if sector_text:
        parts.append(f"**板块温差信号**:")
        parts.append(sector_text)
        parts.append("")
    if headlines_text:
        parts.append(f"**今日要闻标题**:")
        parts.append(headlines_text)
    else:
        parts.append("*(暂无新闻)*")
    parts.append("")
    parts.append("---")
    parts.append("> 💡 数据直接来自行情源和快讯源，未经 AI 加工。下一次简报将恢复 AI 解读。")
    return "\n".join(parts)


def _ai_insight(context: str, news_titles: str, max_tokens: int = 400,
                macro_context: str = "") -> str:
    """LLM 生成持仓+新闻解读（可结合宏观日历）。D9 重构：引入投资宪法+思维链。

    🔥 2026-07-07 容灾改造：LLM 超时/异常 → 自动降级到 _build_fallback_insight()
    """
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
            return _build_fallback_insight(context, news_titles)

        resp = client.chat.completions.create(
            model=get_llm_model(), max_tokens=max_tokens, temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        # 🔥 2026-07-14 两层降级：DeepSeek 超时 → Qwen3.6-27B 短 prompt 重试
        # Qwen3.6-27B 和 DeepSeek 不同 GPU 池，高峰不拥堵，质量远高于纯文本兜底
        logger.warning("主模型 DeepSeek 超时/异常: %s，尝试 Qwen3.6-27B 降级", str(e)[:80])

    # ── 降级层：Qwen3.6-27B 短 prompt ──
    try:
        from src.llm import get_fallback_llm_client, get_fallback_llm_model
        f_client = get_fallback_llm_client()
        if f_client is None:
            return _build_fallback_insight(context, news_titles)

        # 精简 prompt：不要宪法和思维链，只要核心数据 + 简短指令
        short_prompt = f"""你是量化投资顾问。请根据以下信息，用大白话（150-200字）给出持仓解读：
当前语境：{context[:500]}
新闻标题：{news_titles[:600]}
持仓概况：{pf_summary[:400]}
要求：提及对具体持仓大类的影响，结尾说一句最值得关注的事。直接输出正文。"""

        f_resp = f_client.chat.completions.create(
            model=get_fallback_llm_model(), max_tokens=min(max_tokens, 300),
            temperature=0.3,
            messages=[{"role": "user", "content": short_prompt}],
        )
        logger.info("Qwen3.6-27B 降级解读成功")
        return "[Qwen降级] " + f_resp.choices[0].message.content.strip()
    except Exception as e2:
        logger.warning("Qwen3.6-27B 降级也失败: %s，降到纯文本摘要", str(e2)[:100])
        return _build_fallback_insight(context, news_titles)


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
    # 早间 8:30 亚太尚未开盘，数据均为上一个交易日收盘
    market_context = _build_global_market_snapshot(prefix="上一交易日收盘")
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

    # ── 5. 雷达（仅展示信号列表，LLM 解读并入下面的综合解读）──
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
            # 🔥 2026-07-07：不再单独调 _radar_insight()，信号直接喂给下面的综合解读
            radar_block = "\n" + radar_raw if radar_raw else ""
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
    radar_snippet = radar_block[:800] if radar_block else ""
    global_snippet = global_block[:300] if global_block else ""
    pf_summary = "\n".join(f"{p.get('name','')[:12]} {p.get('asset_class','')}" for p in pf[:10]) if pf else ""
    trades = _build_trade_summary()
    full_context = f"{titles_only} {earnings_titles} {trades} {market_context[:300]} {pf_summary} {radar_snippet} {global_snippet}"

    insight = _ai_insight(
        "早间简报——请综合所有信息（隔夜新闻/昨日财报/近5日交易记录/全球市场/持仓/宏观日历/雷达信号/国际快讯），"
        "给出一段对今天持仓的综合解读，必须提及对具体持仓大类的影响。"
        "如果交易记录显示某大类近期已操作过，在建议中提醒'3天内同一大类已经操作过，按纪律等冷却期'。"
        "结尾用一句话说今天最值得关注的1-2件事。",
        full_context, macro_context=macro_prompt
    )
    insight_block = "\n🧠 **AI 综合解读**\n" + insight + "\n" if insight else ""

    # 🔥 2026-07-07：快速关注已合并到综合解读中，不再单独调 LLM
    # 原 L764-769 focus = _ai_insight("早间——请给出今天白天最值得关注的1-2件事...") 已删除

    return f"""☀️ **{today} 早间简报**　|　{now.strftime('%H:%M')}

{vix_line}
{market_block}
**📰 隔夜要闻**
{news_block}
{earnings_block}
{macro_block}
{radar_block}
{global_block}
{value_summary}
{insight_block}> 📐 上午 12:00 推送午间快讯"""


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

    # ── 日经/KOSPI/台湾（优先 .info 实时价 → 日线兜底）──
    apac_lines = []
    for ticker, name in [('^N225','日经225'), ('^KS11','韩国KOSPI'), ('^TWII','台湾加权')]:
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            info = t.info
            now_price = info.get('regularMarketPrice') or info.get('currentPrice')
            prev_close = info.get('previousClose') or info.get('regularMarketPreviousClose')
            if now_price and prev_close:
                pct = round((now_price-prev_close)/prev_close*100,2)
                arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                apac_lines.append(f"· {name}: {now_price:,.2f}　{arrow}{pct:+.2f}%（实时）")
        except Exception:
            try:
                df = yf.Ticker(ticker).history(period='5d')
                if len(df) >= 2:
                    prev = float(df['Close'].iloc[-2])
                    today = float(df['Close'].iloc[-1])
                    pct = round((today-prev)/prev*100,2)
                    arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                    apac_lines.append(f"· {name}: {today:,.2f}　{arrow}{pct:+.2f}%（收盘）")
            except Exception:
                pass
    if apac_lines:
        lines.append("\n**亚太其他（实时）**")
        lines.extend(apac_lines)

    if len(lines) == 1:
        return ""
    return "\n".join(lines)

def _build_global_market_snapshot(prefix: str = '') -> str:
    """全球市场快照。prefix=''时为各时段默认标签。"""
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
            t = yf.Ticker(ticker)
            info = t.info
            now_price = info.get('regularMarketPrice') or info.get('currentPrice')
            prev_close = info.get('previousClose') or info.get('regularMarketPreviousClose')
            if now_price and prev_close:
                pct = round((now_price-prev_close)/prev_close*100,2)
                arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                apac_lines.append(f"· {name}: {now_price:,.2f}　{arrow}{pct:+.2f}%")
        except Exception:
            try:
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
    insight = _ai_insight("午间要闻——请根据上午新闻和亚太市场表现给出对下午A股走势的1-2点观察", titles_only, max_tokens=400)
    insight_block = f"\n🧠 **午间快评**\n{insight}\n" if insight else ""

    value_summary = _portfolio_value_summary()

    # ── 板块轮动（仅港股+A股实时温差）──
    sector_rotation_block = _build_sector_rotation_block(market_filter="hk_cn")
    sector_block = f"\n{sector_rotation_block}\n" if sector_rotation_block else ""

    return f"""🌤️ **{today} 午间快讯**　|　{now.strftime('%H:%M')}

{apac_block}
{sector_block}**📰 上午要闻**
{news_block}
{value_summary}
{insight_block}
**💡 下午关注**
· 亚太市场午后走势
· 14:30 收盘前报告（15:00 场外基金截单）"""


def _build_closing() -> str:
    """14:30 A 股收盘前 30 分钟策略防御带 -- 仓位健康 + 雷达扫描 + 市场基准。"""
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

    # ── 雷达扫描（仅展示信号列表，LLM 解读并入下面的综合解读）──
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

    # ── 市场基准 ──
    market_context = _build_global_market_snapshot()
    market_block = f"\n{market_context}\n" if market_context else ""

    # ── 美股盘前风向 ──
    futures_raw = _build_us_futures_block()
    futures_block = f"\n🌙 **美股盘前风向**\n{futures_raw}\n" if futures_raw else ""

    # ── 板块轮动 ──
    sector_raw = _build_sector_rotation_block()
    sector_block = f"\n{sector_raw}\n" if sector_raw else ""

    # ── 国际 RSS ──
    global_block = ""
    try:
        from src.global_news import _build_global_news_brief
        global_news_block = _build_global_news_brief()
        global_block = f"\n{global_news_block}\n" if global_news_block else ""
    except Exception:
        pass

    # ── AI 综合解读（所有数据就绪后再调 LLM）──
    radar_snippet = radar_block[:800] if radar_block else ""
    global_snippet = global_block[:300] if global_block else ""
    futures_snippet = futures_raw[:200] if futures_raw else ""
    sector_snippet = sector_raw[:300] if sector_raw else ""
    trades = _build_trade_summary()
    full_context = f"{titles_only} {trades} {health[:300]} {market_context[:300]} {futures_snippet} {sector_snippet} {radar_snippet} {global_snippet}"
    insight = _ai_insight("午间收盘前——请综合所有信息（仓位偏离度/近5日交易记录/市场基准/雷达信号/国际快讯），给出一段收盘前的综合建议，结尾用一句话说今天最值得关注的1件事", full_context, max_tokens=400)
    insight_block = f"\n🧠 **AI 综合解读**\n{insight}\n" if insight else ""

    # 🔥 2026-07-07：快速关注已合并到综合解读中，不再单独调 LLM
    # 原 L1075-1079 focus = _ai_insight("收盘前——请给出今天剩下的时间最值得关注的1件事...") 已删除

    return f"""⚡ **{today} 收盘前指令**　|　{now.strftime('%H:%M')}　⏰ 距 15:00 截单还有 30 分钟

**📰 午间要闻**
{news_block}
{market_block}
{futures_block}{sector_block}{radar_block}
{global_block}
{health_block}
{value_summary}
{insight_block}
🔔 总市值 ¥{verdict['total_value']:,.2f}　|　买入参考 100-200 元/次　|　长底仓只买不卖{_exchange_rate_footnote(verdict.get('exchange_rates'))}

> 以上结论由量化系统计算，仅供参考，不构成投资建议"""


def _build_evening() -> str:
    """21:00 夜盘前瞻 + AI 综合解读。需要美股开市。"""
    if not is_us_market_open():
        return "SKIP"

    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    articles = fetch_all_news(max_results=40)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=8)
    news_block = _fmt_news(filtered, max_items=8)
    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:8])

    # ── 1. VIX 恐慌指数 ──
    vix_block = _build_vix_block()
    vix_line = f"\n{vix_block}\n" if vix_block else ""

    # ── 2. 美股期货实时 ──
    futures_raw = _build_us_futures_block()
    futures_block = f"\n📡 **美股期货实时**\n{futures_raw}\n" if futures_raw else ""

    # ── 3. 板块轮动 ──
    sector_raw = _build_sector_rotation_block()
    sector_block = f"\n{sector_raw}\n" if sector_raw else ""

    # ── 4. 全球市场 ──
    market_context = _build_global_market_snapshot()
    market_block = f"\n{market_context}\n" if market_context else ""

    # ── 5. 近期财报提示 ──
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

    # ── 4. 雷达扫描（仅展示信号列表，LLM 解读并入下面的综合解读）──
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
    radar_snippet = radar_block[:800] if radar_block else ""
    global_snippet = global_block[:300] if global_block else ""
    pf_summary = "\n".join(f"{p.get('name','')[:12]} {p.get('asset_class','')}" for p in pf[:10]) if pf else ""
    trades = _build_trade_summary()
    futures_snippet = futures_raw[:200] if futures_raw else ""
    sector_snippet = sector_raw[:300] if sector_raw else ""
    full_context = f"{titles_only} {trades} {earnings_titles} {futures_snippet} {sector_snippet} {market_snippet} {pf_summary} {radar_snippet} {global_snippet}"

    insight = _ai_insight(
        "今晚夜盘前瞻——请综合以下所有信息（国内新闻/近5日交易记录/国际快讯/全球市场/持仓/雷达信号/近期财报），"
        "给出一段对今晚美股和明天持仓的综合解读，必须提及对具体持仓大类的影响。"
        "结尾用一句话说今晚/明天最值得关注的1-2件事",
        full_context
    )
    insight_block = f"\n🧠 **AI 综合解读**\n{insight}\n" if insight else ""

    # 🔥 2026-07-07：快速关注已合并到综合解读中，不再单独调 LLM
    # 原 L1182-1187 focus = _ai_insight("今晚——请给出今晚/明天最值得关注的1-2件事...") 已删除

    return f"""🌆 **{today} 夜盘前瞻**　|　{now.strftime('%H:%M')}

{value_summary}
{vix_line}
{futures_block}{sector_block}{market_block}
**📰 今日要闻**
{news_block}
{earnings_block}
{radar_block}
{global_block}
{insight_block}> ☀️ 明早 08:30 推送美股隔夜收盘复盘"""


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
    "sun_evening":  ("📅 下周前瞻", _build_sun_evening),
}

# 需要 A 股开市才运行的时段
_CN_GATED = {"midday", "closing"}
# 需要美股开市才运行的时段（含晚间简报派发前检查）
_US_GATED = {"evening", "sat_morning"}


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
