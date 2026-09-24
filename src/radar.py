# -*- coding: utf-8 -*-
"""
雷达观测表 —— 隔离区状态机。

对飞书「雷达观测表」中的高波动卫星标的做双向信号检测
（抄底 + 追涨），每日早间/收盘前简报注入信号。

职责：
- 逐只抓取历史价格（yfinance → akshare 双源 fallback）
- 计算 5/10/20 日涨跌幅 + 趋势 + 20 日均线
- 判定抄底/追涨信号
- 写回飞书雷达表
- 产出简报嵌入文本

用法：
    python -m src.radar              # 扫描全部雷达标的
    python -m src.radar --dry-run    # 只算不写
    python -m src.radar --brief      # 仅产出简报文本
"""

from __future__ import annotations

import logging

from src.feishu_client import FeishuClient

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 信号阈值常量
# ═══════════════════════════════════════════════════════════════

THRESHOLD_BUY_SHORT = -5.0    # 10 日跌超 5% → 🟡 关注
THRESHOLD_BUY_LONG = -8.0     # 20 日跌超 8% → 🔵 底部反转
MA20_BREAK_RATIO = 1.03       # 追涨要求现价 ≤ 20 日线 × 1.03

# ── #32 超配闸门（2026-09-24）──
# 为什么需要：审计（TODO §1.14 证据⑤）发现 50 条推送里有 **11 条**把
# 「🟢 趋势加速」打在**已超配 15.5pp 的债券基金**上（招商产业债券A /
# 银华安颐中短债 / 兴业60天滚动短债C）。对超配大类发买入信号，与项目铁律
# 「自然稀释」直接矛盾，而且是**反向误导**。
# ⚠️ 阈值取 5.0pp：与 `strategy._determine_signal` 的 ±5 口径一致
#    （那里的 deviation 也是「百分点」）。低于该值的偏离属正常波动，
#    不该静默掉信号——闸门只拦"已经明显超配"的大类。
OVERWEIGHT_BLOCK_PP = 5.0


def _field_text(value) -> str:
    """把飞书字段值归一成字符串。

    飞书 bitable API 的字段返回形态不统一：文本/数字是标量，
    单选/多选是**数组**（实测底仓表的「资产大类」「投资载体」「标签」
    都是 `["美股资产"]` 这种形态）。只做 str() 会得到 `"['美股资产']"`
    这种带方括号的脏值，静默匹配不上任何大类。
    """
    if isinstance(value, (list, tuple)):
        return str(value[0]).strip() if value else ""
    return str(value).strip() if value is not None else ""


def _holding_market_value(h: dict) -> float:
    """取底仓市值，用于算大类权重。

    ⚠️ 优先用公式字段「市值」（飞书算好、已含汇率折算）。API 可能把它返回成
       字符串 `"284.18"` 或数字，故两种都试。
    ⚠️ 拿不到时回退「持仓份额 × 现价」：这对港股会漏掉汇率折算，但权重判定的
       量级不受影响 —— 好过整条闸门因为解析失败而**静默失效**。
    """
    mv = _field_text(h.get("市值"))
    if mv:
        try:
            if float(mv) > 0:
                return float(mv)
        except ValueError:
            pass
    try:
        return float(h.get("持仓份额") or 0) * float(h.get("现价") or 0)
    except (TypeError, ValueError):
        return 0.0


def _calc_overweight_classes(holdings: list[dict]) -> dict[str, float]:
    """算出各大类的「超配百分点」（实际权重 − 目标权重，单位 pp）。

    ⚠️ 大类取底仓表的「资产大类」列，**不用 `infer_asset_class()` 现算** ——
       后者对场外基金会触发 akshare 查询，扫描几十只标的会给 CI 引入新的
       挂死点（job 上限 25 分钟已经很紧，见 TODO §1.10）。
    ⚠️ 该列有已知失真（亚洲半导体/韩国被标成「港股资产」，见 TODO §1.15 ④），
       但在「超配判定」这个用途下不产生误判：港股真实 5.2%、失真后约 8.6%，
       两者都低于 10% 目标 → 都不会被误拦；而固收 +15.5pp 是真实的。
       根治见 #34（补「底层指数」字段）。
    ⚠️ 分母用**全部底仓市值**（含「待分类」）：待分类占掉份额会让其他大类
       权重偏低 → 更保守，不易误触发。
    """
    from src.constants import TARGET_WEIGHTS

    total = 0.0
    by_class: dict[str, float] = {}
    for h in holdings:
        mv = _holding_market_value(h)
        if mv <= 0:
            continue
        total += mv
        cls = _field_text(h.get("资产大类"))
        if cls:
            by_class[cls] = by_class.get(cls, 0.0) + mv

    if total <= 0:
        return {}

    return {
        cls: round((by_class.get(cls, 0.0) / total - target) * 100, 2)
        for cls, target in TARGET_WEIGHTS.items()
    }


