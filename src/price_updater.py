"""
现价自动更新器 —— B2 核心模块。

职责：
- 从飞书底仓表读出所有持仓
- 根据标的类型自动路由到对应行情源
- 批量写回「现价」列，飞书公式字段「市值」自动重算

当前支持：
- 场外基金：akshare fund_open_fund_info_em → 取最新单位净值
- 场内 ETF：market_data.fetch_cn_etf
- 美股：market_data.fetch_us_etf
- 港股：yfinance (预留)

用法：
    python -m src.price_updater           # 更新全部持仓
    python -m src.price_updater --dry-run # 只抓不写，验证数据
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Optional

import pandas as pd

from src.feishu_client import FeishuClient

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 价格抓取 —— 按品种类型路由
# ═══════════════════════════════════════════════════════════════

def _is_fund_code(code: str) -> bool:
    """判断是否为场外基金代码（6位纯数字，非ETF区间）。"""
    return code.isdigit() and len(code) == 6


def _is_cn_etf_code(code: str) -> bool:
    """判断是否为场内 ETF 代码。

    典型 ETF 前缀：
    51xxxx, 56xxxx, 58xxxx (上交所)
    159xxx (深交所)
    注：16xxxx 是深交所 LOF，归类为场外基金。
    """
    if not code.isdigit() or len(code) != 6:
        return False
    return code.startswith(("51", "56", "58", "159"))


def _is_us_code(code: str) -> bool:
    """判断是否为美股代码（纯字母）。"""
    return code.isalpha() and not code.isdigit()


def detect_fund_trend(df) -> str:
    """从历史净值表中检测基金趋势。

    取最近 5 个交易日净值，判断方向：
    - "右侧企稳"：最近 3 天净值连续上涨，短期回升信号
    - "左侧下跌"：最近 5 天整体仍在下跌通道中
    - "横盘震荡"：无明显方向
    - ""：数据不足无法判断

    Args:
        df: akshare fund_open_fund_info_em 返回的 DataFrame
    """
    if df is None or df.empty or "单位净值" not in df.columns:
        return ""

    navs = df["单位净值"].dropna()
    if len(navs) < 5:
        return ""

    recent = navs.iloc[-5:].tolist()
    recent = [float(v) for v in recent]

    # 最近 3 天连续上涨 → 右侧企稳
    last_3 = recent[-3:]
    if len(last_3) == 3 and all(last_3[i] < last_3[i + 1] for i in range(len(last_3) - 1)):
        return "右侧企稳"

    # 5 天前比今天高 → 仍在下跌
    if recent[-1] < recent[0]:
        return "左侧下跌"

    return "横盘震荡"


def fetch_fund_price(code: str) -> Optional[dict]:
    """通过 akshare 获取场外基金最新净值 + 趋势检测。

    Returns:
        {'price': 1.2345, 'date': '2026-06-08', 'change_pct': +0.12, 'trend': '右侧企稳'}
        None 如果获取失败
    """
    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare 未安装")
        return None

    try:
        df = ak.fund_open_fund_info_em(code)
        if df.empty or "单位净值" not in df.columns:
            logger.warning("[%s] 基金数据为空", code)
            return None

        last = df.iloc[-1]
        nav = last["单位净值"]
        if pd.isna(nav):
            logger.warning("[%s] 最新净值为空", code)
            return None

        change_pct = 0.0
        if "日增长率" in df.columns and not pd.isna(last["日增长率"]):
            change_pct = float(last["日增长率"])

        trend = detect_fund_trend(df)

        return {
            "price": round(float(nav), 4),
            "date": str(last["净值日期"]),
            "change_pct": round(change_pct, 2),
            "trend": trend,
            "source": "akshare_fund",
        }
    except Exception as e:
        logger.warning("[%s] 基金净值获取失败: %s", code, e)
        return None


def fetch_etf_price(code: str) -> Optional[dict]:
    """获取场内 ETF 价格（委托给 market_data）。"""
    from src import market_data
    from datetime import datetime, timezone, timedelta
    data = market_data.fetch_cn_etf(code)
    if data:
        return {
            "price": data["close"],
            "date": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d"),
            "change_pct": data["change_pct"],
            "source": data.get("source", "etf"),
        }
    return None


def fetch_us_price(code: str) -> Optional[dict]:
    """获取美股价格（委托给 market_data）。"""
    from src import market_data
    from datetime import datetime, timezone, timedelta
    data = market_data.fetch_us_etf(code)
    if data:
        return {
            "price": data["close"],
            "date": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d"),
            "change_pct": data["change_pct"],
            "source": data.get("source", "us"),
        }
    return None


def _is_hk_code(code: str) -> bool:
    """判断是否为港股代码（5位数字，如 00700）。"""
    return code.isdigit() and len(code) == 5


def fetch_hk_price(code: str) -> Optional[dict]:
    """获取港股价格（委托给 market_data）。"""
    from src import market_data
    from datetime import datetime, timezone, timedelta
    data = market_data.fetch_hk_stock(code)
    if data:
        return {
            "price": data["close"],
            "date": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d"),
            "change_pct": data["change_pct"],
            "source": data.get("source", "hk"),
        }
    return None


def fetch_price(code: str) -> Optional[dict]:
    """智能路由：根据代码格式自动选择行情源。

    场外基金 → 6位数字（非ETF前缀）
    场内ETF  → 51/56/58/159/16开头
    港股     → 5位数字
    美股     → 纯字母

    Returns:
        {'price': 1.50, 'date': '2026-06-08', 'change_pct': +0.35, 'source': 'akshare_fund'}
    """
    if not code:
        return None

    # 场外基金 (6位数字，如 017093)
    if _is_fund_code(code) and not _is_cn_etf_code(code):
        return fetch_fund_price(code)

    # 场内 ETF (51/56/58/159/16 开头)
    if _is_cn_etf_code(code):
        return fetch_etf_price(code)

    # 港股 (5位数字，如 00700)
    if _is_hk_code(code):
        return fetch_hk_price(code)

    # 美股 (字母代码)
    if _is_us_code(code):
        return fetch_us_price(code)

    # 兜底：尝试基金 API
    if code.isdigit() and len(code) == 6:
        return fetch_fund_price(code)

    logger.warning("[%s] 无法识别标的类型，跳过", code)
    return None


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def update_all_prices(client: Optional[FeishuClient] = None, dry_run: bool = False) -> dict:
    """从飞书读持仓 → 抓现价 → 写回。

    Returns:
        {'updated': 5, 'failed': 0, 'skipped': 0, 'details': [...]}
    """
    if client is None:
        client = FeishuClient()

    # 1. 读取底仓表
    logger.info("正在从飞书底仓表读取持仓...")
    records = client.list_records("底仓表")
    if not records:
        logger.warning("底仓表为空")
        return {"updated": 0, "failed": 0, "skipped": 0, "details": []}

    logger.info("共 %d 只持仓", len(records))

    # 2. 逐只抓取现价
    updates = []
    details = []
    failed = 0
    skipped = 0

    for rec in records:
        name = rec.get("标的名称", "未知")
        code = rec.get("标的代码", "")
        record_id = rec.get("_record_id", "")
        old_price = float(rec.get("现价") or 0)

        if not record_id:
            logger.warning("[%s] 无 _record_id，跳过", name)
            skipped += 1
            continue

        if not code:
            logger.warning("[%s] 无标的代码，跳过", name)
            skipped += 1
            continue

        logger.info("  抓取 %s (%s)...", name, code)
        result = fetch_price(code)

        if result is None or result["price"] == 0:
            # 🔥 2026-07-07 容灾改造：超时/抓取失败 → 复用飞书底仓现有现价作为缓存
            # 确保即使行情源全部超时，持仓展示和策略计算仍能基于上次成功数据继续运行
            if old_price > 0:
                logger.info("    ⚠️ %s 实时价超时，复用飞书缓存 ¥%.4f", name, old_price)
                updates.append({
                    "_record_id": record_id,
                    "现价": old_price,
                    "价格更新日期": rec.get("价格更新日期", ""),
                    "趋势": rec.get("趋势", ""),
                    "日涨跌幅%": 0.0,  # 缓存价无法计算当日变动，填 0
                })
                details.append({
                    "name": name, "code": code,
                    "old_price": old_price, "new_price": old_price,
                    "change_pct": 0.0,
                    "source": "cached",
                    "status": "cached",
                })
            else:
                logger.warning("    ❌ %s 抓取失败且无缓存", name)
                failed += 1
                details.append({"name": name, "code": code, "old_price": old_price, "new_price": None, "status": "failed"})
            continue

        new_price = result["price"]
        logger.info("    %s %s → %s (源: %s, 日变动: %+.2f%%)",
                     "🔺" if new_price > old_price else "🔻" if new_price < old_price else "➖",
                     old_price, new_price,
                     result.get("source", "未知"),
                     result.get("change_pct", 0))

        updates.append({
            "_record_id": record_id,
            "现价": new_price,
            "价格更新日期": result.get("date", ""),
            "趋势": result.get("trend", ""),
            "日涨跌幅%": result.get("change_pct", 0),
        })
        details.append({
            "name": name, "code": code,
            "old_price": old_price, "new_price": new_price,
            "change_pct": result.get("change_pct", 0),
            "date": result.get("date", ""),
            "source": result.get("source", "未知"),
            "status": "updated",
        })

        time.sleep(0.3)  # 礼貌限速

    # 3. 批量写回飞书
    if dry_run:
        logger.info("[DRY RUN] 将更新 %d 条记录，未实际写入", len(updates))
    elif updates:
        logger.info("正在写回 %d 条记录到飞书...", len(updates))
        count = client.batch_update_records("底仓表", updates)
        logger.info("成功更新 %d 条", count)
    else:
        logger.info("无更新")

    return {
        "updated": len(updates),
        "failed": failed,
        "skipped": skipped,
        "details": details,
    }


def main():
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="现价自动更新器")
    parser.add_argument("--dry-run", action="store_true", help="只抓不写")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    print()
    print("=" * 56)
    print("   💹 现价自动更新器")
    if args.dry_run:
        print("   [DRY RUN 模式 —— 只读不写]")
    print("=" * 56)
    print()

    result = update_all_prices(dry_run=args.dry_run)

    print()
    print("── 结果汇总 ──")
    for d in result["details"]:
        if d["status"] == "updated":
            arrow = "🔺" if (d["new_price"] or 0) > (d["old_price"] or 0) else "🔻" if (d["new_price"] or 0) < (d["old_price"] or 0) else "➖"
            print(f"  {arrow} {d['name']} ({d['code']})")
            print(f"      {d['old_price']} → {d['new_price']}  ({d['change_pct']:+.2f}%)  源: {d['source']}")
        elif d["status"] == "cached":
            print(f"  ⚠️ {d['name']} ({d['code']})")
            print(f"      实时价超时，复用缓存 ¥{d['new_price']}  源: {d['source']}")
        else:
            print(f"  ❌ {d['name']} ({d['code']})  抓取失败")
    print()
    cached = sum(1 for d in result["details"] if d.get("status") == "cached")
    print(f"  更新: {result['updated']} | 缓存: {cached} | 失败: {result['failed']} | 跳过: {result['skipped']}")
    print("=" * 56)


if __name__ == "__main__":
    main()
