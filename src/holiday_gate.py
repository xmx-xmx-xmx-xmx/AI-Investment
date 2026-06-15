"""
节假日前置熔断器（Holiday-Aware Gate）。

无状态运行，每次触发时检查对应市场是否开盘。
- A股/国内基金：XSHG（上海证券交易所）交易日历
- 美股/海外：XNYS（纽约证券交易所）交易日历

用法：
    from src.holiday_gate import market_status
    status = market_status("cn")   → {"open": True/False, "reason": "..."}
    status = market_status("us")   → {"open": True/False, "reason": "..."}
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone, timedelta
from typing import Literal

logger = logging.getLogger(__name__)

tz_cn = timezone(timedelta(hours=8))

# ── 日历单例 ──
_XSHG = None
_XNYS = None


def _get_xshg():
    global _XSHG
    if _XSHG is None:
        try:
            import exchange_calendars as ec
            _XSHG = ec.get_calendar("XSHG")
        except Exception:
            logger.warning("XSHG 日历不可用，退化为简单工作日判断")
            _XSHG = False
    return _XSHG if _XSHG is not False else None


def _get_xnys():
    global _XNYS
    if _XNYS is None:
        try:
            import exchange_calendars as ec
            _XNYS = ec.get_calendar("XNYS")
        except Exception:
            logger.warning("XNYS 日历不可用，退化为简单工作日判断")
            _XNYS = False
    return _XNYS if _XNYS is not False else None


def _is_weekday(d: date) -> bool:
    return d.weekday() < 5


# ── 核心 ──

def is_cn_market_open(check_date: date | None = None) -> bool:
    """检查 A 股（XSHG）是否开市。"""
    d = check_date or date.today()
    cal = _get_xshg()
    if cal is not None:
        return cal.is_session(d)
    return _is_weekday(d)


def is_us_market_open(check_date: date | None = None) -> bool:
    """检查美股（XNYS）是否开市。"""
    d = check_date or date.today()
    cal = _get_xnys()
    if cal is not None:
        return cal.is_session(d)
    return _is_weekday(d)


def next_cn_trading_day(d: date) -> date:
    """返回 d 之后（含 d）的第一个 A 股交易日。

    若 d 本身不是交易日，从 d+1 开始找。
    """
    cal = _get_xshg()
    if cal is not None:
        if cal.is_session(d):
            return d
        # 从 d+1 开始往前找第一个交易日
        cursor = d + timedelta(days=1)
        max_attempts = 30  # 最多往前找 30 天（十一长假）
        for _ in range(max_attempts):
            if cal.is_session(cursor):
                return cursor
            cursor += timedelta(days=1)
        return cursor
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def next_us_trading_day(d: date) -> date:
    cal = _get_xnys()
    if cal is not None:
        if cal.is_session(d):
            return d
        cursor = d + timedelta(days=1)
        for _ in range(30):
            if cal.is_session(cursor):
                return cursor
            cursor += timedelta(days=1)
        return cursor
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def market_status(market: Literal["cn", "us"]) -> dict:
    """获取市场状态。"""
    today = date.today()
    if market == "cn":
        return {"market": "A股", "open": is_cn_market_open(today), "date": str(today)}
    return {"market": "美股", "open": is_us_market_open(today), "date": str(today)}