# ═══════════════════════════════════════════════════════════════
# 投资载体推断（统一从 classification 模块引用）
# ═══════════════════════════════════════════════════════════════

from src.classification import get_investment_vehicle


# ═══════════════════════════════════════════════════════════════
# 历史价格抓取（yfinance 主 → akshare 兜底）
# ═══════════════════════════════════════════════════════════════

def _fetch_historical_prices(code: str, days: int = 25) -> dict | None:
    """抓取标的最近 N 个交易日的历史收盘价与日涨跌幅。

    数据源优先级：yfinance → akshare（与 market_data.py 一致）

    Args:
        code: 标的代码
        days: 需要的交易日天数（默认 25，覆盖 20 日窗口 + 缓冲）

    Returns:
        {"prices": [p1, p2, ...], "changes": [c1, c2, ...], "source": "yfinance"}
        失败返回 None。prices 和 changes 长度相等，按时间升序排列。
    """
    vehicle = get_investment_vehicle(code)

    if vehicle == "场内ETF":
        # 场内 ETF 分布在 A 股/港股/美股，按代码格式路由数据源
        if code.isdigit() and len(code) == 6:
            return _fetch_cn_historical(code, days)          # A 股 ETF
        if code.isdigit() and len(code) == 5:
            return _fetch_hk_historical(code, days)          # 港股 ETF
        if code.isalpha():
            return _fetch_us_historical(code, days)          # 美股 ETF
        logger.warning("[%s] 无法识别 ETF 市场，跳过", code)
        return None
    elif vehicle == "场外基金":
        return _fetch_fund_historical(code, days)
    elif vehicle == "个股":
        # 按代码进一步区分港股/美股
        if code.isdigit() and len(code) == 5:
            return _fetch_hk_historical(code, days)
        if code.isalpha():
            return _fetch_us_historical(code, days)
        logger.warning("[%s] 无法识别个股市场，跳过", code)
        return None
    else:
        logger.warning("[%s] 无法识别投资载体，跳过", code)
        return None


def _fetch_fund_historical(code: str, days: int) -> dict | None:
    """场外基金历史净值（akshare 单源）。"""
    try:
        # 2026-09-23 超时保护：见 src/net_guard.py
        from src.net_guard import import_ak
        ak = import_ak()
        df = ak.fund_open_fund_info_em(code)
        if df.empty or len(df) < days:
            return None
        # 取最近 days 行
        recent = df.iloc[-days:]
        navs = [float(v) for v in recent["单位净值"].tolist()]
        # 日增长率已经是百分比值，如 0.95 表示 +0.95%
        changes = [float(v) for v in recent["日增长率"].tolist()]
        return {"prices": navs, "changes": changes, "source": "akshare_fund"}
    except Exception as e:
        logger.warning("[%s] 场外基金历史净值获取失败: %s", code, str(e)[:80])
        return None


