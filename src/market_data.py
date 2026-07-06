# -*- coding: utf-8 -*-
"""
轻量级行情数据层 —— 直接封装 akshare + yfinance。

设计原则：
- 零依赖 data_provider/，不碰那个断裂的基类体系
- 每个函数独立可测，出错了只影响自己
- 输出格式统一，方便上游 advisor / market_brief 消费

当前覆盖：
- A 股 ETF 行情（akshare，完全免费）
- 美股 ETF 行情（yfinance，完全免费）
- 港股待后续扩展

用法：
    from src.market_data import fetch_cn_etf, fetch_us_etf
    data = fetch_cn_etf("515080")  # 中证红利ETF
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 代理防御（国内数据源需要直连，不能走代理）
# ═══════════════════════════════════════════════════════════════

_PROXY_CLEANED = False


def _ensure_no_proxy() -> None:
    """移除代理环境变量，确保国内数据源直连。

    惰性调用（首次 fetch 时才清理），避免 import 时副作用。
    """
    global _PROXY_CLEANED
    if _PROXY_CLEANED:
        return
    for _key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(_key, None)
    _PROXY_CLEANED = True


# ═══════════════════════════════════════════════════════════════
# ETF 品种注册表 —— 加新品种只需在此追加一行
# ═══════════════════════════════════════════════════════════════

CN_ETF_MAP = {
    "515080": "中证红利ETF",
    "512890": "红利低波ETF",
    "510500": "中证500ETF",
    "513100": "纳指100ETF",
    "159941": "纳指ETF",
    "159509": "纳指科技ETF",
    "518880": "黄金ETF",
    "513050": "中概互联ETF",
    "513330": "恒生互联ETF",
}

US_ETF_MAP = {
    "QQQ": "纳斯达克100 ETF",
    "GLD": "黄金ETF",
    "SPY": "标普500 ETF",
    "IWM": "罗素2000 ETF",
    "EEM": "新兴市场 ETF",
    "TLT": "20年期美债 ETF",
}

US_INDEX_MAP = {
    "^DJI":  "道琼斯工业指数",
    "^GSPC": "标普500指数",
    "^IXIC": "纳斯达克综合指数",
    "^SOX":  "费城半导体指数",
}

# akshare sina 源符号映射（去掉 ^ 前缀，sina 用 . 前缀）
_AKSHARE_INDEX_SYMBOL = {
    "^DJI":  ".DJI",
    "^GSPC": ".INX",
    "^IXIC": ".IXIC",
    "^SOX":  ".SOX",
}


# ═══════════════════════════════════════════════════════════════
# A 股 ETF（akshare）
# ═══════════════════════════════════════════════════════════════

def fetch_cn_etf(code: str) -> Optional[dict]:
    """
    抓取单只 A 股 ETF 最新行情。

    Args:
        code: 6 位 ETF 代码，如 "515080"

    Returns:
        {"code": "515080", "name": "中证红利ETF", "close": 1.50, "change_pct": +0.35}
        失败返回 None
    """
    _ensure_no_proxy()

    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare 未安装，请执行: pip install akshare")
        return None

    name = CN_ETF_MAP.get(code, code)

    # 策略 1: 东方财富源（自带涨跌幅，最可靠）
    try:
        df = ak.fund_etf_hist_em(symbol=code, period="daily", adjust="")
        if len(df) >= 2:
            return {
                "code": code,
                "name": name,
                "market": "A股",
                "close": round(float(df["收盘"].iloc[-1]), 2),
                "change_pct": round(float(df["涨跌幅"].iloc[-1]), 2),
                "source": "akshare_em",
            }
    except Exception:
        logger.debug("[%s] akshare_em 源失败", code)

    # 策略 2: 新浪源（英文列名，需手算涨跌）
    try:
        # 自动判断交易所前缀：159/16开头→深圳(sz)，其余→上海(sh)
        prefix = "sz" if code.startswith(("159", "16")) else "sh"
        symbol = f"{prefix}{code}"
        df = ak.fund_etf_hist_sina(symbol=symbol)
        if len(df) >= 2:
            prev = float(df["close"].iloc[-2])
            today = float(df["close"].iloc[-1])
            return {
                "code": code,
                "name": name,
                "market": "A股",
                "close": round(today, 2),
                "change_pct": round((today - prev) / prev * 100, 2),
                "source": "akshare_sina",
            }
    except Exception:
        logger.debug("[%s] akshare_sina 源失败", code)

    logger.warning(f"[{code}] {name} 所有数据源均失败")
    return None


def fetch_cn_etfs(codes: list[str]) -> list[dict]:
    """批量抓取多只 A 股 ETF，失败的不影响其他。"""
    results = []
    for code in codes:
        data = fetch_cn_etf(code)
        if data:
            results.append(data)
        time.sleep(0.3)  # 礼貌限速
    return results


# ═══════════════════════════════════════════════════════════════
# 美股 ETF（yfinance）
# ═══════════════════════════════════════════════════════════════

def fetch_us_etf(ticker: str) -> Optional[dict]:
    """
    抓取单只美股 ETF 最新行情。

    数据源优先级：yfinance → akshare（国内网络兜底）

    Args:
        ticker: 美股代码，如 "QQQ"

    Returns:
        {"ticker": "QQQ", "name": "纳斯达克100 ETF", "close": 500.0, "change_pct": +0.80}
        失败返回 None
    """
    name = US_ETF_MAP.get(ticker, ticker)

    # 策略 1: yfinance（国际通用，但国内可能被墙/限流）
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        df = t.history(period="5d")
        if len(df) >= 2:
            prev = float(df["Close"].iloc[-2])
            today = float(df["Close"].iloc[-1])
            return {
                "ticker": ticker, "name": name, "market": "美股",
                "close": round(today, 2),
                "change_pct": round((today - prev) / prev * 100, 2),
                "source": "yfinance",
            }
    except Exception:
        logger.debug("[%s] yfinance 源失败", ticker)

    # 策略 2: akshare 东方财富源（国内可用，免费无需代理）
    try:
        import akshare as ak
        df = ak.stock_us_hist(symbol=ticker, period="daily", adjust="")
        if len(df) >= 2:
            prev = float(df["收盘"].iloc[-2])
            today = float(df["收盘"].iloc[-1])
            pct = round((today - prev) / prev * 100, 2)
            return {
                "ticker": ticker, "name": name, "market": "美股",
                "close": round(today, 2),
                "change_pct": pct,
                "source": "akshare_em",
            }
    except Exception:
        logger.debug("[%s] akshare_em 源失败", ticker)

    # 策略 3: akshare 新浪源
    try:
        import akshare as ak
        df = ak.stock_us_daily(symbol=ticker, adjust="")
        if len(df) >= 2:
            prev = float(df["close"].iloc[-2])
            today = float(df["close"].iloc[-1])
            pct = round((today - prev) / prev * 100, 2)
            return {
                "ticker": ticker, "name": name, "market": "美股",
                "close": round(today, 2),
                "change_pct": pct,
                "source": "akshare_sina",
            }
    except Exception:
        logger.debug("[%s] akshare_sina 源失败", ticker)

    logger.warning(f"[{ticker}] {name} 所有数据源均失败")
    return None


def fetch_us_etfs(tickers: list[str]) -> list[dict]:
    """批量抓取多只美股 ETF。"""
    results = []
    for t in tickers:
        data = fetch_us_etf(t)
        if data:
            results.append(data)
        time.sleep(0.2)
    return results


# ═══════════════════════════════════════════════════════════════
# 美股三大指数（yfinance 优先 → akshare sina 兜底）
# ═══════════════════════════════════════════════════════════════

def fetch_us_index(ticker: str) -> Optional[dict]:
    """抓取单只美股指数最新行情。

    数据源优先级：yfinance → akshare index_us_stock_sina

    Args:
        ticker: 美股指数代码，如 "^DJI"

    Returns:
        {"ticker": "^DJI", "name": "道琼斯工业指数", "market": "美股",
         "close": 42000.0, "change_pct": +0.35, "source": "yfinance"}
        失败返回 None
    """
    name = US_INDEX_MAP.get(ticker, ticker)
    ak_symbol = _AKSHARE_INDEX_SYMBOL.get(ticker, ticker)

    # 策略 1: yfinance（海外网络直连）
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        df = t.history(period="5d")
        if len(df) >= 2:
            prev = float(df["Close"].iloc[-2])
            today = float(df["Close"].iloc[-1])
            return {
                "ticker": ticker, "name": name, "market": "美股",
                "close": round(today, 2),
                "change_pct": round((today - prev) / prev * 100, 2),
                "source": "yfinance",
            }
    except Exception:
        logger.debug("[%s] yfinance 源失败", ticker)

    # 策略 2: akshare 新浪源（国内可用）
    try:
        import akshare as ak
        df = ak.index_us_stock_sina(symbol=ak_symbol)
        if len(df) >= 2:
            prev = float(df["close"].iloc[-2])
            today = float(df["close"].iloc[-1])
            return {
                "ticker": ticker, "name": name, "market": "美股",
                "close": round(today, 2),
                "change_pct": round((today - prev) / prev * 100, 2),
                "source": "akshare_sina",
            }
    except Exception:
        logger.debug("[%s] akshare_sina 源失败", ticker)

    logger.warning(f"[{ticker}] {name} 所有数据源均失败")
    return None


def fetch_us_indices(tickers: list[str]) -> list[dict]:
    """批量抓取多只美股指数。"""
    results = []
    for t in tickers:
        data = fetch_us_index(t)
        if data:
            results.append(data)
        time.sleep(0.2)
    return results


# ═══════════════════════════════════════════════════════════════
# 美债收益率（akshare 单源）
# ═══════════════════════════════════════════════════════════════

def fetch_us_treasury() -> Optional[dict]:
    """抓取最新美国国债收益率（2Y / 10Y / 10Y-2Y 利差）。

    数据源：akshare bond_zh_us_rate（中美债券收益率全期限表）

    Returns:
        {"date": "2026-06-18", "us_2y": 4.19, "us_10y": 4.46,
         "us_10y2y_spread": 0.27, "source": "akshare_bond"}
        失败返回 None
    """
    _ensure_no_proxy()

    try:
        import akshare as ak
        df = ak.bond_zh_us_rate()
        if df.empty:
            logger.warning("美债收益率数据为空")
            return None

        last = df.iloc[-1]
        return {
            "date": str(last["日期"]),
            "us_2y": float(last["美国国债收益率2年"]),
            "us_10y": float(last["美国国债收益率10年"]),
            "us_10y2y_spread": float(last["美国国债收益率10年-2年"]),
            "source": "akshare_bond",
        }
    except Exception as e:
        logger.warning("美债收益率获取失败: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════
# VIX 恐慌指数 —— 多源 fallback
# ═══════════════════════════════════════════════════════════════

def _vix_level(vix: float) -> str:
    if vix >= 30:
        return "极度恐慌"
    if vix >= 25:
        return "恐慌"
    if vix >= 20:
        return "谨慎"
    if vix >= 15:
        return "正常"
    return "极度平静"


def fetch_vix() -> Optional[dict]:
    """获取 VIX 恐慌指数。双源 fallback：yfinance → akshare。
    """
    _ensure_no_proxy()

    # 策略 1: yfinance（海外网络直连）
    try:
        import yfinance as yf
        t = yf.Ticker("^VIX")
        df = t.history(period="5d")
        if not df.empty:
            vix = round(float(df["Close"].iloc[-1]), 2)
            return {"vix": vix, "level": _vix_level(vix), "source": "yfinance"}
    except Exception:
        logger.debug("VIX yfinance 源失败")

    # 策略 2: akshare 全球指数（东方财富源）
    try:
        import akshare as ak
        df = ak.index_global_hist_em(symbol="VIX")
        if not df.empty and "收盘" in df.columns:
            vix = round(float(df["收盘"].iloc[-1]), 2)
            return {"vix": vix, "level": _vix_level(vix), "source": "akshare_em"}
    except Exception:
        logger.debug("VIX akshare_em 源失败")

    logger.warning("VIX 所有数据源均失败")
    return None


# ═══════════════════════════════════════════════════════════════
# 港股行情
# ═══════════════════════════════════════════════════════════════

HK_STOCK_MAP = {
    "00700": "腾讯控股",
    "09988": "阿里巴巴-SW",
    "09660": "比亚迪股份",
    "01810": "小米集团-W",
    "09618": "京东集团-SW",
    "09999": "网易-S",
    "00981": "中芯国际",
    "02269": "药明生物",
    "01299": "友邦保险",
    "00005": "汇丰控股",
    "02318": "中国平安",
    "00388": "香港交易所",
    "03690": "美团-W",
    "09961": "携程集团-S",
    "01211": "比亚迪",
}


def fetch_hk_stock(code: str) -> Optional[dict]:
    """抓取单只港股最新行情。

    Args:
        code: 5 位港股代码（不含 .HK），如 "00700"

    Returns:
        {"code": "00700", "name": "腾讯控股", "close": 350.0, "change_pct": +1.50}
        失败返回 None
    """
    _ensure_no_proxy()

    name = HK_STOCK_MAP.get(code, code)

    # 策略 0: Sina 实时行情（交易时段可获得盘中价，轻量、免费）
    # 新浪格式: [0]英文名 [1]中文名 [2]今开 [3]昨收 [6]最新价 [8]涨跌幅%
    try:
        import requests as _req
        url = f"https://hq.sinajs.cn/list=hk{code}"
        resp = _req.get(
            url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=8
        )
        resp.encoding = "gbk"
        text = resp.text
        if "hq_str_hk" in text and '""' not in text:
            parts = text.split('"')[1].split(",")
            if len(parts) >= 9:
                price = float(parts[6]) if parts[6] else 0
                prev_close = float(parts[3]) if parts[3] else 0
                pct_raw = float(parts[8]) if parts[8] else 0
                if price > 0:
                    name_cn = parts[1].strip() if parts[1] else name
                    # 优先用新浪已算好的涨跌幅，兜底自算
                    pct = round(pct_raw, 2) if pct_raw != 0 else (
                        round((price - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0
                    )
                    return {
                        "code": code, "name": name_cn, "market": "港股",
                        "close": round(price, 2),
                        "change_pct": pct,
                        "source": "sina_realtime",
                    }
    except Exception:
        logger.debug("[%s] Sina 实时行情源失败", code)

    # 策略 1: yfinance .info（实时价，交易时段可用）
    try:
        import yfinance as yf
        t = yf.Ticker(f"{code}.HK")
        info = t.info
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
        if price and prev_close:
            pct = round((price - prev_close) / prev_close * 100, 2)
            return {
                "code": code, "name": name, "market": "港股",
                "close": round(price, 2),
                "change_pct": pct,
                "source": "yfinance_realtime",
            }
    except Exception:
        logger.debug("[%s] yfinance .info 源失败", code)

    # 策略 2: akshare 东方财富源（国内可用，免费）
    try:
        import akshare as ak
        df = ak.stock_hk_hist_em(symbol=code, period="daily", adjust="")
        if len(df) >= 2:
            prev = float(df["收盘"].iloc[-2])
            today = float(df["收盘"].iloc[-1])
            pct = round((today - prev) / prev * 100, 2)
            return {
                "code": code, "name": name, "market": "港股",
                "close": round(today, 2),
                "change_pct": pct,
                "source": "akshare_em",
            }
    except Exception:
        logger.debug("[%s] akshare_em 源失败", code)

    # 策略 3: akshare 新浪源
    try:
        import akshare as ak
        df = ak.stock_hk_daily(symbol=code, adjust="")
        if len(df) >= 2:
            prev = float(df["close"].iloc[-2])
            today = float(df["close"].iloc[-1])
            pct = round((today - prev) / prev * 100, 2)
            return {
                "code": code, "name": name, "market": "港股",
                "close": round(today, 2),
                "change_pct": pct,
                "source": "akshare_sina",
            }
    except Exception:
        logger.debug("[%s] akshare_sina 源失败", code)

    # 策略 4: yfinance 兜底
    try:
        import yfinance as yf
        t = yf.Ticker(f"{code}.HK")
        df = t.history(period="5d")
        if len(df) >= 2:
            prev = float(df["Close"].iloc[-2])
            today = float(df["Close"].iloc[-1])
            pct = round((today - prev) / prev * 100, 2)
            return {
                "code": code, "name": name, "market": "港股",
                "close": round(today, 2),
                "change_pct": pct,
                "source": "yfinance",
            }
    except Exception:
        logger.debug("[%s] yfinance 源失败", code)

    logger.warning(f"[{code}] {name} 所有数据源均失败")
    return None


# ═══════════════════════════════════════════════════════════════
# 便捷入口：一键获取全品种快照
# ═══════════════════════════════════════════════════════════════

def snapshot() -> dict:
    """
    一键获取当前持仓相关所有品种的价格快照。

    Returns:
        {
            "cn_etfs": [...],
            "us_etfs": [...],
            "hk_stocks": [...],
            "vix": {...},
            "us_indices": [...],
            "us_treasury": {...},
            "ok": True/False
        }
    """
    result = {"cn_etfs": [], "us_etfs": [], "hk_stocks": [], "vix": None,
              "us_indices": [], "us_treasury": None, "ok": False}

    result["cn_etfs"] = fetch_cn_etfs(["515080", "513100", "159941", "518880"])
    result["us_etfs"] = fetch_us_etfs(["QQQ", "GLD"])
    result["hk_stocks"] = fetch_hk_stocks(["00700", "09988"])
    result["vix"] = fetch_vix()
    result["us_indices"] = fetch_us_indices(["^DJI", "^GSPC", "^IXIC"])
    result["us_treasury"] = fetch_us_treasury()

    result["ok"] = len(result["cn_etfs"]) > 0 or len(result["us_etfs"]) > 0
    return result


def fetch_hk_stocks(codes: list[str]) -> list[dict]:
    """批量抓取多只港股。"""
    results = []
    for code in codes:
        data = fetch_hk_stock(code)
        if data:
            results.append(data)
        time.sleep(0.3)
    return results


# ═══════════════════════════════════════════════════════════════
# 汇率（外币 → CNY）
# ═══════════════════════════════════════════════════════════════

# 缓存：同一次进程运行只抓一次
_exchange_rate_cache: dict[str, float | None] = {}


def fetch_exchange_rate(currency: str) -> float | None:
    """获取某货币兑人民币汇率（1 外币 = ? CNY）。

    数据源优先级：akshare 中行折算价 → yfinance FX 对 → None 兜底。

    Args:
        currency: "HKD", "USD", 或 "CNY"

    Returns:
        汇率值，CNY 返回 1.0，所有数据源失败返回 None
    """
    cur = str(currency).upper().strip()
    if cur == "CNY":
        return 1.0

    if cur in _exchange_rate_cache:
        return _exchange_rate_cache[cur]

    _ensure_no_proxy()

    rate = _fetch_rate_akshare(cur)
    if rate is None:
        rate = _fetch_rate_yfinance(cur)

    _exchange_rate_cache[cur] = rate
    if rate is None:
        logger.warning("汇率 %s/CNY 所有数据源均失败，将不做换算", cur)
    else:
        logger.info("汇率 %s/CNY = %.4f", cur, rate)
    return rate


def _fetch_rate_akshare(currency: str) -> float | None:
    """akshare 中行折算价（100 外币 → CNY，需 /100）。

    数据源：currency_boc_safe（多币种日频）→ currency_boc_sina（仅 USD）→ 免费汇率 API。
    """
    try:
        import akshare as ak
        # 策略 1: currency_boc_safe（包含美元、港元等多币种）
        col_map = {"USD": "美元", "HKD": "港元"}
        col_name = col_map.get(currency)
        if col_name:
            df = ak.currency_boc_safe()
            if df is not None and not df.empty and col_name in df.columns:
                # 取最新有效行
                for idx in range(len(df) - 1, -1, -1):
                    val = df.iloc[idx][col_name]
                    if val is not None and (isinstance(val, (int, float)) and val > 0):
                        # 报价以 100 外币为单位 → 除以 100
                        return round(float(val) / 100.0, 6)
    except Exception:
        logger.debug("汇率 %s currency_boc_safe 失败", currency)

    try:
        import akshare as ak
        # 策略 2: currency_boc_sina（仅 USD/CNY）
        if currency == "USD":
            df = ak.currency_boc_sina()
            if df is not None and not df.empty and "中行折算价" in df.columns:
                last = df["中行折算价"].iloc[-1]
                if last and float(last) > 0:
                    return round(float(last) / 100.0, 6)
    except Exception:
        logger.debug("汇率 %s currency_boc_sina 失败", currency)

    # 策略 3: 免费汇率 API（k780.com，无认证要求）
    try:
        import requests as _req
        r = _req.get(
            f"https://sapi.k780.com/?app=finance.rate&scur={currency}&tcur=CNY"
            f"&appkey=10003&sign=b59bc3ef6191eb9f747dd4e83c99f2a4",
            timeout=10,
        )
        if r.ok:
            data = r.json()
            if data.get("success") == "1":
                rate = float(data["result"]["rate"])
                if rate > 0:
                    return round(rate, 6)
    except Exception:
        logger.debug("汇率 %s k780 API 失败", currency)

    return None


def _fetch_rate_yfinance(currency: str) -> float | None:
    """yfinance FX 对兜底。"""
    try:
        import yfinance as yf
        import time as _time
        # yfinance 可能限流，加短暂延迟
        _time.sleep(0.5)
        ticker = f"{currency}CNY=X"
        t = yf.Ticker(ticker)
        df = t.history(period="5d")
        if df.empty:
            return None
        close = float(df["Close"].iloc[-1])
        if close <= 0:
            return None
        return round(close, 6)
    except Exception as e:
        logger.debug("汇率 %s yfinance 源失败: %s", currency, str(e)[:80])
        return None
