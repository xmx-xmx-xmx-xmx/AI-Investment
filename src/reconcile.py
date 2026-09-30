"""#4b 底仓增量对账（只告警、不改数）。2026-09-30。

背景：`pending_resolver` 是一次性结算机（置 completed 后永不回看），全仓原本
没有任何"从流水核对底仓"的逻辑 → **算错一次 = 底仓永久停在错值**（2026-09-17
缓存覆写事故 4 笔静默错 250/277 份，零报错；2026-09-24 失败单虚增 57.6 份）。

#38 L1/L2 解决的是"让你看得见、回滚得准"；本模块补的是"系统自己发现写回错了"。

**能抓什么**（写回链路自洽性）：
  - 同批次后续笔基于旧份额覆写前一笔（9/17 事故的直接类别）
  - 双重结算 / 写回失败但状态已置 completed
  - 底仓被系统外改动（手动改表、支付宝侧有系统外动作）
  - 全卖光但底仓记录没删掉

**抓不到什么**（刻意说明，别期待过高）：
  - "结算了一笔实际失败的单"（9/24 类）——那类错误**内部自洽**（快照 + 确认份额
    恰好等于底仓现值），错在"该不该结算"的判断，不在写回。它靠 L1 回执
    （你核对支付宝）与未来的 L3（快捷指令扫失败短信）。

设计约束：
  - **只告警、不改数**：绕开"底仓有表外历史、全量重算会清零"的死结，
    也不需要用户确认任何基准点 → 因此**不依赖 #33 规则**，可独立投产。
  - **增量**：只核对本时段结算的笔（回执 items），不碰存量。
  - **对账基准 = 流水行的「结算前份额」快照**（#38 L2）：expected = prev ± 确认份额。
    快照缺失或 ≤0 → 跳过（首笔买入 prev=0 合法但与"列没填"不可区分；
    宁可漏核不可误报）。
  - **fail-silent**：读飞书失败 → WARNING + 不告警，绝不拖垮简报
    （与 radar 超配闸门同一策略）。
  - 阈值：份额差 > 0.01 份即告警（份额均为 2 位小数，任何超出都是真异常；
    0.01 吸收二进制浮点噪声）。

对用户的动作约定（告警文案已内置）：
  1. 最近手动改过底仓 / 有系统外操作 → 回一句"我自己动的"，人工对齐基线；
  2. 没动过 → 把告警转发给助手，按 `docs/FAILED_TRADE_SOP.md` 定位回滚。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

tz_cn = timezone(timedelta(hours=8))

RECEIPT_PATH = "data/pending_resolve_result.json"
SHARE_TOLERANCE = 0.01  # 份额差超过此值才告警（2 位小数 + 浮点噪声缓冲）


def _num(v) -> float:
    """飞书数字字段归一：标量/字符串数字 → float，其余 → 0。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def reconcile_items(items: list[dict], client) -> list[dict]:
    """核对回执明细与底仓实际值，返回异常列表（空 = 全部对上）。

    items: 回执文件里的 items（需含 record_id / holding_record_id /
           action / shares / sold_out，由 #4b 锚点提供）。
    client: FeishuClient（None → 返回 []，本地不核）。
    """
    if client is None or not items:
        return []

    candidates = [
        i for i in items
        if i.get("status", "resolved") == "resolved"
        and str(i.get("action") or "buy") in ("buy", "sell")
        and i.get("record_id")
        and i.get("holding_record_id")
    ]
    if not candidates:
        return []

    # 一次拉两张表，建 record_id → 行 的索引
    trades = {r.get("_record_id"): r for r in (client.list_records("交易流水表") or [])}
    holdings = {r.get("_record_id"): r for r in (client.list_records("底仓表") or [])}

    anomalies: list[dict] = []
    for it in candidates:
        product = str(it.get("product") or "?")
        trade_row = trades.get(it["record_id"])

        # 对账基准取流水行的「结算前份额」快照（#38 L2）。
        # ⚠️ 缺失或 ≤0 → 跳过：首笔买入 prev=0 与"快照列没写上"不可区分，
        #    误报的代价（吓用户）高于漏核的代价（该笔不受保护）。
        prev = _num(trade_row.get("结算前份额")) if trade_row else 0.0
        if trade_row is None or prev <= 0:
            logger.info("对账跳过（无快照基准）：%s", product)
            continue

        shares = abs(_num(it.get("shares")))
        action = str(it.get("action"))
        expected = round(prev - shares if action == "sell" else prev + shares, 2)

        holding_row = holdings.get(it["holding_record_id"])
        if it.get("sold_out"):
            # 全卖光：底仓行应已删除；还在且有份额 → 异常
            if holding_row is not None and _num(holding_row.get("持仓份额")) > SHARE_TOLERANCE:
                anomalies.append({
                    "product": product, "kind": "sold_out_not_deleted",
                    "expected": 0.0,
                    "actual": _num(holding_row.get("持仓份额")),
                })
            continue

        actual = _num(holding_row.get("持仓份额")) if holding_row else 0.0
        diff = round(actual - expected, 2)
        if abs(diff) > SHARE_TOLERANCE:
            anomalies.append({
                "product": product, "kind": "shares_mismatch",
                "expected": expected, "actual": actual, "diff": diff,
            })
    return anomalies


def build_reconcile_alert(
    client,
    receipt_path: str = RECEIPT_PATH,
    now_cn: datetime | None = None,
) -> str:
    """读本时段结算回执 → 对账 → 生成告警文本。无异常/无法核对 → 空串。"""
    now_cn = now_cn or datetime.now(tz_cn)
    try:
        data = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    if data.get("date") != now_cn.date().isoformat():
        return ""  # 旧文件（本地残留）→ 不核对

    items = [i for i in (data.get("items") or []) if isinstance(i, dict)]
    if not items:
        return ""

    try:
        anomalies = reconcile_items(items, client)
    except Exception as e:  # noqa: BLE001 —— fail-silent 是刻意设计
        logger.warning("对账执行失败（不告警、不阻断）：%s", e)
        return ""

    if not anomalies:
        return ""

    lines = [f"⚠️ **对账告警：{len(anomalies)} 笔结算与底仓对不上**"]
    for a in anomalies[:5]:
        if a["kind"] == "sold_out_not_deleted":
            lines.append(
                f"· {a['product']}：应已清仓删除，底仓仍有 {a['actual']:.2f} 份"
            )
        else:
            lines.append(
                f"· {a['product']}：结算后应为 {a['expected']:.2f} 份，"
                f"底仓实际 {a['actual']:.2f} 份（差 {a['diff']:+.2f}）"
            )
    if len(anomalies) > 5:
        lines.append(f"· …另有 {len(anomalies) - 5} 笔")
    lines.append(
        "_若你最近手动改过底仓或有系统外操作，回「我自己动的」即可；"
        "否则把这段发我，我来定位回滚_"
    )
    return "\n".join(lines)