def _fetch_cn_historical(code: str, days: int) -> dict | None:
    """A 股 ETF 历史价格。"""
    # 策略 1: yfinance（国内标的也支持 .SS/.SZ 后缀）
    try:
        # 2026-09-23 超时保护：见 src/net_guard.py
        from src.net_guard import import_yf
        yf = import_yf()
        prefix = "sz" if code.startswith(("159", "16")) else "sh"
        ticker = yf.Ticker(f"{code}.{prefix.upper()}" if code.isdigit() and len(code) == 6 else code)
        df = ticker.history(period="1mo")
        if len(df) >= days:
            closes = [float(v) for v in df["Close"].tolist()]
            prevs = [closes[0]] + closes[:-1]
            changes = [round((c - p) / p * 100, 2) if p != 0 else 0.0 for c, p in zip(closes, prevs)]
            return {"prices": closes, "changes": changes, "source": "yfinance"}
    except Exception:
        logger.debug("[%s] yfinance CN 历史失败", code)

    # 策略 2: akshare 东方财富源
    try:
        # 2026-09-23 超时保护：见 src/net_guard.py
        from src.net_guard import import_ak
        ak = import_ak()
        df = ak.fund_etf_hist_em(symbol=code, period="daily", adjust="")
        if len(df) < days:
            return None
        closes = [float(v) for v in df["收盘"].tolist()[-days:]]
        changes = [float(v) for v in df["涨跌幅"].tolist()[-days:]]
        return {"prices": closes, "changes": changes, "source": "akshare_em"}
    except Exception:
        logger.debug("[%s] akshare_em CN 历史失败", code)

    # 策略 3: akshare 新浪源
    try:
        # 2026-09-23 超时保护：见 src/net_guard.py
        from src.net_guard import import_ak
        ak = import_ak()
        prefix = "sz" if code.startswith(("159", "16")) else "sh"
        df = ak.fund_etf_hist_sina(symbol=f"{prefix}{code}")
        if len(df) < days:
            return None
        closes = [float(v) for v in df["close"].tolist()[-days:]]
        prevs = [closes[0]] + closes[:-1]
        changes = [round((c - p) / p * 100, 2) if p != 0 else 0.0 for c, p in zip(closes, prevs)]
        return {"prices": closes, "changes": changes, "source": "akshare_sina"}
    except Exception:
        logger.debug("[%s] akshare_sina CN 历史失败", code)

    logger.warning("[%s] 所有 CN 数据源均失败", code)
    return None


def _fetch_us_historical(code: str, days: int) -> dict | None:
    """美股历史价格。"""
    # 策略 1: yfinance
    try:
        # 2026-09-23 超时保护：见 src/net_guard.py
        from src.net_guard import import_yf
        yf = import_yf()
        df = yf.Ticker(code).history(period="1mo")
        if len(df) >= days:
            closes = [float(v) for v in df["Close"].tolist()]
            prevs = [closes[0]] + closes[:-1]
            changes = [round((c - p) / p * 100, 2) if p != 0 else 0.0 for c, p in zip(closes, prevs)]
            return {"prices": closes, "changes": changes, "source": "yfinance"}
    except Exception:
        logger.debug("[%s] yfinance US 历史失败", code)

    # 策略 2: akshare
    try:
        # 2026-09-23 超时保护：见 src/net_guard.py
        from src.net_guard import import_ak
        ak = import_ak()
        df = ak.stock_us_hist(symbol=code, period="daily", adjust="")
        if len(df) < days:
            return None
        closes = [float(v) for v in df["收盘"].tolist()[-days:]]
        prevs = [closes[0]] + closes[:-1]
        changes = [round((c - p) / p * 100, 2) if p != 0 else 0.0 for c, p in zip(closes, prevs)]
        return {"prices": closes, "changes": changes, "source": "akshare_em"}
    except Exception:
        logger.debug("[%s] akshare_em US 历史失败", code)

    logger.warning("[%s] 所有 US 数据源均失败", code)
    return None


def _fetch_hk_historical(code: str, days: int) -> dict | None:
    """港股历史价格。"""
    # 清理代理（国内数据源需要直连）
    import os as _os
    for _k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','all_proxy','ALL_PROXY'):
        _os.environ.pop(_k, None)

    # 策略 1: akshare 新浪源 stock_hk_daily（已验证支持 03121/03486 等港股 ETF）
    try:
        # 2026-09-23 超时保护：见 src/net_guard.py
        from src.net_guard import import_ak
        ak = import_ak()
        df = ak.stock_hk_daily(symbol=code, adjust="")
        if len(df) >= 5:
            take = min(len(df), days)
            closes = [float(v) for v in df["close"].tolist()[-take:]]
            prevs = [closes[0]] + closes[:-1]
            changes = [round((c - p) / p * 100, 2) if p != 0 else 0.0 for c, p in zip(closes, prevs)]
            return {"prices": closes, "changes": changes, "source": "akshare_sina"}
    except Exception:
        logger.debug("[%s] akshare_sina HK 历史失败", code)

    # 策略 2: akshare 东方财富源（含涨跌幅，更准但可能被代理拦截）
    try:
        # 2026-09-23 超时保护：见 src/net_guard.py
        from src.net_guard import import_ak
        ak = import_ak()
        df = ak.stock_hk_hist(symbol=code, period="daily", start_date="20200101",
                              end_date="20991231", adjust="")
        if len(df) >= 5:
            take = min(len(df), days)
            closes = [float(v) for v in df["收盘"].tolist()[-take:]]
            prevs = [closes[0]] + closes[:-1]
            changes = [round((c - p) / p * 100, 2) if p != 0 else 0.0 for c, p in zip(closes, prevs)]
            return {"prices": closes, "changes": changes, "source": "akshare_em"}
    except Exception:
        logger.debug("[%s] akshare_em HK 历史失败", code)

    # 策略 3: yfinance 兜底
    try:
        # 2026-09-23 超时保护：见 src/net_guard.py
        from src.net_guard import import_yf
        yf = import_yf()
        df = yf.Ticker(f"{int(code)}.HK").history(period="1mo")
        if len(df) >= 5:
            take = min(len(df), days)
            closes = [float(v) for v in df["Close"].tolist()[-take:]]
            prevs = [closes[0]] + closes[:-1]
            changes = [round((c - p) / p * 100, 2) if p != 0 else 0.0 for c, p in zip(closes, prevs)]
            return {"prices": closes, "changes": changes, "source": "yfinance"}
    except Exception:
        logger.debug("[%s] yfinance HK 历史失败", code)

    logger.warning("[%s] 所有 HK 数据源均失败", code)
    return None


