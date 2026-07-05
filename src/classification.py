# -*- coding: utf-8 -*-
"""
资产分类模块 —— 投资载体 & 资产大类推断的单一真源。

所有模块统一从此引用，避免 radar / pending_resolver 多处维护不一致。

投资载体（纯代码格式推断，100% 准确）：
- 场外基金: 6位数字, 非 ETF 前缀 (51/56/58/159). 含 16xxxx LOF
- 场内ETF:  6位数字, ETF 前缀
- 个股:     5位数字(港股) 或 纯字母(美股)

资产大类（多策略推断）：
- ETF/个股 → 从代码格式推断市场 → 映射到五大类
- 场外基金 → akshare fund_name_em 基金类型 → 映射
            → 名称关键词匹配（兜底）
            → "待分类"（彻底兜底）
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 投资载体推断
# ═══════════════════════════════════════════════════════════════


# 名称中的 ETF 关键词（区分 ETF vs 个股）
_ETF_NAME_KW = ["ETF", "ETP", "指数", "Index", "指数型", "指数基金"]

# 知名美股 ETF（纯字母代码，名称未知时也能识别）
_KNOWN_US_ETFS = {
    "QQQ", "SPY", "GLD", "IWM", "EEM", "TLT", "VOO", "VTI", "BND",
    "XLF", "XLE", "XLK", "XLV", "XLY", "XLI", "XLB", "XLP", "XLU",
    "DIA", "IVV", "EFA", "VEA", "VWO", "AGG", "LQD", "HYG", "TIP",
    "SCHD", "ARKK", "SOXX", "SMH",
}


def get_investment_vehicle(code: str, name: str = "") -> str:
    """根据代码格式 + 名称关键词推断投资载体。

    港股(5位)/美股(字母)无法仅靠代码格式区分 ETF 与个股，
    需要借助名称中的关键词（如 "ETF"、"指数"）辅助判断。

    Returns:
        "场外基金" | "场内ETF" | "个股" | "未知"
    """
    if not code:
        return "未知"

    code_s = str(code).strip()
    name_up = str(name).upper() if name else ""

    def _name_says_etf() -> bool:
        if any(kw.upper() in name_up for kw in _ETF_NAME_KW):
            return True
        if code_s.upper() in _KNOWN_US_ETFS:
            return True
        return False

    # 6 位数字 → 区分场内 ETF / 场外基金
    if code_s.isdigit() and len(code_s) == 6:
        if code_s.startswith(("51", "56", "58", "159")):
            return "场内ETF"
        return "场外基金"

    # 5 位数字 → 港股，需名称区分 ETF vs 个股
    if code_s.isdigit() and len(code_s) == 5:
        if _name_says_etf():
            return "场内ETF"
        return "个股"

    # 纯字母 → 美股，需名称/已知列表区分 ETF vs 个股
    if code_s.isalpha():
        if _name_says_etf():
            return "场内ETF"
        return "个股"

    return "未知"


# ═══════════════════════════════════════════════════════════════
# 资产大类推断
# ═══════════════════════════════════════════════════════════════

# akshare fund_name_em「基金类型」→ 资产大类映射
_FUND_TYPE_TO_CLASS: dict[str, str] = {
    "指数型-海外股票": "美股资产",
    "QDII":              "美股资产",
    "债券型-中短债":     "固收资产",
    "债券型-长债":       "固收资产",
    "债券型-混合债":     "固收资产",
    "债券型-可转债":     "固收资产",
    "债券型":            "固收资产",
    "货币型":            "固收资产",
    "商品(不含黄金)":    "避险商品",
    "商品型":            "避险商品",
    "黄金":              "避险商品",
    "指数型-股票":       "A股资产",
    "股票型":            "A股资产",
    "混合型-偏股":       "A股资产",
    "混合型-偏债":       "A股资产",
    "混合型-灵活":       "A股资产",
    "混合型-平衡":       "A股资产",
    "混合型":            "A股资产",
}

# 名称关键词 → 资产大类（场外基金兜底策略）
_NAME_KEYWORD_CLASS: list[tuple[list[str], str]] = [
    (["纳斯达克", "纳指", "标普500", "标普", "道琼斯", "美股"], "美股资产"),
    (["港股通互联网", "恒生互联网", "恒生科技", "港股通红利", "恒生红利",
      "沪港深", "港股", "恒生"], "港股资产"),
    (["红利低波", "红利", "中证红利", "中证500", "中证1000", "中证",
      "沪深300", "沪深", "创业板", "上证", "深证", "A股"], "A股资产"),
    (["债券", "短债", "纯债", "中短债", "长债", "信用债", "利率债",
      "货币", "固收", "理财"], "固收资产"),
    (["上海金", "黄金", "金", "大宗商品", "原油"], "避险商品"),
]

# 代码格式 → 资产大类（ETF/个股用）
_CODE_FORMAT_CLASS: dict[str, str] = {
    "场内ETF_A股": "A股资产",
    "场内ETF_港股": "港股资产",
    "场内ETF_美股": "美股资产",
    "个股_港股": "港股资产",
    "个股_美股": "美股资产",
}


def _code_format_key(code: str) -> str | None:
    """返回代码格式的分类 key，供资产大类映射使用。"""
    if not code:
        return None
    code = str(code).strip()

    if code.isdigit() and len(code) == 6:
        if code.startswith(("51", "56", "58", "159", "16")):
            # 大部分 A 股 ETF，但也有 513xxx 等港股/美股 ETF
            # 通过具体代码前缀进一步区分
            if code.startswith(("513", "1599")):
                return "场内ETF_美股"  # 纳指/标普 ETF
            if code.startswith("5130") and code[4:] >= "50":
                return "场内ETF_港股"  # 恒生/中概 ETF
            return "场内ETF_A股"
        return None  # 场外基金 → 走基金类型推断

    if code.isdigit() and len(code) == 5:
        return "个股_港股"

    if code.isalpha():
        return "个股_美股"

    return None


# 基金类型缓存
_fund_type_cache: dict[str, str | None] = {}


def _get_fund_type(code: str) -> str:
    """通过 akshare fund_name_em 获取基金类型（缓存）。"""
    if code in _fund_type_cache:
        return _fund_type_cache[code] or ""

    try:
        import os as _os
        for _k in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY'):
            _os.environ.pop(_k, None)
        import akshare as _ak
        df = _ak.fund_name_em()
        if df is not None and not df.empty:
            match = df[df["基金代码"] == code]
            if not match.empty:
                ftype = str(match.iloc[0].get("基金类型", ""))
                _fund_type_cache[code] = ftype
                return ftype
    except Exception as e:
        logger.debug("基金类型查询失败 [%s]: %s", code, str(e)[:80])

    _fund_type_cache[code] = None
    return ""


def infer_asset_class(code: str, name: str = "", fund_type: str = "") -> str:
    """根据代码、名称、基金类型推断资产大类。

    返回 TARGET_WEIGHTS 中五大类之一，或 "待分类"。

    Args:
        code: 标的代码
        name: 标的名称（可选，用于关键词匹配）
        fund_type: akshare「基金类型」字段值（可选，调用方传入可避免重复 API 调用）
    """
    if not code:
        return "待分类"

    vehicle = get_investment_vehicle(code, name)

    # ── ETF / 个股：从代码推断市场 → 映射大类 ──
    fmt_key = _code_format_key(code)
    if fmt_key and fmt_key in _CODE_FORMAT_CLASS:
        return _CODE_FORMAT_CLASS[fmt_key]

    # ── 场外基金：多策略推断 ──
    if vehicle == "场外基金":
        # 策略 1: 名称关键词（最准，优先级最高）
        if name:
            for keywords, cls in _NAME_KEYWORD_CLASS:
                if any(kw in name for kw in keywords):
                    return cls

        # 策略 2: 基金类型 → 映射
        ftype = fund_type or _get_fund_type(code)
        if ftype:
            for key, cls in _FUND_TYPE_TO_CLASS.items():
                if key in ftype:
                    return cls

    # 兜底
    if vehicle == "场外基金":
        return "待分类"

    return "待分类"
