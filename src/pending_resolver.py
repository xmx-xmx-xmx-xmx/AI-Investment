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
# -1. 自动推断工具
# ═══════════════════════════════════════════════════════════════

def _infer_asset_class(code: str) -> str:
    """根据代码格式推断资产大类，复用 radar 逻辑。"""
    from src.radar import _get_asset_class as _radar_ac
    result = _radar_ac(code)
    if result in ("未知", ""):
        return "基金"
    if result == "基金":
        return "基金"
    return result


def _auto_detect_fund_code(name: str) -> str:
    """通过 akshare 全市场基金表模糊匹配产品名称 → 基金代码。

    首次调用时下载全量基金列表（~27000条），后续缓存到模块级变量。
    只在 pending_resolver 遇到新品且无法从已知标的匹配时才触发。

    Returns:
        基金代码（6位数字），未匹配返回空字符串。
    """
    if not name or len(str(name).strip()) < 4:
        return ""

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


def _fuzzy_match_product(query: str, name_to_rec: dict[str, dict]) -> Optional[dict]:
    """在底仓表记录中用归一化名称匹配产品。

    Returns:
        匹配到的底仓记录 dict（含 _record_id, 标的代码, 持仓份额, 成本均价 等），
        未匹配返回 None。
    """
    q = _normalize_name(query)
    if not q:
        return None

    # 1) 精确归一化匹配
    for name, rec in name_to_rec.items():
        if _normalize_name(name) == q:
            return rec

    # 2) 子串匹配
    for name, rec in name_to_rec.items():
        n = _normalize_name(name)
        if q in n or n in q:
            return rec

    # 3) 容错字典
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
    """归一化买卖方向 → buy / sell。"""
    if action_field is None:
        return "buy"
    if isinstance(action_field, list) and len(action_field) > 0:
        return str(action_field[0]).lower()
    return str(action_field).lower()


# ═══════════════════════════════════════════════════════════════
# 3. 净值抓取
# ═══════════════════════════════════════════════════════════════

def _fetch_nav_on_date(code: str, target_date: date) -> Optional[float]:
    """若 T 日净值尚未发布返回 None（触发 QDII 懒加载跳过）。"""
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
    """卖出：只减份额，成本价不变。"""
    old_shares = float(holding_rec.get("持仓份额", 0) or 0)
    new_shares = max(old_shares - confirm_shares, 0)
    return {"持仓份额": round(new_shares, 4)}


# ═══════════════════════════════════════════════════════════════
# 5. 主流程
# ═══════════════════════════════════════════════════════════════

def resolve_pending(dry_run: bool = False) -> dict:
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

        if not product_name:
            logger.warning("[%s] 产品名称为空，跳过", record_id)
            skipped += 1; continue
        if not trade_time:
            logger.warning("[%s] 交易时间无法解析，跳过", record_id)
            skipped += 1; continue
        try:
            amount = float(rec.get("交易金额", 0))
        except (ValueError, TypeError):
            logger.warning("[%s] 交易金额无效", record_id)
            skipped += 1; continue

        # 匹配底仓
        holding = _fuzzy_match_product(product_name, name_to_rec)
        journal_code = rec.get("标的代码", "") or ""

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

            # 2. 资产大类：根据代码格式自动推断
            if not journal_code:
                asset_cls = "基金"  # 默认，等 LLM 搜到码后再修正
            else:
                asset_cls = _infer_asset_class(journal_code)

            logger.info("[%s] 新品「%s」(code=%s, cls=%s)→ 自动创建底仓记录", record_id, product_name, journal_code or "?", asset_cls)

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

            # 创建底仓记录
            new_id = client.create_record("底仓表", {
                "标的名称": product_name,
                "标的代码": journal_code,
                "资产大类": asset_cls,
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

        logger.info("[%s] %s | ¥%.2f | %s | T日=%s | 今天=%s",
                     code[:8], product_name[:20], amount,
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

        confirm_shares = round(amount / nav, 2)

        # ── 买卖分支 ──
        if action == "sell":
            holding_update = _apply_sell(holding, confirm_shares)
            cost_line = ""

            # 全卖光 → 删除底仓记录
            sold_out = holding_update.get("持仓份额", 1) == 0
        else:
            holding_update = _apply_buy(holding, amount, nav, confirm_shares)
            cost_line = f" 新成本价={holding_update.get('成本均价','?')}"
            sold_out = False

        if dry_run:
            action_note = "将删除底仓记录" if sold_out else ""
            logger.info("  [DRY] NAV=%s 份额=%s%s %s", nav, confirm_shares, cost_line, action_note)
            details.append({"product": product_name, "code": code, "amount": amount,
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
            if ok1 and ok2:
                logger.info("  ✅ NAV=%s 份额=%s%s", nav, confirm_shares, cost_line)
                details.append({"product": product_name, "code": code, "amount": amount,
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
    parser = argparse.ArgumentParser(description="Pending 交易自动确认器")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")

    print(f"\n{'='*55}\n   📋 Pending 交易自动确认器{' [DRY RUN]' if args.dry_run else ''}\n{'='*55}\n")
    result = resolve_pending(dry_run=args.dry_run)

    print("\n── 处理明细 ──")
    for d in result["details"]:
        if d["status"] in ("resolved", "dry_run"):
            print(f"  {'🔍' if d['status']=='dry_run' else '✅'} {d['product'][:25]:<27}"
                  f" ¥{d['amount']:>8.2f} → NAV={d['nav']} 份额={d['shares']}",
                  f"[未写入]" if d['status'] == 'dry_run' else "")
        elif d["status"] == "skipped":
            print(f"  ⏭️  {d['product'][:25]:<27} — {d.get('reason','未知')}")
        else:
            print(f"  ❌ {d['product'][:25]:<27} — {d.get('reason','未知')}")
    print(f"\n  确认: {result['resolved']} | 跳过: {result['skipped']} | 错误: {result['errors']}\n{'='*55}")


if __name__ == "__main__":
    main()