# ═══════════════════════════════════════════════════════════════
# 趋势检测
# ═══════════════════════════════════════════════════════════════

def _detect_trend(prices_5d: list[float]) -> str:
    """用最近 5 个交易日收盘价判断趋势方向。

    Args:
        prices_5d: 最近 5 日收盘价（按时间升序，prices_5d[-1] = 最新）

    Returns:
        "右侧企稳" / "左侧下跌" / "横盘震荡" / ""
    """
    if len(prices_5d) < 5:
        return ""

    last_3 = prices_5d[-3:]
    if all(last_3[i] < last_3[i + 1] for i in range(2)):
        return "右侧企稳"

    if prices_5d[-1] < prices_5d[0]:
        return "左侧下跌"

    return "横盘震荡"


# ═══════════════════════════════════════════════════════════════
# 信号判定
# ═══════════════════════════════════════════════════════════════

def _calc_buy_signal(
    change_10d: float | None,
    change_20d: float | None,
    trend: str,
) -> str:
    """抄底信号：双窗口 + 双档位。

    🟡 关注：10日跌幅 ≤ -5% AND 趋势="右侧企稳"
    🔵 底部反转：20日跌幅 ≤ -8% AND 趋势="右侧企稳"
    两档同时命中 → 🔵 底部反转优先
    """
    if trend != "右侧企稳":
        return ""

    if change_10d is None or change_20d is None:
        return ""

    # 从强到弱判定：🔵 优先
    if change_20d <= THRESHOLD_BUY_LONG:
        return "🔵 底部反转"
    if change_10d <= THRESHOLD_BUY_SHORT:
        return "🟡 关注"

    return ""


def _calc_chase_signal(
    daily_changes_5d: list[float],
    close: float,
    ma20: float | None,
) -> str:
    """追涨信号：连续阳线 AND 未溢价。

    🟢 趋势加速：近5日每日涨 AND 现价 ≤ 20日线 × 1.03
    """
    if len(daily_changes_5d) < 5:
        return ""
    if ma20 is None:
        return ""
    if not all(c > 0 for c in daily_changes_5d):
        return ""
    if close > ma20 * MA20_BREAK_RATIO:
        return ""

    return "🟢 趋势加速"


# ═══════════════════════════════════════════════════════════════
# 核心扫描循环
# ═══════════════════════════════════════════════════════════════

