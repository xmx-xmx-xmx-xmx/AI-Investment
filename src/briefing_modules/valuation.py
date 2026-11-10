"""Portfolio valuation, intraday estimation, trade summary, and returns."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
tz_cn = timezone(timedelta(hours=8))

# ═══════════════════════════════════════════════════════════════
# 场外基金→指数实时穿透映射（白天用指数涨跌估算基金变动）
# ═══════════════════════════════════════════════════════════════

# 关键词 → (数据源, 代码, 折扣系数)
_FUND_INDEX_MAP: list[tuple[list[str], str, str, float]] = [
    (["纳斯达克", "纳指"], "us_index", "^IXIC", 0.95),
    (["标普500", "标普"], "us_index", "^GSPC", 0.95),
    (["新兴市场"], "us_index", "^GSPC", 0.95),
    (["港股通互联网", "恒生互联网"], "hk_spot", "HSTECH", 0.90),
    (["港股通红利", "恒生红利"], "hk_spot", "HSI", 0.85),
    (["港股消费"], "hk_spot", "HSI", 0.80),
    (["沪港深"], "hk_spot", "HSI", 0.80),
    (["上海金", "黄金"], "us_etf", "GLD", 0.90),
    (["红利低波", "红利"], "cn_index", "000922", 0.90),
    (["节能环保", "环保", "低碳"], "cn_etf", "512580", 0.95),
    (["信用添利"], "cn_etf", "511010", 0.60),
]

# 缓存：基金代码 → 估算涨跌幅（当天有效）
_fund_estimate_cache: dict[str, float | None] = {}
_ESTIMATE_CACHE_DATE = ""


def _build_portfolio_summary(portfolio: list[dict] | None = None) -> str:
    from src import briefing
    load_pf_fn = getattr(briefing, "load_portfolio", None)
    if load_pf_fn is None:
        from src.advisor import load_portfolio as load_pf_fn

    try:
        pf = portfolio if portfolio is not None else load_pf_fn()
        from src.advisor import calculate_rebalance
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
    """读取最近 5 天的交易流水，供 AI 判断是否近期已操作。

    🔥 2026-09-05 P0 改造：本地开发不调飞书 API，返回空字符串 (CLAUDE.md L22)。
    """
    from src.feishu_client import get_feishu_client_or_none
    client = get_feishu_client_or_none()
    if client is None:
        return ""

    try:
        records = client.list_records("交易流水表")
        now = datetime.now(tz_cn)
        recent = []
        for r in records:
            status = r.get("状态", "")
            if isinstance(status, list):
                status = status[0] if status else ""
            if str(status) != "completed":
                continue

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
                action = r.get("买卖方向", "")
                if isinstance(action, list):
                    action = action[0] if action else ""
                target = r.get("转入标的", "")
                if isinstance(target, list):
                    target = target[0] if target else ""
                try:
                    amt = float(r.get("交易金额") or 0)
                except (ValueError, TypeError):
                    amt = 0.0
                day = dt.strftime("%m/%d")
                if str(action) == "convert" and str(target).strip():
                    line = f"{day} 转换 {product} → {target}"
                    if amt:
                        line += f" ¥{amt:,.2f}"
                elif amt:
                    line = f"{day} {action} {product} ¥{amt:,.2f}"
                else:
                    line = f"{day} {action} {product}"
                recent.append(line)
        if recent:
            return "近5日交易记录:\n" + "\n".join(recent[-10:])
    except Exception:
        pass
    return ""


def _fetch_fund_nav_change(code: str) -> dict | None:
    """蛋卷基金接口：按代码查真实净值涨跌（全基金覆盖）。"""
    if not code or not str(code).strip().isdigit():
        return None
    try:
        import requests
        r = requests.get(
            f"https://danjuanfunds.com/djapi/fund/{code}",
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)"},
            timeout=6,
        )
        fd = (r.json().get("data", {}) or {}).get("fund_derived", {}) or {}
        pct_raw, date_raw = fd.get("nav_grtd"), fd.get("end_date")
        if pct_raw in (None, "") or date_raw in (None, ""):
            return None
        pct = float(pct_raw)
        if math.isnan(pct):
            return None
        date_str = str(date_raw)[:10]
        today_str = datetime.now(tz_cn).strftime("%Y-%m-%d")
        return {"pct": pct, "date": date_str, "is_today": date_str == today_str}
    except Exception:
        return None


def _estimate_fund_realtime_pct(code: str, name: str, prefer_nav: bool = False) -> float | None:
    """估算场外基金今日涨跌（%）。"""
    global _fund_estimate_cache, _ESTIMATE_CACHE_DATE
    from src import briefing
    md = getattr(briefing, "market_data", None)
    if md is None:
        from src import market_data as md

    hk_open_fn = getattr(briefing, "is_hk_market_open", None)
    if hk_open_fn is None:
        from src.holiday_gate import is_hk_market_open as hk_open_fn

    cn_open_fn = getattr(briefing, "is_cn_market_open", None)
    if cn_open_fn is None:
        from src.holiday_gate import is_cn_market_open as cn_open_fn

    today_str = datetime.now(tz_cn).strftime("%Y%m%d")
    if _ESTIMATE_CACHE_DATE != today_str:
        _fund_estimate_cache = {}
        _ESTIMATE_CACHE_DATE = today_str

    cache_key = f"{code}:nav" if prefer_nav else f"{code}:est"
    if cache_key in _fund_estimate_cache:
        return _fund_estimate_cache[cache_key]

    if prefer_nav:
        nav = _fetch_fund_nav_change(code)
        if nav and nav["is_today"]:
            _fund_estimate_cache[cache_key] = nav["pct"]
            return nav["pct"]

    for keywords, source, ticker, ratio in _FUND_INDEX_MAP:
        if any(kw in name for kw in keywords):
            try:
                pct = None
                if source == "us_index":
                    data = md.fetch_us_index(ticker)
                    if data:
                        pct = data["change_pct"]
                elif source == "us_etf":
                    data = md.fetch_us_etf(ticker)
                    if data:
                        pct = data["change_pct"]
                elif source == "hk_spot":
                    if hk_open_fn():
                        from src.net_guard import import_ak
                        _ak = import_ak()
                        df = _ak.stock_hk_index_spot_sina()
                        target_name = {"HSTECH": "恒生科技指数", "HSI": "恒生指数"}.get(ticker, ticker)
                        rows = df[df['名称']==target_name]
                        if len(rows)>0:
                            pct = float(rows.iloc[0]['涨跌幅'])
                elif source == "cn_index":
                    if cn_open_fn():
                        from src.net_guard import import_ak
                        _ak = import_ak()
                        import os as _os
                        for _k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','all_proxy','ALL_PROXY'):
                            _os.environ.pop(_k, None)
                        df = _ak.stock_zh_index_daily_tx(symbol=f'sh{ticker}')
                        if len(df) >= 2:
                            prev = float(df['close'].iloc[-2])
                            today = float(df['close'].iloc[-1])
                            pct = round((today-prev)/prev*100, 2)
                elif source == "cn_etf":
                    if cn_open_fn():
                        data = md.fetch_cn_etf(ticker)
                        if data:
                            pct = data["change_pct"]

                if pct is not None:
                    if math.isnan(float(pct)):
                        _fund_estimate_cache[cache_key] = None
                        return None
                    estimate = round(float(pct) * ratio, 2)
                    _fund_estimate_cache[cache_key] = estimate
                    return estimate
            except Exception:
                pass
            break

    if any(kw in name for kw in ("债券", "债", "货币", "现金")):
        _fund_estimate_cache[cache_key] = 0.0
        return 0.0

    _fund_estimate_cache[cache_key] = None
    return None


def _exchange_rate_footnote(exchange_rates: dict | None = None) -> str:
    """生成汇率折算脚注，仅当有非 CNY 持仓时显示。"""
    if not exchange_rates or len(exchange_rates) <= 1:
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


def _short_name(name: str) -> str:
    """产品名缩短：去掉括号后缀与份额等级(A/C)，截到 8 字。"""
    import re as _re
    n = _re.sub(r"[（(].*?[)）]", "", str(name))
    n = _re.sub(r"\s*[A-Ca-c]$", "", n).strip()
    return n[:8]


def _portfolio_value_summary(label: str = "auto", portfolio: list[dict] | None = None) -> str:
    """瘦身版：只交付「本期盈亏一个数字 + 主要贡献 + 总市值」"""
    from src import briefing
    load_pf_fn = getattr(briefing, "load_portfolio", None)
    if load_pf_fn is None:
        from src.advisor import load_portfolio as load_pf_fn

    try:
        pf = portfolio if portfolio is not None else load_pf_fn()
        from src.advisor import calculate_rebalance
        rb = calculate_rebalance(pf)
    except Exception:
        return "持仓数据暂不可用"

    if label == "auto":
        now = datetime.now(tz_cn)
        if now.hour < 12:
            label = "yesterday"
        elif now.hour >= 20:
            label = "today"
        else:
            label = "midday"

    positions = rb.get("positions", [])
    if not positions:
        return ""

    if label in ("midday", "today"):
        md = getattr(briefing, "market_data", None)
        if md is None:
            from src import market_data as md
        for pos in positions:
            vehicle = pos.get("investment_vehicle", "")
            if vehicle not in ("场内ETF", "个股"):
                continue
            code = pos.get("code", "")
            if not code:
                continue
            try:
                if code.isdigit() and len(code) == 5:
                    data = md.fetch_hk_stock(code)
                elif code.isdigit() and len(code) == 6:
                    data = md.fetch_cn_etf(code)
                elif code.isalpha():
                    data = md.fetch_us_etf(code)
                else:
                    continue
                if data and data.get("change_pct") is not None:
                    pos["daily_change_pct"] = data["change_pct"]
            except Exception:
                pass

    contributions: list[tuple[str, float]] = []
    total_pnl = 0.0
    pending: list[str] = []
    for pos in positions:
        name = pos.get("name", "?")
        mv = pos.get("market_value", 0) or 0
        vehicle = pos.get("investment_vehicle", "")
        pct = None

        if label == "yesterday":
            pct = pos.get("daily_change_pct")
        elif vehicle in ("场内ETF", "个股"):
            pct = pos.get("daily_change_pct")
        else:
            pct = _estimate_fund_realtime_pct(
                pos.get("code", ""), name,
                prefer_nav=(label == "today"),
            )

        if pct is None:
            pending.append(name)
            continue
        amt = mv * float(pct) / 100
        total_pnl += amt
        contributions.append((name, amt))

    day_label = "昨日" if label == "yesterday" else "今日"
    src_note = {"midday": "（盘中估算）", "today": "（净值确认）", "yesterday": "（实际）"}.get(label, "")

    lines = ["**💰 持仓速览**"]
    if contributions:
        arrow = "🔺" if total_pnl > 0 else "🔻" if total_pnl < 0 else "➖"
        lines.append(f"💵 {day_label}盈亏{src_note}：{arrow} ¥{total_pnl:+,.0f}")
        ranked = sorted(contributions, key=lambda x: abs(x[1]), reverse=True)
        def _amt(a: float) -> str:
            return f"{'+' if a > 0 else ''}¥{a:,.0f}"
        if len(ranked) > 3:
            rest = sum(a for _, a in ranked[3:])
            parts = [f"{_short_name(n)} {_amt(a)}" for n, a in ranked[:3]] + [f"其余 {_amt(rest)}"]
        else:
            parts = [f"{_short_name(n)} {_amt(a)}" for n, a in ranked]
        lines.append("　" + " ｜ ".join(parts))
    else:
        lines.append(f"💵 {day_label}盈亏：数据待更新")

    total_mv = sum(p.get("market_value", 0) or 0 for p in positions)
    lines.append(f"　总市值 ¥{total_mv:,.0f}")

    if pending:
        shown = "、".join(_short_name(n) for n in pending[:3])
        more = "等" if len(pending) > 3 else ""
        lines.append(f"　⏳ 待更新：{shown}{more}")

    footnote = _exchange_rate_footnote(rb.get("exchange_rates"))
    if footnote:
        lines.append(footnote)

    return "\n".join(lines)


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
            price_7d_ago = hist["prices"][-min(8, len(hist["prices"]))]
            mv_start = shares * price_7d_ago
            total_start += mv_start
        else:
            total_start += mv_end

    if total_start <= 0:
        return ""

    week_pnl = total_end - total_start
    week_pct = (week_pnl / total_start) * 100
    arrow = "🔺" if week_pnl > 0 else "🔻" if week_pnl < 0 else "➖"
    lines.append(f"**📊 本周仓位盘点**")
    lines.append(f"总市值 ¥{total_end:,.0f}　本周 {arrow} ¥{week_pnl:+,.0f}（{week_pct:+.1f}%）")

    return "\n".join(lines)
