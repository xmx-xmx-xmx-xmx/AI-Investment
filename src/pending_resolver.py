"""
Pending 交易自动确认器。

职责：
- 读取飞书「交易流水表」中状态为 pending 的记录
- 根据 15:00 分水岭 + 中国交易日历，确定正确的 T 日
- 从 akshare 抓取基金净值，计算确认份额
- 写回飞书，状态更新为 completed

T 日结算规则：
  交易时间 < T日 15:00 → T 日当天净值
  交易时间 ≥ T日 15:00 或非交易日 → 下一个交易日净值

用法：
  python -m src.pending_resolver
  python -m src.pending_resolver --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone, timedelta, date
from typing import Optional

import pandas as pd

from src.feishu_client import FeishuClient

logger = logging.getLogger(__name__)
tz_cn = timezone(timedelta(hours=8))

# ── 交易日历 ──
try:
    import exchange_calendars as ec
    _XSHG = ec.get_calendar("XSHG")
except Exception:
    _XSHG = None
    logger.warning("exchange_calendars 不可用，将退化为简单工作日判断（不含节假日）")


# ═══════════════════════════════════════════════════════════════
# 1. T 日计算
# ═══════════════════════════════════════════════════════════════

def _is_trading_day(d: date) -> bool:
    """判断是否为 A 股交易日。"""
    if _XSHG is not None:
        return _XSHG.is_session(d)
    # 退化：周一到周五
    return d.weekday() < 5


def _next_trading_day(d: date) -> date:
    """返回 d 之后（含 d）的第一个交易日。"""
    if _XSHG is not None:
        return _XSHG.next_session(d) if not _XSHG.is_session(d) else d
    # 退化：跳过周末
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _get_t_day(trade_time: datetime) -> date:
    """根据交易时间确定净值确认日（T 日）。

    规则：
    - 若交易时间在交易日 15:00 之前 → T = 当天
    - 若交易时间在 15:00 之后或非交易日 → T = 下一个交易日
    """
    trade_date = trade_time.date()
    cutoff = trade_time.replace(hour=15, minute=0, second=0, microsecond=0)

    if _is_trading_day(trade_date) and trade_time < cutoff:
        return trade_date

    return _next_trading_day(trade_date + timedelta(days=1))


# ═══════════════════════════════════════════════════════════════
# 2. 数据提取
# ═══════════════════════════════════════════════════════════════

def _extract_product_name(product_field) -> str:
    """从飞书关联字段中提取纯文本产品名称。

    关联字段的 JSON 结构是：
    [{"text": "摩根标普500指数(QDII)C", "record_ids": [...], ...}]
    """
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
    """解析飞书交易时间字段。

    飞书格式：unix 时间戳（毫秒）。
    """
    if time_field is None:
        return None
    try:
        ts = float(time_field)
        # 飞书 datetime 字段返回的是毫秒级时间戳
        if ts > 1e12:
            ts = ts / 1000
        return datetime.fromtimestamp(ts, tz=tz_cn)
    except (ValueError, OSError, TypeError):
        pass
    # 尝试字符串解析
    try:
        return datetime.strptime(str(time_field), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz_cn)
    except ValueError:
        pass
    return None


# ═══════════════════════════════════════════════════════════════
# 3. 净值抓取
# ═══════════════════════════════════════════════════════════════

def _fetch_nav_on_date(code: str, target_date: date) -> Optional[float]:
    """从 akshare 历史净值中查找指定日期的单位净值。

    Returns:
        净值（float），若指定日期的净值尚未发布则返回 None。
    """
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

    # 净值日期可能是字符串或 datetime
    date_col = df["净值日期"]
    target_str = target_date.strftime("%Y-%m-%d")

    for idx in range(len(df) - 1, -1, -1):  # 从最新往前找，更快
        row_date = date_col.iloc[idx]
        row_str = str(row_date)[:10]  # "2026-05-20" 或 "2026-05-20 00:00:00"
        if row_str == target_str:
            nav = df["单位净值"].iloc[idx]
            if pd.isna(nav):
                return None
            return round(float(nav), 4)

    return None  # T 日净值尚未发布


# ═══════════════════════════════════════════════════════════════
# 4. 主流程
# ═══════════════════════════════════════════════════════════════

def resolve_pending(dry_run: bool = False) -> dict:
    """扫描交易流水表中的 pending 记录，尝试确认。

    Returns:
        {"resolved": N, "skipped": N, "errors": N, "details": [...]}
    """
    client = FeishuClient()

    # ── 1. 读取 pending 记录 ──
    logger.info("正在读取交易流水表…")
    all_records = client.list_records("交易流水表")
    pending = [r for r in all_records if r.get("状态") == "pending"]

    if not pending:
        logger.info("无 pending 记录")
        return {"resolved": 0, "skipped": 0, "errors": 0, "details": []}

    logger.info("共 %d 条 pending 记录", len(pending))

    # ── 2. 构建 产品名称 → 标的代码 的映射 ──
    logger.info("正在读取底仓表构建名称→代码映射…")
    holdings = client.list_records("底仓表")
    name_to_code: dict[str, str] = {}
    for h in holdings:
        name = h.get("标的名称", "")
        code = h.get("标的代码", "")
        if name and code:
            name_to_code[name] = code

    logger.info("映射表：%d 条", len(name_to_code))

    # ── 3. 逐条处理 ──
    resolved, skipped, errors = 0, 0, 0
    details = []

    for rec in pending:
        record_id = rec.get("_record_id", "")
        product_raw = rec.get("产品名称")
        product_name = _extract_product_name(product_raw)
        ts_raw = rec.get("交易时间")
        trade_time = _parse_trade_time(ts_raw)

        if not product_name:
            logger.warning("[%s] 产品名称为空，跳过", record_id)
            skipped += 1
            continue

        if not trade_time:
            logger.warning("[%s] 交易时间无法解析，跳过", record_id)
            skipped += 1
            continue

        # 金额
        amount_raw = rec.get("交易金额", 0)
        try:
            amount = float(amount_raw)
        except (ValueError, TypeError):
            logger.warning("[%s] 交易金额无效: %s", record_id, amount_raw)
            skipped += 1
            continue

        # 匹配标的代码
        code = name_to_code.get(product_name)
        if not code:
            # 模糊匹配——如果 exact match 失败，试试 partial
            for k, v in name_to_code.items():
                if product_name in k or k in product_name:
                    code = v
                    break
        if not code:
            logger.warning("[%s] 未找到「%s」的标的代码，跳过", record_id, product_name)
            skipped += 1
            details.append({
                "product": product_name, "record_id": record_id,
                "status": "skipped", "reason": "标的代码未匹配"
            })
            continue

        # ── T 日计算 ──
        t_day = _get_t_day(trade_time)
        today = date.today()

        logger.info(
            "[%s] %s | ¥%.2f | 交易时间 %s | T日=%s | 今天=%s",
            code[:8], product_name[:20], amount,
            trade_time.strftime("%m-%d %H:%M"),
            t_day, today,
        )

        # ── 净值抓取 ──
        nav = _fetch_nav_on_date(code, t_day)

        if nav is None:
            logger.info("  → %s 净值尚未发布（可能 T+1/T+2），等待下次", t_day)
            details.append({
                "product": product_name, "code": code,
                "amount": amount, "t_day": str(t_day),
                "status": "skipped", "reason": f"{t_day} 净值未发布"
            })
            skipped += 1
            continue

        # ── 计算份额 + 写回飞书 ──
        shares = round(amount / nav, 2)

        if dry_run:
            logger.info("  [DRY] NAV=%s 份额=%s，未写入", nav, shares)
            details.append({
                "product": product_name, "code": code,
                "amount": amount, "t_day": str(t_day),
                "nav": nav, "shares": shares,
                "status": "dry_run",
            })
            resolved += 1
        else:
            ok = client.update_record("交易流水表", record_id, {
                "确认净值": nav,
                "确认份额": shares,
                "状态": "completed",
            })
            if ok:
                logger.info("  ✅ 已完成：净值=%s 份额=%s", nav, shares)
                details.append({
                    "product": product_name, "code": code,
                    "amount": amount, "t_day": str(t_day),
                    "nav": nav, "shares": shares,
                    "status": "resolved",
                })
                resolved += 1
            else:
                logger.error("  ❌ 写回飞书失败")
                details.append({
                    "product": product_name, "code": code,
                    "status": "error", "reason": "飞书写入失败"
                })
                errors += 1

    return {
        "resolved": resolved,
        "skipped": skipped,
        "errors": errors,
        "details": details,
    }


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Pending 交易自动确认器")
    parser.add_argument("--dry-run", action="store_true", help="仅计算不写入")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    print()
    print("=" * 55)
    print("   📋 Pending 交易自动确认器" + (" [DRY RUN]" if args.dry_run else ""))
    print("=" * 55)
    print()

    result = resolve_pending(dry_run=args.dry_run)

    print()
    print("── 处理明细 ──")
    for d in result["details"]:
        if d["status"] == "resolved":
            print(f"  ✅ {d['product'][:25]:<27} ¥{d['amount']:>8.2f} → NAV={d['nav']} 份额={d['shares']}")
        elif d["status"] == "dry_run":
            print(f"  🔍 {d['product'][:25]:<27} ¥{d['amount']:>8.2f} → NAV={d['nav']} 份额={d['shares']} [未写入]")
        elif d["status"] == "skipped":
            print(f"  ⏭️  {d['product'][:25]:<27} — {d.get('reason', '未知')}")
        else:
            print(f"  ❌ {d['product'][:25]:<27} — {d.get('reason', '未知')}")
    print()
    print(f"  确认: {result['resolved']} | 跳过: {result['skipped']} | 错误: {result['errors']}")
    print("=" * 55)


if __name__ == "__main__":
    main()