def scan_radar(client: "FeishuClient | None" = None, dry_run: bool = False) -> dict:
    """扫描雷达观测表所有标的，计算信号并写回。

    Args:
        client: 飞书客户端。None 时自动创建。
        dry_run: True 时只算不写飞书。

    Returns:
        {"scanned": 5, "has_signal": 2, "failed": 1,
         "updates": [...], "details": [...], "signal_items": [...]}
    """
    import time as _time

    # 🔥 2026-09-05 P0 改造：本地开发不调飞书 API，dry_run 或本地模式返回空 dict
    if client is None:
        from src.feishu_client import get_feishu_client_or_none
        client = get_feishu_client_or_none()
        if client is None:
            return {
                "scanned": 0, "has_signal": 0, "failed": 0,
                "updates": [], "details": [], "signal_items": [],
            }

    # ── 构建扫描清单：雷达观测表 + 底仓表持仓 ──
    scan_queue = []  # [(code, name, record_id, source_table, linked, entry_date), ...]

    radar_records = client.list_records("雷达观测表")
    for rec in radar_records:
        code = rec.get("标的代码", "")
        name = rec.get("标的名称", "未知")
        rid = rec.get("_record_id", "")
        if code and rid:
            scan_queue.append((code, name, rid, "雷达观测表", rec.get("关联底仓", ""), rec.get("入库日期", "")))

    # 底仓表持仓：只算信号不写回
    # ⚠️ holdings 必须在 try **之外**初始化：读取失败时下面算 #32 闸门还要用
    holdings: list[dict] = []
    try:
        holdings = client.list_records("底仓表")
        for h in holdings:
            hcode = h.get("标的代码", "")
            hname = h.get("标的名称", "未知")
            hid = h.get("_record_id", "")
            if hcode and hid:
                scan_queue.append((hcode, hname, hid, "底仓表", "", ""))
    except Exception:
        logger.warning("底仓表读取失败，雷达扫描仅含雷达观测表")

    # ── #32 超配闸门：已明显超配的大类不发买入类信号 ──
    # ⚠️ 只对「在底仓表里、且标了大类」的标的生效。雷达观测表里的纯观察对象
    #    不在持仓中、没有权重可算 → 一律放行（宁可漏拦，不可误杀）。
    asset_class_of: dict[str, str] = {
        _field_text(h.get("标的代码")): _field_text(h.get("资产大类"))
        for h in holdings
        if _field_text(h.get("标的代码")) and _field_text(h.get("资产大类"))
    }
    overweight = _calc_overweight_classes(holdings)
    blocked_classes = {c for c, d in overweight.items() if d >= OVERWEIGHT_BLOCK_PP}
    if blocked_classes:
        logger.info("⛔ 超配闸门生效（≥+%.1fpp）：%s", OVERWEIGHT_BLOCK_PP,
                    "、".join(f"{c} +{overweight[c]:.1f}pp" for c in sorted(blocked_classes)))

    logger.info("雷达扫描开始，共 %d 只标的（雷达%d + 底仓%d）",
                len(scan_queue), len(radar_records), len(scan_queue) - len(radar_records))

    if not scan_queue:
        logger.info("无标的可扫描")
        return {"scanned": 0, "has_signal": 0, "failed": 0,
                "updates": [], "details": [], "signal_items": []}

    records = radar_records  # for write-back reference

    updates = []
    details = []
    signal_items = []
    scanned = 0
    failed = 0

    for code, name, record_id, source_table, linked, entry_date in scan_queue:

        if not record_id or not code:
            logger.warning("[%s] 缺少 _record_id 或标的代码，跳过", name)
            continue

        # 1. 抓取历史价格
        logger.info("  扫描 %s (%s)...", name, code)
        hist = _fetch_historical_prices(code, days=25)
        if hist is None:
            logger.warning("    ❌ %s 历史价格抓取失败", name)
            failed += 1
            details.append({"name": name, "code": code, "status": "failed",
                            "buy_signal": "", "chase_signal": "", "linked": linked})
            continue

        scanned += 1
        prices = hist["prices"]
        changes = hist["changes"]
        close = prices[-1]

        # 2. 计算指标
        # 10 日涨跌幅
        change_10d = None
        if len(prices) >= 11:
            change_10d = round((prices[-1] - prices[-11]) / prices[-11] * 100, 2)

        # 20 日涨跌幅
        change_20d = None
        if len(prices) >= 21:
            change_20d = round((prices[-1] - prices[-21]) / prices[-21] * 100, 2)

        # 趋势（5 日）
        trend = _detect_trend(prices[-5:]) if len(prices) >= 5 else ""

        # 20 日均线
        ma20 = None
        if len(prices) >= 20:
            ma20 = round(sum(prices[-20:]) / 20, 2)

        # 5 日每日涨跌幅
        daily_5d = changes[-5:] if len(changes) >= 5 else []

        # 3. 信号判定
        buy_signal = _calc_buy_signal(change_10d, change_20d, trend)
        chase_signal = _calc_chase_signal(daily_5d, close, ma20)

        # ── #32 超配闸门 ──
        # 已明显超配的大类不再收买入类信号：对它们发「趋势加速 / 底部反转」，
        # 与「自然稀释」铁律直接矛盾（用户已经买多了，系统却劝他再买）。
        # 取「抑制」而非「改写」：宁可静默，也不给反向建议。
        _cls = asset_class_of.get(str(code).strip(), "")
        if _cls and _cls in blocked_classes:
            if buy_signal or chase_signal:
                logger.info("    ⛔ [超配闸门] %s 属「%s」(+%.1fpp)，抑制信号 %s%s",
                            name, _cls, overweight[_cls], buy_signal, chase_signal)
            buy_signal, chase_signal = "", ""

        has_signal = bool(buy_signal or chase_signal)

        # 4. 入库日期（首次扫描时自动填入）
        if not entry_date:
            from datetime import datetime, timezone, timedelta
            tz_cn = timezone(timedelta(hours=8))
            entry_date = datetime.now(tz_cn).strftime("%Y-%m-%d")

        # 5. 日志
        sig_text = f"  {buy_signal}" if buy_signal else ""
        sig_text += f"  {chase_signal}" if chase_signal else ""
        if sig_text:
            sig_text = f"🔔{sig_text}"
        else:
            sig_text = "➖ 无信号"
        logger.info("    %s 现价=%.2f  10日=%s%%  20日=%s%%  趋势=%s",
                     sig_text, close,
                     f"{change_10d:+.2f}" if change_10d is not None else "N/A",
                     f"{change_20d:+.2f}" if change_20d is not None else "N/A",
                     trend or "N/A")

        # 6. 收集回写（仅雷达观测表，底仓表不写雷达字段）
        if source_table == "雷达观测表":
            updates.append({
                "_record_id": record_id,
                "现价": close,
                "10日涨跌幅%": change_10d if change_10d is not None else 0,
                "20日涨跌幅%": change_20d if change_20d is not None else 0,
                "趋势": trend,
                "抄底信号": buy_signal,
                "追涨信号": chase_signal,
                "入库日期": entry_date,
            })

        detail = {
            "name": name, "code": code,
            "close": close,
            "change_10d": change_10d, "change_20d": change_20d,
            "trend": trend,
            "buy_signal": buy_signal, "chase_signal": chase_signal,
            "linked": linked, "status": "ok",
        }
        details.append(detail)
        if has_signal:
            signal_items.append(detail)

        _time.sleep(0.3)

    # 7. 写回飞书
    if dry_run:
        logger.info("[DRY RUN] 将更新 %d 条记录，未实际写入", len(updates))
    elif updates:
        logger.info("写回 %d 条记录到雷达观测表...", len(updates))
        count = client.batch_update_records("雷达观测表", updates)
        logger.info("成功更新 %d 条", count)

    return {
        "scanned": scanned,
        "has_signal": len(signal_items),
        "failed": failed,
        "updates": updates,
        "details": details,
        "signal_items": signal_items,
    }


