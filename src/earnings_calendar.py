# -*- coding: utf-8 -*-
"""
财报日历 —— D4 模块。

职责：
- 扫描雷达表/底仓表中的美股个股，查询 yfinance 财报日历
- 输出本周/下周财报日历 + 昨日已发布财报的关键指标

用法：
    from src.earnings_calendar import fetch_weekly_earnings, fetch_yesterdays_earnings
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

tz_cn = timezone(timedelta(hours=8))

# 雷达/持仓中关注的个股 ticker（自动从飞书扫描）
# 硬编码一组核心半导体/科技股作为兜底
_FALLBACK_TICKERS = ["AAPL", "NVDA", "MSFT", "TSM", "ASML", "MU", "INTC", "AMD"]


def _get_radar_us_tickers() -> list[str]:
    """从雷达观测表 + 底仓表读取美股个股代码。"""
    tickers = set()
    try:
        from src.feishu_client import FeishuClient
        client = FeishuClient()

        # 雷达表
        try:
            radar = client.list_records("雷达观测表")
            for r in radar:
                code = r.get("标的代码", "")
                if code and code.isalpha() and code == code.upper():
                    tickers.add(code)
        except Exception:
            pass

        # 底仓表（仅美股）
        try:
            holdings = client.list_records("底仓表")
            for h in holdings:
                code = h.get("标的代码", "")
                cls = h.get("资产大类", "")
                if isinstance(cls, list):
                    cls = cls[0] if cls else ""
                if "美股" in str(cls) and code and code.isalpha() and code == code.upper():
                    tickers.add(code)
        except Exception:
            pass
    except Exception:
        pass

    if not tickers:
        tickers = set(_FALLBACK_TICKERS)

    return sorted(tickers)


def fetch_weekly_earnings(days_ahead: int = 7) -> list[dict]:
    """查询未来 N 天有财报发布的美股个股。

    Returns:
        [{"ticker": "MU", "name": "Micron Technology", "earnings_date": date(2026,6,26),
          "eps_estimate": 1.65, "revenue_estimate": 8.9e9, "source": "雷达"}, ...]
    """
    tickers = _get_radar_us_tickers()
    results = []
    today = date.today()
    end_date = today + timedelta(days=days_ahead)

    for ticker_str in tickers:
        try:
            import yfinance as yf
            t = yf.Ticker(ticker_str)
            info = t.info
            name = info.get("shortName") or info.get("longName") or ticker_str

            # earnings_dates 返回 DataFrame: index=date, columns=['EPS Estimate', 'Reported EPS', 'Surprise(%)']
            df = None
            try:
                df = t.earnings_dates
            except Exception:
                pass

            if df is None or df is None or (hasattr(df, 'empty') and df.empty):
                continue

            if not hasattr(df, 'iterrows'):
                continue

            for idx, row in df.iterrows():
                edate = idx.date() if hasattr(idx, 'date') else None
                if edate is None:
                    try:
                        edate = idx.date()
                    except Exception:
                        continue

                if today <= edate <= end_date:
                    eps_est = row.get('EPS Estimate', None) if 'EPS Estimate' in df.columns else None
                    results.append({
                        "ticker": ticker_str,
                        "name": name,
                        "earnings_date": edate,
                        "eps_estimate": round(float(eps_est), 2) if eps_est is not None and not (isinstance(eps_est, float) and (eps_est != eps_est)) else None,  # NaN check
                        "source": "yfinance",
                    })
            time.sleep(0.2)
        except Exception as e:
            logger.debug("[%s] 财报查询失败: %s", ticker_str, str(e)[:80])

    # 按日期排序
    results.sort(key=lambda x: x["earnings_date"])
    return results


def fetch_yesterdays_earnings() -> list[dict]:
    """查询昨天发布了财报的美股个股，带实际 EPS vs 预期。

    Returns:
        [{"ticker": "MU", "name": "Micron", "eps_actual": 1.70, "eps_estimate": 1.58, ...}, ...]
    """
    tickers = _get_radar_us_tickers()
    results = []
    yesterday = date.today() - timedelta(days=1)

    for ticker_str in tickers:
        try:
            import yfinance as yf
            t = yf.Ticker(ticker_str)
            info = t.info
            name = info.get("shortName") or info.get("longName") or ticker_str

            df = None
            try:
                df = t.earnings_dates
            except Exception:
                pass

            if df is None or (hasattr(df, 'empty') and df.empty):
                continue

            if not hasattr(df, 'iterrows'):
                continue

            for idx, row in df.iterrows():
                edate = idx.date() if hasattr(idx, 'date') else None
                if edate is None:
                    try:
                        edate = idx.date()
                    except Exception:
                        continue

                if edate == yesterday:
                    eps_est = row.get('EPS Estimate', None) if 'EPS Estimate' in df.columns else None
                    eps_actual = row.get('Reported EPS', None) if 'Reported EPS' in df.columns else None
                    surprise = row.get('Surprise(%)', None) if 'Surprise(%)' in df.columns else None
                    results.append({
                        "ticker": ticker_str,
                        "name": name,
                        "earnings_date": edate,
                        "eps_estimate": round(float(eps_est), 2) if eps_est is not None and not (isinstance(eps_est, float) and (eps_est != eps_est)) else None,
                        "eps_actual": round(float(eps_actual), 2) if eps_actual is not None and not (isinstance(eps_actual, float) and (eps_actual != eps_actual)) else None,
                        "surprise_pct": round(float(surprise), 1) if surprise is not None and not (isinstance(surprise, float) and (surprise != surprise)) else None,
                        "source": "yfinance",
                    })
            time.sleep(0.2)
        except Exception as e:
            logger.debug("[%s] 财报查询失败: %s", ticker_str, str(e)[:80])

    return results


def format_weekly_earnings(earnings: list[dict]) -> str:
    """格式化本周财报日历为简报文本。"""
    if not earnings:
        return ""
    lines = ["📅 **下周财报日历**"]
    for e in earnings:
        edate = e["earnings_date"]
        date_str = edate.strftime("%m/%d") if edate else "?"
        day_map = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
        day_cn = day_map.get(edate.weekday(), "") if edate else ""
        eps = f" | EPS预期 ${e['eps_estimate']}" if e.get('eps_estimate') else ""
        lines.append(f"· {e['ticker']} {e['name']} {date_str}({day_cn}){eps}")
    return "\n".join(lines)


def format_yesterdays_earnings(earnings: list[dict]) -> str:
    """格式化昨日已发布财报为简报文本。"""
    if not earnings:
        return ""
    lines = ["📅 **昨日财报**"]
    for e in earnings:
        eps_line = ""
        if e.get('eps_actual') and e.get('eps_estimate'):
            beat = "超预期" if (e['eps_actual'] or 0) > (e['eps_estimate'] or 0) else "低于预期"
            eps_line = f" EPS实际${e['eps_actual']} vs 预期${e['eps_estimate']}（{beat}"
            if e.get('surprise_pct'):
                eps_line += f" {e['surprise_pct']:+.1f}%"
            eps_line += "）"
        lines.append(f"· {e['ticker']} {e['name']}{eps_line}")
    return "\n".join(lines)
