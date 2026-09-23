"""
节假日前置熔断器（Holiday-Aware Gate）。

无状态运行，每次触发时检查对应市场是否开市。
- A股 / 国内基金：XSHG（上海证券交易所）
- 港股：XHKG（香港交易所）
- 美股 / 海外：XNYS（纽约证券交易所）

用法：
    from src.holiday_gate import is_cn_market_open, is_hk_market_open, is_us_market_open
    is_cn_market_open()             # 今天 A 股开不开市
    is_hk_market_open(date(...))    # 指定日期
    holiday_notice()               # 「部分市场休市」提示行；无则返回 ""

⚠️ 已知边界（2026-09-23 审计发现，见 TODO §1.12）
    exchange-calendars 的 **XSHG 节假日数据只记录到 2026 年底**
    （XNYS / XHKG 记录到 2027-09-23）。2027-01-01 起 `is_session()` 会抛
    `DateOutOfBounds`。本模块所有日历查询都经 `_safe_is_session()`：
    越界或异常 → 记一次 WARNING → 退化为「工作日判断」，**绝不让异常冒泡**。

    为什么必须兜底：`pending_resolver` 每次运行都会调用本模块，
    而它是流水线的第一步——一旦抛异常，**整条流水线失败**（不是少推一条）。

    ⚠️ 降级后**只能识别周末，识别不出节假日**。彻底解决需升级
    exchange-calendars（PyPI 当前最新版即 4.13.2，暂无更新）
    或内置一份 A 股节假日表。届时会打 WARNING，注意日志。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Literal

logger = logging.getLogger(__name__)

Market = Literal["cn", "hk", "us"]

#: market key → (交易所日历代码, 中文名)
_MARKETS: dict[str, tuple[str, str]] = {
    "cn": ("XSHG", "A股"),
    "hk": ("XHKG", "港股"),
    "us": ("XNYS", "美股"),
}

#: 向前扫多少天找下个交易日（十一/春节长假足够）
_MAX_SCAN_DAYS = 30

_calendars: dict[str, object | None] = {}
_degraded_warned: set[str] = set()


def _is_weekday(d: date) -> bool:
    return d.weekday() < 5


def _get_calendar(market: str):
    """取交易所日历单例。不可用返回 None（调用方降级为工作日判断）。"""
    if market in _calendars:
        return _calendars[market]
    code, label = _MARKETS[market]
    cal = None
    try:
        import exchange_calendars as ec
        cal = ec.get_calendar(code)
    except Exception as e:
        logger.warning("%s 日历(%s) 不可用（%s），退化为简单工作日判断", label, code, e)
    _calendars[market] = cal
    return cal


def _safe_is_session(market: str, d: date) -> bool | None:
    """查交易所日历。越界/异常返回 None 交由调用方降级，**绝不抛异常**。"""
    cal = _get_calendar(market)
    if cal is None:
        return None
    try:
        return bool(cal.is_session(d))
    except Exception as e:
        if market not in _degraded_warned:
            _degraded_warned.add(market)
            code, label = _MARKETS[market]
            logger.warning(
                "⚠️ %s 日历(%s) 查询 %s 越界（%s）→ 本模块降级为「工作日判断」，"
                "**节假日将识别不出**。需升级 exchange-calendars 或内置节假日表。",
                label, code, d, type(e).__name__,
            )
        return None


# ── 核心 ──

def is_market_open(market: Market, check_date: date | None = None) -> bool:
    """该市场在 check_date（默认今天）是否开市。

    日历不可用或日期越界 → 退化为「工作日判断」（识别不出节假日）。
    """
    d = check_date or date.today()
    session = _safe_is_session(market, d)
    return _is_weekday(d) if session is None else session


def is_cn_market_open(check_date: date | None = None) -> bool:
    """A 股（XSHG）是否开市。"""
    return is_market_open("cn", check_date)


def is_hk_market_open(check_date: date | None = None) -> bool:
    """港股（XHKG）是否开市。"""
    return is_market_open("hk", check_date)


def is_us_market_open(check_date: date | None = None) -> bool:
    """美股（XNYS）是否开市。"""
    return is_market_open("us", check_date)


def next_trading_day(market: Market, d: date) -> date:
    """返回 d 之后（含 d）的第一个该市场交易日。"""
    if _get_calendar(market) is not None:
        if _safe_is_session(market, d):
            return d
        cursor = d + timedelta(days=1)
        for _ in range(_MAX_SCAN_DAYS):
            session = _safe_is_session(market, cursor)
            if session is None:
                break  # 日历越界 → 放弃日历，转工作日兜底
            if session:
                return cursor
            cursor += timedelta(days=1)
        else:
            return cursor  # 扫满仍无交易日（原行为）
        d = cursor
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def next_cn_trading_day(d: date) -> date:
    """返回 d 之后（含 d）的第一个 A 股交易日。

    若 d 本身不是交易日，从 d+1 开始找。
    """
    return next_trading_day("cn", d)


# ── 展示辅助 ──

def closed_markets(check_date: date | None = None) -> list[str]:
    """今日休市的市场中文名列表（按 A股 / 港股 / 美股 顺序）。"""
    d = check_date or date.today()
    return [label for m, (_, label) in _MARKETS.items() if not is_market_open(m, d)]


def holiday_notice(check_date: date | None = None) -> str:
    """交易日内的「部分市场休市」提示行。

    全部开市、或全部休市（周末 / 长假）→ 返回 `""`：
    后者是常态，不必在卡片上提醒。
    """
    d = check_date or date.today()
    open_labels: list[str] = []
    closed_labels: list[str] = []
    for m, (_, label) in _MARKETS.items():
        (open_labels if is_market_open(m, d) else closed_labels).append(label)
    if not open_labels or not closed_labels:
        return ""
    return (
        f"**🏖️ 今日休市**：{'、'.join(closed_labels)}"
        f"（{'、'.join(open_labels)} 正常交易）"
    )


def _reset_calendar_cache() -> None:
    """清空日历与降级告警状态（**仅供测试**）。"""
    _calendars.clear()
    _degraded_warned.clear()