# ═══════════════════════════════════════════════════════════════
# 简报产出
# ═══════════════════════════════════════════════════════════════

def build_radar_brief(signal_items: list[dict]) -> str:
    """根据有信号的标的生产简报嵌入文本。

    Returns:
        雷达扫描区块的纯文本，直接嵌入 briefing。
        无信号时返回空字符串。
    """
    if not signal_items:
        return ""

    lines = [f"\U0001f52d **雷达扫描（{len(signal_items)} 有信号）**"]
    for s in signal_items:
        name = s["name"]
        code = s["code"]
        close = s["close"]
        linked = f" | 关联: {s['linked']}" if s.get("linked") else ""

        sig_tags = []
        if s["buy_signal"]:
            sig_tags.append(s["buy_signal"])
        if s["chase_signal"]:
            sig_tags.append(s["chase_signal"])
        tag_line = " ".join(sig_tags)

        detail = ""
        if s["buy_signal"] == "\U0001f7e1 关注" and s.get("change_10d") is not None:
            detail = f"（10日 {s['change_10d']:+.1f}%）"
        elif s["buy_signal"] == "\U0001f535 底部反转" and s.get("change_20d") is not None:
            detail = f"（20日 {s['change_20d']:+.1f}%）"

        lines.append(f"\n· {name} ({code})")
        lines.append(f"  {tag_line} {detail}| 现 ${close:.2f}{linked}")

    return "\n".join(lines)


