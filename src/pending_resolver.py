"""
Pending 交易自动确认器。

职责：
- 读取飞书「交易流水表」中状态为 pending 的记录
- 根据 15:00 分水岭 + 中国交易日历，确定正确的 T 日
- 从 akshare 抓取基金净值，计算确认份额
- 加权平均法计算新成本价，同步覆写飞书底仓表
- QDII/场外基金 T+n 懒加载：净值未发布时静默跳过

用法：
  python -m src.pending_resolver
  python -m src.pending_resolver --dry-run
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime, timezone, timedelta, date
from typing import Optional

import pandas as pd

from src.feishu_client import FeishuClient
from src.holiday_gate import is_cn_market_open, next_cn_trading_day

logger = logging.getLogger(__name__)
tz_cn = timezone(timedelta(hours=8))

# ═══════════════════════════════════════════════════════════════
# 0. 名称清洗 + 容错映射
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# -1. 自动推断工具（统一从 classification 模块引用）
# ═══════════════════════════════════════════════════════════════


def _infer_asset_class(code: str, name: str = "") -> str:
    """根据代码+名称推断资产大类，委托给 classification 模块。"""
    from src.classification import infer_asset_class as _cls_infer
    return _cls_infer(code, name)


def _infer_vehicle(code: str, name: str = "") -> str:
    """根据代码格式+名称推断投资载体。"""
    from src.classification import get_investment_vehicle
    return get_investment_vehicle(code, name)


def _detect_hk_sehk_code(raw_name: str) -> str:
    """检测并处理港股 SEHK 格式的产品名称。

    例如 "3121 SEHK" → 返回港股代码 "03121"（补齐 5 位）
          "3121.HK"  → 同上
    """
    import re
    name = str(raw_name).strip().upper()
    # 匹配 "3121 SEHK" 或 "03121 SEHK" 格式
    m = re.match(r"(\d{4,5})\s*(?:\.HK|SEHK|HK)", name)
    if m:
        code_num = m.group(1)
        return code_num.zfill(5)  # 补齐到 5 位
    # 匹配 "3121.HK" 格式
    m = re.match(r"(\d{4,5})\.HK", name)
    if m:
        return m.group(1).zfill(5)
    return ""


def _hk_stock_name(code: str) -> str:
    """通过 yfinance 获取港股名称（如 03121 → 三星高息房托ETF）。"""
    try:
        import yfinance as yf
        t = yf.Ticker(f"{int(code)}.HK")
        info = t.info
        return info.get("shortName") or info.get("longName") or f"港股{code}"
    except Exception:
        return f"港股{code}"


def _get_hkd_cny_rate() -> float:
    """获取港元兑人民币汇率（中行折算价）。"""
    try:
        import os as _os
        for _k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','all_proxy','ALL_PROXY'):
            _os.environ.pop(_k, None)
        import akshare as ak
        df = ak.currency_boc_sina()
        # 中行折算价 列是基准汇率为人民币/100外币
        if '中行折算价' in df.columns:
            hkd_row = df[df['中行折算价'].notna()]
            # 中行折算价对所有货币通用，需要按行找港元
        # 直接用固定汇率兜底
    except Exception:
        pass
    # 兜底: ~0.92 即 100 HKD = 92 CNY
    return 0.92


def _auto_detect_fund_code(name: str) -> str:
    """通过 akshare 全市场基金表模糊匹配产品名称 → 基金代码。

    首次调用时下载全量基金列表（~27000条），后续缓存到模块级变量。
    只在 pending_resolver 遇到新品且无法从已知标的匹配时才触发。

    Returns:
        基金代码（6位数字），未匹配返回空字符串。
    """
    if not name or len(str(name).strip()) < 4:
        return ""

    # 优先检测港股 SEHK 格式
    hk_code = _detect_hk_sehk_code(name)
    if hk_code:
        return hk_code

    # 缓存：一次 session 只下载一次
    global _FUND_NAME_CACHE
    if "_FUND_NAME_CACHE" not in globals():
        _FUND_NAME_CACHE = None

    if _FUND_NAME_CACHE is None:
        try:
            import os as _os
            for _k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','all_proxy','ALL_PROXY'):
                _os.environ.pop(_k, None)
            import akshare as _ak
            df = _ak.fund_name_em()
            if df is not None and not df.empty:
                _FUND_NAME_CACHE = df
                logger.info("[pending] 基金代码缓存已加载，%d 条", len(df))
        except Exception:
            _FUND_NAME_CACHE = False  # 失败不重试
            return ""

    if _FUND_NAME_CACHE is False or _FUND_NAME_CACHE is None:
        return ""

    df = _FUND_NAME_CACHE
    q = str(name).strip()
    # 优先精确匹配
    exact = df[df["基金简称"] == q]
    if len(exact) > 0:
        return str(exact.iloc[0]["基金代码"])

    # 取前6个字符的子串匹配（去掉基金公司名，如"工银瑞信睿智进取"）
    for prefix_len in (12, 10, 8, 6, 4):
        sub = q[:prefix_len]
        matches = df[df["基金简称"].str.contains(sub, na=False)]
        if len(matches) > 0:
            return str(matches.iloc[0]["基金代码"])

    return ""


def _fuzzy_find_code(name: str, known_codes: dict[str, str]) -> str:
    """从已知标的列表中模糊匹配代码。"""
    from src.pending_resolver import _normalize_name
    q = _normalize_name(name)
    for k, v in known_codes.items():
        if q in _normalize_name(k) or _normalize_name(k) in q:
            return v
    return ""


FUND_NAME_MAPPING: dict[str, str] = {
    "摩根标普500指数(QDII)C": "摩根标普500指数(QDII)C",
    "摩根标普500指数（QDII）C": "摩根标普500指数(QDII)C",
    "景顺长城纳斯达克科技市值加权ETF联接(QDII)C": "景顺长城纳斯达克科技市值加权ETF联接(QDII)C",
    "建信短债债券C": "建信短债债券C",
    "易方达沪深300ETF联接C": "易方达沪深300ETF联接C",
    "富国上海金ETF联接C": "富国上海金ETF联接C",
    "富国中证港股通互联网ETF联接C": "富国中证港股通互联网ETF联接C",
    "华宝中证沪港深新消费指数C": "华宝中证沪港深新消费指数C",
}


def _normalize_name(raw: str) -> str:
    """清洗产品名称：统一括号、去空格、全角→半角。"""
    if not raw:
        return ""
    s = str(raw).strip()
    s = re.sub(r"\s+", "", s)
    s = s.replace("（", "(").replace("）", ")")
    s = s.replace("Ａ", "A").replace("Ｂ", "B").replace("Ｃ", "C")
    s = s.replace("ａ", "a").replace("ｂ", "b").replace("ｃ", "c")
    s = s.replace("０", "0").replace("１", "1").replace("２", "2")
    s = s.replace("３", "3").replace("４", "4").replace("５", "5")
    s = s.replace("６", "6").replace("７", "7").replace("８", "8")
    s = s.replace("９", "9")
    return s


def _collect_name_matches(query: str, name_to_rec: dict[str, dict]) -> list[dict]:
    """收集**全部**候选底仓记录：先精确归一化匹配，无精确则取全部子串命中。

    ⚠️ 返回列表而不是首个命中，是因为"取首个"会在下述场景**静默记错**：
    底仓同时存在 `……联接(QDII)A / C / E` 多行时，一个**缺份额类别字母**的名称
    （如 `……联接(QDII)`）会同时子串命中多行，取首个就把份额记到错误的类别上。
    """
    q = _normalize_name(query)
    if not q:
        return []
    exact = [rec for name, rec in name_to_rec.items() if _normalize_name(name) == q]
    if exact:
        return exact
    return [rec for name, rec in name_to_rec.items()
            if q in _normalize_name(name) or _normalize_name(name) in q]


def _distinct_targets(recs: list[dict]) -> set[str]:
    """把候选记录折叠成"不同标的"的集合（优先用标的代码，无代码则用名称）。"""
    out = set()
    for r in recs:
        code = str(r.get("标的代码") or "").strip()
        out.add(code or _normalize_name(str(r.get("标的名称") or "")))
    return {x for x in out if x}


def _find_holding_conflicts(query: str, name_to_rec: dict[str, dict]) -> list[dict]:
    """名称在底仓命中**多只不同标的**时返回这些候选，否则返回空列表。"""
    matches = _collect_name_matches(query, name_to_rec)
    return matches if len(_distinct_targets(matches)) > 1 else []


def _fmt_conflicts(recs: list[dict]) -> str:
    """把歧义候选渲染成 `名称(代码)` 列表，用于告警文案。"""
    return "、".join(f"{r.get('标的名称')}({r.get('标的代码') or '无代码'})" for r in recs)


def _fuzzy_match_product(query: str, name_to_rec: dict[str, dict]) -> Optional[dict]:
    """在底仓表记录中用归一化名称匹配产品。

    Returns:
        匹配到的底仓记录 dict（含 _record_id, 标的代码, 持仓份额, 成本均价 等），
        未匹配**或命中多只不同标的（歧义）**时返回 None。
    """
    q = _normalize_name(query)
    if not q:
        return None

    # 1) 精确 / 子串匹配（候选多于一只标的 → 拒绝猜测，交由调用方告警）
    matches = _collect_name_matches(query, name_to_rec)
    targets = _distinct_targets(matches)
    if len(targets) == 1:
        return matches[0]
    if len(targets) > 1:
        return None

    # 2) 容错字典
    for alias, canonical in FUND_NAME_MAPPING.items():
        if _normalize_name(alias) == q:
            for name, rec in name_to_rec.items():
                if _normalize_name(name) == _normalize_name(canonical):
                    return rec

    return None


# ═══════════════════════════════════════════════════════════════
# 1. T 日计算
# ═══════════════════════════════════════════════════════════════

def _get_t_day(trade_time: datetime) -> date:
    trade_date = trade_time.date()
    cutoff = trade_time.replace(hour=15, minute=0, second=0, microsecond=0)
    if is_cn_market_open(trade_date) and trade_time < cutoff:
        return trade_date
    return next_cn_trading_day(trade_date + timedelta(days=1))


# ═══════════════════════════════════════════════════════════════
# 2. 数据提取
# ═══════════════════════════════════════════════════════════════

def _extract_product_name(product_field) -> str:
    if product_field is None:
        return ""
    if isinstance(product_field, str):
        return product_field
    if isinstance(product_field, list) and len(product_field) > 0:
        item = product_field[0]
        if isinstance(item, dict):
            return item.get("text", "")
        return str(item)
    return str(product_field)


def _parse_trade_time(time_field) -> Optional[datetime]:
    if time_field is None:
        return None
    try:
        ts = float(time_field)
        if ts > 1e12:
            ts = ts / 1000
        return datetime.fromtimestamp(ts, tz=tz_cn)
    except (ValueError, OSError, TypeError):
        pass
    try:
        return datetime.strptime(str(time_field), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz_cn)
    except ValueError:
        pass
    return None


def _parse_action(action_field) -> str:
    """归一化买卖方向 → buy / sell / convert / unknown。

    飞书单选字段可能是中文（"买入"/"卖出"/"转换"）或英文，
    这里统一归一化为英文，确保下游分支判断正确。

    🔥 2026-09-17 convert 改造：**未知方向不再兜底为 buy**。
    旧逻辑 `return "buy"` 会把任何无法识别的方向当成买入执行
    （转出方份额不扣、转入方凭空增加）—— 静默污染底仓。
    现在返回 "unknown"，由主流程跳过并告警，宁可漏记不可记错。
    """
    if action_field is None:
        return "unknown"
    raw = str(action_field[0]) if (isinstance(action_field, list) and action_field) else str(action_field)
    raw_lower = raw.strip().lower()
    # 中文 → 英文（精确）
    if raw_lower in ("卖出", "sell"):
        return "sell"
    if raw_lower in ("买入", "buy"):
        return "buy"
    if raw_lower in ("转换", "convert", "基金转换"):
        return "convert"
    # 模糊包含
    if "转换" in raw_lower or "convert" in raw_lower:
        return "convert"
    if "卖" in raw_lower or "赎回" in raw_lower:
        return "sell"
    if "买" in raw_lower or "申购" in raw_lower or "定投" in raw_lower:
        return "buy"
    return "unknown"


def _parse_shares(field) -> Optional[float]:
    """解析份额字段（数字或字符串）→ float。

    返回 None 表示无有效份额（空值 / 非数字 / <= 0）。
    """
    if field is None or field == "" or field == []:
        return None
    if isinstance(field, list):
        field = field[0] if field else None
        if field is None:
            return None
    try:
        v = float(field)
    except (ValueError, TypeError):
        return None
    return v if v > 0 else None


# ═══════════════════════════════════════════════════════════════
# 3. 净值抓取
# ═══════════════════════════════════════════════════════════════

def _fetch_nav_on_date(code: str, target_date: date) -> Optional[float]:
    """若 T 日净值尚未发布返回 None（触发 QDII 懒加载跳过）。

    支持：场外基金（akshare）+ 港股（yfinance）。
    """
    # ── 港股（5 位数字）→ yfinance ──
    if code.isdigit() and len(code) == 5:
        try:
            import yfinance as yf
            # yfinance 要求港股代码不带前导零（1810.HK 而非 01810.HK）
            symbol = f"{int(code)}.HK"
            t = yf.Ticker(symbol)
            df = t.history(start=target_date, end=target_date + timedelta(days=3))
            if not df.empty:
                close = float(df["Close"].iloc[0])
                return round(close, 4)
            # 精确日期无数据（如 timestamp 错误导致 T 日是过去的日期），
            # 尝试扩大到最近 30 天兜底
            df_wide = t.history(start=target_date - timedelta(days=30), end=target_date + timedelta(days=2))
            if not df_wide.empty:
                close = float(df_wide["Close"].iloc[-1])
                logger.warning("[%s] T日 %s 无数据，用最近交易日 %s 净值兜底", code, target_date, df_wide.index[-1].strftime("%Y-%m-%d"))
                return round(close, 4)
        except Exception as e:
            logger.warning("[%s] 港股净值拉取失败: %s", code, str(e)[:100])
        return None

    # ── 场外基金 → akshare ──
    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare 未安装")
        return None

    try:
        df = ak.fund_open_fund_info_em(code)
    except Exception as e:
        logger.warning("[%s] 净值拉取失败: %s", code, str(e)[:100])
        return None

    if df is None or df.empty or "单位净值" not in df.columns or "净值日期" not in df.columns:
        logger.warning("[%s] 净值数据为空或字段缺失", code)
        return None

    target_str = target_date.strftime("%Y-%m-%d")
    for idx in range(len(df) - 1, -1, -1):
        if str(df["净值日期"].iloc[idx])[:10] == target_str:
            nav = df["单位净值"].iloc[idx]
            if pd.isna(nav):
                return None
            return round(float(nav), 4)

    return None


# ═══════════════════════════════════════════════════════════════
# 4. 成本/份额计算
# ═══════════════════════════════════════════════════════════════

def _apply_buy(holding_rec: dict, confirm_amount: float, confirm_nav: float, confirm_shares: float):
    """买入：加权平均法更新持仓份额与成本均价。

    新份额 = 原份额 + 确认份额
    新成本 = (原份额 × 原成本价 + 本次交易金额) / 新份额（若无旧成本，本次金额/本次份额）
    """
    old_shares = float(holding_rec.get("持仓份额", 0) or 0)
    old_cost = float(holding_rec.get("成本均价", 0) or 0)
    new_shares = old_shares + confirm_shares
    if old_shares > 0 and old_cost > 0:
        new_cost = round(
            (old_shares * old_cost + confirm_amount) / new_shares, 4
        )
    else:
        new_cost = round(confirm_amount / confirm_shares, 4)
    return {"持仓份额": round(new_shares, 4), "成本均价": round(new_cost, 2)}


def _apply_sell(holding_rec: dict, confirm_shares: float):
    """卖出：只减份额，成本价不变。

    返回 (holding_update_dict, is_effectively_sold_out: bool)。
    因买入积累的份额精度(4位)与卖出确认份额精度(2位)不对齐，
    is_effectively_sold_out 在剩余 < 0.1 份时也视为清仓，避免残留零头。
    """
    old_shares = float(holding_rec.get("持仓份额", 0) or 0)
    new_shares = max(old_shares - confirm_shares, 0)
    new_shares = round(new_shares, 4)
    sold_out = new_shares < 0.1
    return {"持仓份额": new_shares}, sold_out


def _cache_apply(name_to_rec: dict, holding: dict, update: Optional[dict]) -> None:
    """底仓写回飞书**成功后**，把同一份变更同步进本次运行的内存缓存。

    为什么必须做（2026-09-17 真机事故）
        `name_to_rec` 在整个运行开始时只读一次底仓表，之后不再刷新。
        同一个运行里可能有多笔 pending 命中**同一只底仓**——典型：3 笔转换的
        转入腿 + 1 笔买入指向同一个 E 类份额。若不刷新缓存，后一笔会基于
        「运行开始时的旧份额」重新计算并整体覆写，把前一笔刚加/扣的份额抹掉，
        而且**完全不报错**（流水表份额、状态、净值全都正确）。

        事故结果：C 类应 308.23-280 = 28.23，实际 278.23（只扣了最后一笔 30 份）；
                  E 类应 0+277.48+16.22 = 293.70，实际 16.22（只落了最后一笔买入）。
        两处误差方向相反，总市值只差约 -¥86，从简报里根本看不出来。

    Args:
        holding: 缓存里的那份 dict（`_ensure_holding` / `_fuzzy_match_product`
                 返回的对象就是缓存对象本身，故原地 update 即生效）
        update: 刚写进飞书的字段；传 None 表示该底仓记录已被删除（清仓），
                必须从缓存摘除——否则同批次后续笔会继续写一条已删记录
    """
    if update is None:
        rid = holding.get("_record_id")
        for key in [k for k, v in name_to_rec.items()
                    if v is holding or (rid and v.get("_record_id") == rid)]:
            name_to_rec.pop(key, None)
        return
    holding.update(update)


# ═══════════════════════════════════════════════════════════════
# 4b. 基金转换（convert）—— 2026-09-17 新增
# ═══════════════════════════════════════════════════════════════


def _ensure_holding(name: str, *, client, name_to_rec: dict, all_known_codes: dict,
                    dry_run: bool, label: str = ""):
    """查找底仓记录；不存在则自动识别代码并创建。

    Returns:
        (holding_dict | None, error_reason | None)
    """
    h = _fuzzy_match_product(name, name_to_rec)
    if h:
        return h, None

    # ⚠️ 歧义保护：名称缺份额类别字母时，底仓里的 A/C/E 多行会被同时命中。
    #    此时**必须拒绝猜测**——若继续走下面的"新品自动建底仓"分支，
    #    会凭空多出一行无类别底仓，或把份额记到错误的类别上。
    conflicts = _find_holding_conflicts(name, name_to_rec)
    if conflicts:
        return None, (f"{label}「{name}」在底仓命中多只标的"
                      f"（{_fmt_conflicts(conflicts)}）——名称缺少份额类别字母，"
                      f"拒绝猜测，请核对产品全称后重录")

    code = _auto_detect_fund_code(name) or _fuzzy_find_code(name, all_known_codes)
    if not code:
        return None, f"{label}「{name}」无法识别标的代码，请先在底仓表手工添加"

    if dry_run:
        # dry-run 不写表，返回一个临时占位底仓用于计算
        return {"_record_id": "", "标的名称": name, "标的代码": code,
                "持仓份额": 0, "成本均价": 0}, None

    cls = _infer_asset_class(code, name)
    vehicle = _infer_vehicle(code, name)
    new_id = client.create_record("底仓表", {
        "标的名称": name, "标的代码": code, "资产大类": cls,
        "投资载体": vehicle, "持仓份额": 0, "成本均价": 0, "现价": 0,
    })
    if not new_id:
        return None, f"{label}「{name}」底仓记录创建失败"

    holding = {"_record_id": new_id, "标的名称": name, "标的代码": code,
               "持仓份额": 0, "成本均价": 0}
    name_to_rec[name] = holding
    all_known_codes[name] = code
    logger.info("  ✅ %s底仓记录已创建: %s", label, new_id)
    return holding, None


def _resolve_convert(rec: dict, *, client, name_to_rec: dict, all_known_codes: dict,
                     dry_run: bool):
    """处理一条 convert（基金转换）记录：转出腿卖出 + 转入腿买入。

    一行 = 一次转换申请，字段语义：
        `产品名称`  = 转出标的
        `转入标的`  = 转入标的
        `转出份额`  = 申请转出份额（份额驱动，转换单没有金额）
        `交易金额`  = 空（转换单无金额）

    设计要点：
        D2 份额优先 —— 用 `转出份额`，不依赖 `金额/净值`
        D5 确认份额用户填优先，缺失才用 转出份额×转出净值/转入净值 折算
        两腿都成功才把状态置 completed；任一失败保持 pending 并明确告警

    Returns:
        (outcome, detail)；outcome ∈ {"resolved", "dry_run", "skipped", "error"}
    """
    record_id = rec.get("_record_id", "")
    out_name = _extract_product_name(rec.get("产品名称"))
    in_name = _extract_product_name(rec.get("转入标的"))
    trade_time = _parse_trade_time(rec.get("交易时间"))
    base = {"product": out_name or "?", "record_id": record_id}

    if not out_name or not in_name:
        logger.warning("[%s] 转换行缺少「产品名称」或「转入标的」，跳过", record_id)
        return "skipped", {**base, "status": "skipped",
                           "reason": "转换行缺少「产品名称」或「转入标的」"}
    if not trade_time:
        logger.warning("[%s] 转换行交易时间无法解析，跳过", record_id)
        return "skipped", {**base, "status": "skipped", "reason": "交易时间无法解析"}

    out_shares = _parse_shares(rec.get("转出份额"))
    if out_shares is None:
        logger.warning("[%s] 转换行缺少有效「转出份额」，跳过", record_id)
        return "skipped", {**base, "status": "skipped",
                           "reason": "转换行缺少「转出份额」（转换单只有份额，没有金额）"}

    h_out, err = _ensure_holding(out_name, client=client, name_to_rec=name_to_rec,
                                 all_known_codes=all_known_codes, dry_run=dry_run, label="转出")
    if err:
        logger.warning("[%s] %s", record_id, err)
        return "skipped", {**base, "status": "skipped", "reason": err}

    pair = f"{out_name} → {in_name}"
    h_in, err = _ensure_holding(in_name, client=client, name_to_rec=name_to_rec,
                                all_known_codes=all_known_codes, dry_run=dry_run, label="转入")
    if err:
        logger.warning("[%s] %s", record_id, err)
        return "skipped", {**base, "product": pair, "status": "skipped", "reason": err}

    out_code = str(h_out.get("标的代码", "") or "")
    in_code = str(h_in.get("标的代码", "") or "")
    t_day = _get_t_day(trade_time)

    logger.info("[%s] 转换 %s → %s | %.2f 份 | T日=%s",
                record_id, str(out_name)[:16], str(in_name)[:16], out_shares, t_day)

    # ── 双腿净值（任一未发布 → 保持 pending，绝不写一半）──
    nav_out = _fetch_nav_on_date(out_code, t_day)
    nav_in = _fetch_nav_on_date(in_code, t_day)
    if nav_out is None or nav_in is None:
        miss = []
        if nav_out is None:
            miss.append(f"转出 {out_code}")
        if nav_in is None:
            miss.append(f"转入 {in_code}")
        reason = f"{t_day} 净值未发布（{'、'.join(miss)}）"
        logger.info("  → %s，保持 pending", reason)
        return "skipped", {"product": pair, "code": out_code, "t_day": str(t_day),
                           "status": "skipped", "reason": reason}

    # ── D5：用户确认优先，但需要「确认份额 + 确认净值」同时填写才算显式覆盖 ──
    # 闸门原因：快捷指令会把 OCR 出的"申请转出份额"写进「确认份额」，
    # 若只看该列就会把转出份额误当成转入份额。确认净值同填 = 用户确实拿到了确认单。
    user_shares = _parse_shares(rec.get("确认份额"))
    user_nav = _parse_shares(rec.get("确认净值"))
    if user_shares is not None and user_nav is not None:
        in_shares, nav_in_eff, source = round(user_shares, 2), user_nav, "用户填"
    else:
        in_shares, nav_in_eff, source = round(out_shares * nav_out / nav_in, 2), nav_in, "系统算"
    if in_shares <= 0:
        return "skipped", {"product": pair, "status": "skipped",
                           "reason": "折算后转入份额为 0，请检查净值"}

    out_amount = round(out_shares * nav_out, 2)      # 转出金额（审计用）
    in_amount = round(in_shares * nav_in_eff, 2)     # 转入成本

    # ── 两腿运算（复用既有 buy/sell 逻辑，不新写计算）──
    out_update, sold_out = _apply_sell(h_out, out_shares)
    in_update = _apply_buy(h_in, in_amount, nav_in_eff, in_shares)

    if dry_run:
        logger.info("  [DRY] 转出 %.2f 份 @%s = ¥%.2f %s| 转入 %.2f 份 @%s → 新成本=%s",
                    out_shares, nav_out, out_amount, "（清仓）" if sold_out else " ",
                    in_shares, nav_in_eff, in_update.get("成本均价"))
        return "dry_run", {"product": pair, "code": out_code, "t_day": str(t_day),
                           "nav": nav_in_eff, "shares": in_shares, "amount": out_amount,
                           "status": "dry_run",
                           "note": f"转出 {out_shares} 份 @{nav_out}={out_amount}"
                                   + ("（清仓）" if sold_out else "")}

    # ── 写回：两腿都成功才置 completed ──
    ok_out = (client.delete_record("底仓表", h_out["_record_id"]) if sold_out
              else client.update_record("底仓表", h_out["_record_id"], out_update))
    ok_in = client.update_record("底仓表", h_in["_record_id"], in_update)

    # ── 逐腿同步内存缓存（哪一腿写成功就同步哪一腿）──
    #    同一次运行里可能还有别的 pending 命中同一只底仓；不同步会让后续笔
    #    基于旧份额覆写，把这一腿刚加/扣的份额抹掉（见 _cache_apply 注释）。
    #    ⚠️ 先在同步前取旧值快照，供下面的失败告警文案使用。
    out_left_expected = max(float(h_out.get("持仓份额", 0) or 0) - out_shares, 0)
    if ok_out:
        _cache_apply(name_to_rec, h_out, None if sold_out else out_update)
    if ok_in:
        _cache_apply(name_to_rec, h_in, in_update)

    if not (ok_out and ok_in):
        logger.error(
            "  ❌ 转换写回底仓失败（转出=%s 转入=%s），状态保持 pending。"
            "⚠️ 请手工核对底仓：%s 应剩 %.4f 份、%s 应加 %.2f 份 @%s（%s）",
            ok_out, ok_in, str(out_name)[:20], out_left_expected,
            str(in_name)[:20], in_shares, nav_in_eff, source,
        )
        return "error", {"product": pair, "code": out_code, "status": "error",
                         "reason": "两腿写回失败，已告警，状态保持 pending"}

    ok3 = client.update_record("交易流水表", record_id, {
        "确认份额": in_shares, "确认净值": nav_in_eff, "状态": "completed",
    })
    if ok3:
        logger.info("  ✅ 转换完成：转出 %.2f 份 @%s | 转入 %.2f 份 @%s（%s）",
                    out_shares, nav_out, in_shares, nav_in_eff, source)
    else:
        logger.error("  ⚠️ 底仓已更新，但流水表状态回写失败，请手工置 completed")

    return "resolved", {"product": pair, "code": out_code, "t_day": str(t_day),
                        "nav": nav_in_eff, "shares": in_shares, "amount": out_amount,
                        "status": "resolved"}


# ═══════════════════════════════════════════════════════════════
# 5. 主流程
# ═══════════════════════════════════════════════════════════════

def resolve_pending(dry_run: bool = False) -> dict:
    # 🔥 2026-09-05 P0 改造：本地开发禁止跑这个命令（会修改真飞书表）
    from src.env import is_production
    if not is_production():
        raise RuntimeError(
            "resolve_pending() 涉及飞书表写入，本地禁止执行。\n"
            "本地 dry-run 请用: resolve_pending(dry_run=True) 且加 mock client。"
        )

    client = FeishuClient()

    logger.info("正在读取交易流水表…")
    all_records = client.list_records("交易流水表")
    pending = [r for r in all_records if r.get("状态") == "pending"]
    if not pending:
        logger.info("无 pending 记录")
        return {"resolved": 0, "skipped": 0, "errors": 0, "details": []}
    logger.info("共 %d 条 pending 记录", len(pending))

    # ── 构建名称 → 底仓记录 的映射 ──
    logger.info("正在读取底仓表…")
    holdings = client.list_records("底仓表")
    name_to_rec: dict[str, dict] = {}
    all_known_codes: dict[str, str] = {}
    for h in holdings:
        name = h.get("标的名称", "")
        code = h.get("标的代码", "")
        if name and code:
            name_to_rec[name] = h
            all_known_codes[name] = code
    logger.info("底仓表映射：%d 条", len(name_to_rec))

    # 也读雷达观测表 → 扩充 known_codes
    try:
        radar_recs = client.list_records("雷达观测表")
        for r in radar_recs:
            rname = r.get("标的名称", "")
            rcode = r.get("标的代码", "")
            if rname and rcode:
                all_known_codes[rname] = rcode
    except Exception:
        pass

    resolved, skipped, errors = 0, 0, 0
    details = []

    for rec in pending:
        record_id = rec.get("_record_id", "")
        product_name = _extract_product_name(rec.get("产品名称"))
        trade_time = _parse_trade_time(rec.get("交易时间"))
        action = _parse_action(rec.get("买卖方向"))

        # ── 方向无法识别 → 跳过并告警（绝不再按买入兜底）──
        if action == "unknown":
            logger.error("[%s] 无法识别「买卖方向」=%r，跳过（旧逻辑会误判为买入）",
                         record_id, rec.get("买卖方向"))
            details.append({"product": product_name or "?", "record_id": record_id,
                            "status": "skipped", "reason": "买卖方向无法识别"})
            skipped += 1
            continue

        if not product_name:
            logger.warning("[%s] 产品名称为空，跳过", record_id)
            skipped += 1; continue
        if not trade_time:
            logger.warning("[%s] 交易时间无法解析，跳过", record_id)
            skipped += 1; continue

        # ── 基金转换：独立分支，只要求「转出份额」，不要求金额 ──
        if action == "convert":
            outcome, detail = _resolve_convert(
                rec, client=client, name_to_rec=name_to_rec,
                all_known_codes=all_known_codes, dry_run=dry_run,
            )
            details.append(detail)
            if outcome in ("resolved", "dry_run"):
                resolved += 1
            elif outcome == "error":
                errors += 1
            else:
                skipped += 1
            continue

        # ── 金额 / 份额（D2 份额优先：有「转出份额」就不依赖金额）──
        raw_amount = rec.get("交易金额")
        try:
            amount: Optional[float] = (
                float(raw_amount) if raw_amount not in (None, "", []) else None
            )
        except (ValueError, TypeError):
            amount = None
        shares_input = _parse_shares(rec.get("转出份额"))

        if action == "buy" and not amount:
            logger.warning("[%s] 买入缺少有效「交易金额」，跳过", record_id)
            skipped += 1; continue
        if amount is None and shares_input is None:
            logger.warning("[%s] 「交易金额」与「转出份额」均为空，跳过", record_id)
            skipped += 1; continue
        if amount is None:
            amount = 0.0

        # 匹配底仓
        holding = _fuzzy_match_product(product_name, name_to_rec)
        journal_code = rec.get("标的代码", "") or ""

        # ⚠️ 歧义保护：名称缺份额类别字母时会同时命中底仓里的 A/C/E 多行。
        #    绝不能落到下面的"新品自动建底仓"分支——那会凭空多出一行无类别底仓。
        #    （2026-09-17 真机踩坑：快捷指令 few-shot 示例写了具体基金名，
        #     模型照抄示例的类别字母，把 E 类买入记成了 C 类。）
        if not holding:
            conflicts = _find_holding_conflicts(product_name, name_to_rec)
            if conflicts:
                logger.error("[%s] 「%s」在底仓命中多只标的：%s —— 名称缺少份额类别字母，"
                             "拒绝猜测，跳过", record_id, product_name, _fmt_conflicts(conflicts))
                details.append({"product": product_name, "record_id": record_id,
                                "status": "skipped",
                                "reason": "底仓名称歧义（缺少份额类别字母）"})
                skipped += 1
                continue

        # ── 新品：自动推断标的代码 + 资产大类 ──
        if not holding:
            # 1. 尝试自动查代码（akshare 全市场基金表）
            if not journal_code:
                journal_code = _auto_detect_fund_code(product_name)
            if not journal_code:
                # 从雷达观测表、底仓表所有标的名称模糊匹配
                journal_code = _fuzzy_find_code(product_name, all_known_codes)
            if not journal_code:
                logger.info("[%s] 新品「%s」无法自动查代码，将用 LLM 搜索", record_id, product_name)

            # 2. 先查港股 SEHK 真实名称（必须在分类之前）
            is_sehk = False
            if journal_code and len(journal_code) == 5:
                if "SEHK" in str(product_name).upper() or str(product_name).strip().isdigit():
                    is_sehk = True
                    real_name = _hk_stock_name(journal_code)
                    if real_name and real_name != f"港股{journal_code}":
                        product_name = real_name
                        # 同时更新交易流水表中的产品名称
                        try:
                            client.update_record("交易流水表", record_id, {"产品名称": real_name})
                            logger.info("  产品名称已修正: %s → %s", str(rec.get('产品名称',''))[:20], real_name)
                        except Exception:
                            pass

            # 3. 资产大类 + 投资载体（使用查到的真实名称）
            if not journal_code:
                asset_cls = "待分类"
                vehicle = "未知"
            else:
                asset_cls = _infer_asset_class(journal_code, product_name)
                vehicle = _infer_vehicle(journal_code, product_name)

            logger.info("[%s] 新品「%s」(code=%s, cls=%s, vehicle=%s)→ 自动创建底仓记录", record_id, product_name, journal_code or "?", asset_cls, vehicle)

            if dry_run:
                details.append({"product": product_name, "code": journal_code or "待查", "amount": amount,
                                "status": "dry_run", "note": "将创建底仓记录"})
                resolved += 1
                skipped += 1
                continue

            if not journal_code:
                skipped += 1
                details.append({"product": product_name, "record_id": record_id,
                                "status": "skipped", "reason": "新品无法自动识别代码，请手动在底仓表添加"})
                continue

            # 创建底仓记录（同时写入资产大类 + 投资载体）
            new_id = client.create_record("底仓表", {
                "标的名称": product_name,
                "标的代码": journal_code,
                "资产大类": asset_cls,
                "投资载体": vehicle,
                "持仓份额": 0,
                "成本均价": 0,
                "现价": 0,
            })
            if new_id:
                logger.info("  ✅ 底仓记录已创建: %s", new_id)
                holding = {
                    "_record_id": new_id,
                    "标的名称": product_name,
                    "标的代码": journal_code,
                    "持仓份额": 0,
                    "成本均价": 0,
                }
                name_to_rec[product_name] = holding
            else:
                logger.error("  ❌ 底仓记录创建失败")
                errors += 1
                continue

        code = holding.get("标的代码", "") or journal_code
        t_day = _get_t_day(trade_time)
        today = date.today()

        logger.info("[%s] %s | %s | %s | T日=%s | 今天=%s",
                     code[:8], product_name[:20],
                     f"¥{amount:.2f}" if amount else f"{shares_input}份",
                     trade_time.strftime("%m-%d %H:%M"), t_day, today)

        # ── 净值抓取（QDII 懒加载：净值未发布则静默跳过） ──
        nav = _fetch_nav_on_date(code, t_day)
        if nav is None:
            logger.info("  → %s 净值尚未发布（T+n 延迟），保持 pending", t_day)
            details.append({"product": product_name, "code": code, "amount": amount,
                            "t_day": str(t_day), "status": "skipped",
                            "reason": f"{t_day} 净值未发布"})
            skipped += 1
            continue  # ← 关键熔断：净值不到，雷打不动 pending

        # D2 份额优先：有「转出份额」直接用，否则回退 金额/净值
        if shares_input is not None:
            confirm_shares = round(shares_input, 2)
            trade_amount = amount if amount else round(confirm_shares * nav, 2)
            logger.info("  → 份额驱动：%.2f 份 @%s = ¥%.2f", confirm_shares, nav, trade_amount)
        else:
            confirm_shares = round(amount / nav, 2)
            trade_amount = amount

        # ── 买卖分支 ──
        if action == "sell":
            holding_update, sold_out = _apply_sell(holding, confirm_shares)
            cost_line = ""
        else:
            holding_update = _apply_buy(holding, trade_amount, nav, confirm_shares)
            cost_line = f" 新成本价={holding_update.get('成本均价','?')}"
            sold_out = False

        if dry_run:
            action_note = "将删除底仓记录" if sold_out else ""
            logger.info("  [DRY] NAV=%s 份额=%s%s %s", nav, confirm_shares, cost_line, action_note)
            details.append({"product": product_name, "code": code, "amount": trade_amount,
                            "t_day": str(t_day), "nav": nav, "shares": confirm_shares,
                            "status": "dry_run", "note": action_note})
            resolved += 1
        else:
            # 交易流水表：更新确认净值/份额/状态
            ok1 = client.update_record("交易流水表", record_id, {
                "确认净值": nav, "确认份额": confirm_shares, "状态": "completed",
            })
            # 底仓表：全卖光 → 删除；否则更新
            if sold_out:
                ok2 = client.delete_record("底仓表", holding["_record_id"])
                logger.info("  💨 全部卖出，底仓记录已删除")
            else:
                ok2 = client.update_record("底仓表", holding["_record_id"], holding_update)
            # 只要底仓写成功就同步缓存（与 ok1 无关）：否则同批次后续笔
            # 会基于旧份额覆写，把这一笔的份额抹掉
            if ok2:
                _cache_apply(name_to_rec, holding, None if sold_out else holding_update)
            if ok1 and ok2:
                logger.info("  ✅ NAV=%s 份额=%s%s", nav, confirm_shares, cost_line)
                details.append({"product": product_name, "code": code, "amount": trade_amount,
                                "t_day": str(t_day), "nav": nav, "shares": confirm_shares,
                                "status": "resolved"})
                resolved += 1
            else:
                logger.error("  ❌ 写回飞书失败")
                errors += 1

    return {"resolved": resolved, "skipped": skipped, "errors": errors, "details": details}


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="Pending 交易自动确认器")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")

    print(f"\n{'='*55}\n   📋 Pending 交易自动确认器{' [DRY RUN]' if args.dry_run else ''}\n{'='*55}\n")
    result = resolve_pending(dry_run=args.dry_run)

    print("\n── 处理明细 ──")
    for d in result["details"]:
        name = str(d.get("product") or "?")[:25]
        if d["status"] in ("resolved", "dry_run"):
            nav = d.get("nav")
            shares = d.get("shares")
            amount = d.get("amount")
            if nav is not None and shares is not None:
                print(f"  {'🔍' if d['status']=='dry_run' else '✅'} {name:<27}"
                      f" ¥{float(amount or 0):>8.2f} → NAV={nav} 份额={shares}",
                      "[未写入]" if d["status"] == "dry_run" else "")
            else:
                print(f"  🔍 {name:<27} — {d.get('note', 'dry-run')}")
        elif d["status"] == "skipped":
            print(f"  ⏭️  {name:<27} — {d.get('reason', '未知')}")
        else:
            print(f"  ❌ {name:<27} — {d.get('reason', '未知')}")
    print(f"\n  确认: {result['resolved']} | 跳过: {result['skipped']} | 错误: {result['errors']}\n{'='*55}")


if __name__ == "__main__":
    main()
