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


# ═══════════════════════════════════════════════════════════════
# 资产大类推断（复用 price_updater 路由逻辑，不 import 以避免循环依赖）
# ═══════════════════════════════════════════════════════════════

def _get_asset_class(code: str) -> str:
    """根据代码格式推断资产大类。

    Returns:
        "A股" / "美股" / "港股" / "基金" / "未知"
    """
    if not code:
        return "未知"

    # 场内ETF: 51/56/58/159/16 开头
    if code.isdigit() and len(code) == 6:
        if code.startswith(("51", "56", "58", "159", "16")):
            return "A股资产"
        return "基金"  # 其他 6 位数字 = 场外基金

    # 港股: 5 位数字
    if code.isdigit() and len(code) == 5:
        return "港股资产"

    # 美股: 纯字母
    if code.isalpha():
        return "美股资产"

    return "未知"


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
    asset_class = _get_asset_class(code)

    if asset_class == "A股资产":
        return _fetch_cn_historical(code, days)
    elif asset_class == "基金":
        return _fetch_fund_historical(code, days)
    elif asset_class == "美股资产":
        return _fetch_us_historical(code, days)
    elif asset_class == "港股资产":
        return _fetch_hk_historical(code, days)
    else:
        logger.warning("[%s] 无法识别资产大类，跳过", code)
        return None


def _fetch_fund_historical(code: str, days: int) -> dict | None:
    """场外基金历史净值（akshare 单源）。"""
    try:
        import akshare as ak
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
        import yfinance as yf
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
        import akshare as ak
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
        import akshare as ak
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
        import yfinance as yf
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
        import akshare as ak
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
    # 策略 1: akshare 东方财富源
    try:
        import akshare as ak
        df = ak.stock_hk_hist_em(symbol=code, period="daily", adjust="")
        if len(df) < days:
            return None
        closes = [float(v) for v in df["收盘"].tolist()[-days:]]
        prevs = [closes[0]] + closes[:-1]
        changes = [round((c - p) / p * 100, 2) if p != 0 else 0.0 for c, p in zip(closes, prevs)]
        return {"prices": closes, "changes": changes, "source": "akshare_em"}
    except Exception:
        logger.debug("[%s] akshare_em HK 历史失败", code)

    # 策略 2: yfinance 兜底
    try:
        import yfinance as yf
        df = yf.Ticker(f"{code}.HK").history(period="1mo")
        if len(df) >= days:
            closes = [float(v) for v in df["Close"].tolist()]
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

    if client is None:
        client = FeishuClient()

    records = client.list_records("雷达观测表")
    if not records:
        logger.info("雷达观测表为空，跳过扫描")
        return {"scanned": 0, "has_signal": 0, "failed": 0,
                "updates": [], "details": [], "signal_items": []}

    logger.info("雷达扫描开始，共 %d 只标的", len(records))

    updates = []
    details = []
    signal_items = []
    scanned = 0
    failed = 0

    for rec in records:
        code = rec.get("标的代码", "")
        name = rec.get("标的名称", "未知")
        record_id = rec.get("_record_id", "")
        linked = rec.get("关联底仓", "")
        entry_date = rec.get("入库日期", "")

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

        # 6. 收集回写
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

    # 构建标的信息
    item_lines = []
    for s in signal_items:
        sig = s["buy_signal"] or s["chase_signal"]
        linked = f"关联底仓 {s['linked']}" if s.get("linked") else "纯观察"
        item_lines.append(
            f"- {s['name']}({s['code']}) | {sig} | 现价 {s['close']:.2f} | {linked}"
        )
    items_text = "\n".join(item_lines)

    macro_block = ""
    if macro_context:
        macro_block = f"\n<macro_calendar>\n{macro_context}\n</macro_calendar>\n"

    prompt = f"""<system_role>
你是一位量化投资顾问。下面列出了雷达扫描中触发信号的标的。
你的任务是对每个信号给出1句简短确认——结合当天新闻判断这信号有没有基本面支撑。
</system_role>

<hard_rules>
- 每个标的只写 1 句，不超过 40 个字
- 如果新闻对该标的偏利好 → 说信号有支撑
- 如果新闻偏利空或宏观不确定 → 提示谨慎观望
- 如果没有直接相关新闻 → 说纯技术信号
- 输出格式：
  \U0001f916 MU: 隔夜存储芯片利好，追涨信号有基本面支撑
  \U0001f916 159509: 纳指修复中但VIX仍在20+，反弹偏弱可观望
- 直接输出，不要前缀，不要多余解释
</hard_rules>
{macro_block}
<radar_signals>
{items_text}
</radar_signals>

<news context="隔夜/盘间要闻">
{news_titles[:800]}
</news>"""

    try:
        from src.llm import get_llm_client, get_llm_model
        client = get_llm_client()
        if client is None:
            return ""
        resp = client.chat.completions.create(
            model=get_llm_model(), max_tokens=200, temperature=0.3,
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