def _radar_insight(signal_items: list[dict], news_titles: str,
                   macro_context: str = "") -> str:
    """LLM 轻度确认：对每个有信号标的输出一句判断。

    Args:
        signal_items: 有信号的标的信息列表
        news_titles: 当天要闻标题（空格分隔）
        macro_context: 宏观日历上下文

    Returns:
        LLM 输出文本，每行一个标的。失败返回空字符串。
    """
    if not signal_items:
        return ""

    # 构建标的信息（含实际计算数据，让 LLM 引用真实数字）
    item_lines = []
    for s in signal_items:
        sig = s["buy_signal"] or s["chase_signal"]
        linked = f"关联底仓 {s['linked']}" if s.get("linked") else "纯观察"
        c10 = f"{s.get('change_10d', 0):+.1f}%"
        c20 = f"{s.get('change_20d', 0):+.1f}%"
        trend = s.get("trend", "")
        item_lines.append(
            f"- {s['name']}({s['code']}) | 信号:{sig} | "
            f"现价{s['close']:.2f} | 近10日{c10} | 近20日{c20} | 趋势:{trend} | {linked}"
        )
    items_text = "\n".join(item_lines)

    macro_block = ""
    if macro_context:
        macro_block = f"\n<macro_calendar>\n{macro_context}\n</macro_calendar>\n"

    news_text = news_titles[:800]
    if macro_context:
        news_text = f"宏观日历:\n{macro_context[:400]}\n\n新闻:\n{news_text}"

    extra_rules = (
        "- 每个标的写 1-2 句，简洁有力，格式：\n"
        "  \U0001f916 名称:【用大白话说明信号含义，引用近10日/20日真实数据】\n"
        "  → 可做的:【基于信号类型给1个具体建议】⚠️ 风险:【1句话点出反向风险】\n\n"
        "- 信号参考：\n"
        "  \U0001f7e2 趋势加速 = 近5日连续上涨 + 现价未大幅超过20日均线\n"
        "  \U0001f7e1 关注 = 近10日跌超5% + 趋势开始企稳\n"
        "  \U0001f535 底部反转 = 近20日跌超8% + 趋势右侧企稳\n"
        "  （这些信号不等于买入指令—它们是技术面提示，帮你缩小关注范围）\n\n"
        "- 如果标的超过 3 个，每个标的只写 1 句话\n"
        "- 用大白话写，禁止术语堆砌\n"
        "- 每个标的之间用空行分隔\n"
        "- 直接输出，不要前缀和总结"
    )

    from src.prompt_templates import build_analysis_prompt
    prompt = build_analysis_prompt(
        role="你是量化投资顾问。下面列出了雷达扫描中触发信号的投资标的。每个标的附带系统计算的真实数据（近10日涨跌、近20日涨跌、趋势方向）。你的任务是对每个标的给出可操作的解读。",
        holdings_text=items_text,
        market_text="",
        news_text=news_text,
        extra_rules=extra_rules,
    )

    try:
        from src.llm import get_llm_client, get_llm_model
        client = get_llm_client()
        if client is None:
            return ""
        resp = client.chat.completions.create(
            model=get_llm_model(), max_tokens=1000, temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("雷达 AI 确认生成失败: %s", str(e)[:100])
        return ""


# ═══════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════

def main():
    from dotenv import load_dotenv
    load_dotenv()

    import argparse
    parser = argparse.ArgumentParser(description="雷达观测表扫描器")
    parser.add_argument("--dry-run", action="store_true", help="只算不写")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    print()
    print("=" * 56)
    print("   📡 雷达观测表扫描器")
    if args.dry_run:
        print("   [DRY RUN 模式 —— 只读不写]")
    print("=" * 56)
    print()

    result = scan_radar(dry_run=args.dry_run)

    print()
    print("── 扫描结果 ──")
    for d in result["details"]:
        if d["status"] == "failed":
            print(f"  ❌ {d['name']} ({d['code']})  抓取失败")
            continue
        sig = ""
        if d["buy_signal"]:
            sig += f"  {d['buy_signal']}"
        if d["chase_signal"]:
            sig += f"  {d['chase_signal']}"
        if not sig:
            sig = "  ➖ 无信号"
        linked = f"  关联: {d['linked']}" if d.get("linked") else ""
        print(f"  {d['name']} ({d['code']})  现价 {d['close']}{linked}{sig}")
    print()
    print(f"  扫描: {result['scanned']} | 有信号: {result['has_signal']} | 失败: {result['failed']}")
    print("=" * 56)


if __name__ == "__main__":
    main()
