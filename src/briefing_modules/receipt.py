"""Settlement receipt block (Step 0 settlement result display)."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

tz_cn = timezone(timedelta(hours=8))

# pending_resolver 是 daily-run.yml 的 **Step 0**（每个时段都先跑），
# 这里把 Step 0 落盘的回执读出来，摆到卡片标题正下方 —— 让失败单有机会被看见。
_SETTLE_RECEIPT_PATH = "data/pending_resolve_result.json"


def _fmt_settle_item(it: dict) -> str:
    """把一条结算明细格式化成「产品名 +57.60 份」。

    ⚠️ 产品名**不截断**：份额类别字母（A/C/E）在名称**末尾**，截断会把它切掉，
       而类别恰是判断"记到哪只上"的关键。
    """
    name = str(it.get("product") or "?").strip()
    act = str(it.get("action") or "buy").lower()
    try:
        shares = abs(float(it.get("shares") or 0))
    except (TypeError, ValueError):
        shares = 0.0
    if shares <= 0:
        return f"· {name}"
    if act == "sell":
        return f"· {name} -{shares:.2f} 份"
    if act == "convert":
        return f"· {name} 转换 {shares:.2f} 份"
    return f"· {name} +{shares:.2f} 份"


def _build_settlement_receipt() -> str:
    """读取 Step 0 的结算回执；无结算 / 文件缺失 / 格式错 → 返回空串。

    ⚠️ 必须校验日期：CI 里 data/ 每次 run 都是新建的，但**本地跑会残留旧文件**，
       不校验就会把上一次的结算当成"本时段结算"反复播报。
    """
    from src import briefing
    path_val = getattr(briefing, "_SETTLE_RECEIPT_PATH", _SETTLE_RECEIPT_PATH)
    try:
        raw = Path(path_val).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    if data.get("date") != datetime.now(tz_cn).date().isoformat():
        return ""

    items = [i for i in (data.get("items") or []) if isinstance(i, dict) and i.get("product")]
    try:
        errors = int(data.get("errors") or 0)
    except (TypeError, ValueError):
        errors = 0

    lines: list[str] = []
    if items:
        shown = items[:3]
        lines.append(f"📌 **本时段自动入账 {len(items)} 笔**")
        lines.extend(_fmt_settle_item(i) for i in shown)
        if len(items) > len(shown):
            lines.append(f"· …另有 {len(items) - len(shown)} 笔")
        lines.append("_若其中某笔实际未成交（支付宝提示失败），发我单号即可回滚_")
    if errors:
        lines.append(f"⚠️ 另有 {errors} 笔写回底仓失败，请核对")

    return "\n".join(lines)


def _inject_receipt_after_title(card: str, receipt: str) -> str:
    """把回执插到卡片**标题行正下方**。

    标题 = 第一个换行之前的内容（各时段卡片首行都是
    `☀️ **2026-09-24 早间简报**　|　08:30` 这类，格式统一）。放进正文里会
    跟其他 block 一起被略过 —— 那正是 2026-09-24 事故能藏住的原因。
    """
    if not receipt:
        return card
    head, sep, tail = card.partition("\n")
    return f"{head}\n{receipt}" + (f"\n{tail}" if sep else "")
