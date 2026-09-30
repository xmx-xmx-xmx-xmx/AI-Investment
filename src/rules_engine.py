"""#33 落地层：决策规则引擎（规则 1/3/5，只提醒/建议、不改持仓数据）。2026-09-30。

规则唯一来源：`docs/ACTION_RULES.md`（Q3 启用 1/2/3/5/6）。本模块先落地**纯本地**
的三条；规则 2（需同类均值数据源）与规则 6（需资讯映射 #36）后续接入同一出口。

- **规则 1 同主题业绩差**：普通持有桶内、同主题多只、收益率差 >20pp → 提醒
  （头号案例：长城/天弘两只新能源车差 24.4pp）
- **规则 3 回本提醒**：曾深亏（≤−10%）回到成本 ±3% → **建议**（走/留决策点）
- **规则 5 单只占比保险丝**：任一只市值占比 >15% → 提醒（当前无触线，纯保险丝）

动作分级（Q4-C）：回本/止盈类给建议（明确决策点），结构性偏离只提醒
（怎么纠偏涉及现金流，不代答）。

状态存储（规则 3 的"曾深亏"需要跨运行记忆）：
  ⚠️ CI 的 data/ 每次 run 重建 → 本地状态文件活不过一次 run。
  状态存**底仓表新增两字段**：「历史最低收益率」（滚动 min）+「回本提醒已发」
  （防重复触发）。首轮会把 29 行的 min 回填为当前收益率（一次性成本），
  之后只有值变化才回写。写失败只 WARNING，绝不拖垮简报（fail-silent，
  与 reconcile / radar 闸门同策略）。

已知取舍（刻意设计，别当 bug）：
  - 「历史最低收益率」从字段创建日才开始累计——此前已回本的历史深亏抓不到；
  - 回本提醒触发一次后置位，只有跌回 −5% 以下或明显越过 +10% 才复位
    （在成本附近反复震荡不会每时段刷屏）；
  - 规则 1 只看「普通持有」桶——长期底仓默认禁言是 Q2-C 的既定语义。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ── 阈值（与 ACTION_RULES.md 对齐，改规则先改文档再改这里）──
DEEP_LOSS = -0.10        # 规则 3：历史最低收益率 ≤ 此值算"曾深亏"
BREAKEVEN_BAND = 0.03    # 规则 3：回到成本 ±3% 视为"回本在即"
RESET_LOW = -0.05        # 跌回此值以下 → 复位"已发"标志（重新武装）
RESET_HIGH = 0.10        # 明显越过成本此值以上 → 同样复位
THEME_GAP = 0.20         # 规则 1：同主题收益率差 >20pp
SINGLE_CAP = 0.15        # 规则 5：单只占比 >15%

FIELD_MIN_RET = "历史最低收益率"
FIELD_FIRED = "回本提醒已发"

# 主动型（无底层指数）的主题归类：按名称关键词，命中即归组。
# ⚠️ 顺序敏感：先具体后宽泛（"上海金"要在"金"这类之前，本表已按此排）。
_NAME_THEME_KEYWORDS: list[tuple[str, str]] = [
    ("新能源", "新能源车"),
    ("纳斯达克", "纳指"),
    ("标普", "标普"),
    ("红利", "红利"),
    ("互联网", "互联网"),
    ("新消费", "新消费"),
    ("新兴市场", "新兴市场"),
    ("上海金", "黄金"),
    ("黄金", "黄金"),
    ("节能环保", "节能环保"),
    ("短债", "短债"),
    ("债券", "债基"),
]


def _field_text(value) -> str:
    """飞书字段归一：单选/多选是数组（["普通持有"]），标量直接转。与 radar 同逻辑。"""
    if isinstance(value, (list, tuple)):
        return str(value[0]).strip() if value else ""
    return str(value).strip() if value is not None else ""


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_ret(rec: dict) -> float | None:
    """收益率（小数形式，0.05 = +5%）。优先「最新收益率」，缺失时用 现价/成本 兜底。"""
    raw = rec.get("最新收益率")
    if raw not in (None, "", []) :
        try:
            return float(raw if not isinstance(raw, (list, tuple)) else raw[0])
        except (TypeError, ValueError):
            pass
    price, cost = _num(rec.get("现价")), _num(rec.get("成本均价"))
    if price > 0 and cost > 0:
        return price / cost - 1.0
    return None


def _pct(v: float) -> str:
    return f"{v * 100:+.1f}%"


def _theme_key(rec: dict) -> str:
    """主题分组键：底层指数（指数基金）→ 名称关键词（主动型）→ 名称自身（不触发）。"""
    idx = _field_text(rec.get("底层指数"))
    if idx and not idx.startswith("无"):
        return idx
    name = _field_text(rec.get("标的名称"))
    for kw, theme in _NAME_THEME_KEYWORDS:
        if kw in name:
            return theme
    return name


def _market_value(rec: dict) -> float:
    """市值（公式字段，飞书已按币种折算）。缺失时用 现价×份额 兜底。"""
    mv = _num(rec.get("市值"))
    if mv > 0:
        return mv
    return _num(rec.get("现价")) * _num(rec.get("持仓份额"))


# ═══════════════════════════════════════════════════════════════
# 三条规则的纯函数实现（不触网、不写表，测试直接喂 dict）
# ═══════════════════════════════════════════════════════════════

def _rule_breakeven(rec: dict) -> tuple[dict | None, dict]:
    """规则 3。返回 (alert, 状态增量 update)；update 只在值真正变化时非空。"""
    name = _field_text(rec.get("标的名称"))
    ret = _parse_ret(rec)
    update: dict = {}
    if ret is None:
        return None, update

    prev_min_raw = rec.get(FIELD_MIN_RET)
    prev_min = None
    if prev_min_raw not in (None, "", []):
        try:
            prev_min = float(prev_min_raw if not isinstance(prev_min_raw, (list, tuple)) else prev_min_raw[0])
        except (TypeError, ValueError):
            prev_min = None
    new_min = ret if prev_min is None else min(prev_min, ret)

    fired = bool(rec.get(FIELD_FIRED))
    new_fired = fired
    trigger = False
    if new_min <= DEEP_LOSS and -BREAKEVEN_BAND <= ret <= BREAKEVEN_BAND:
        if not fired:
            trigger = True
            new_fired = True
    elif ret < RESET_LOW or ret > RESET_HIGH:
        new_fired = False  # 跌回深亏区 / 已明显越过成本 → 重新武装

    if prev_min is None or new_min != prev_min or new_fired != fired:
        update = {FIELD_MIN_RET: round(new_min, 4), FIELD_FIRED: new_fired}

    if trigger:
        alert = {
            "rule": 3, "level": "建议",
            "text": f"👉 **{name}** 回本在即（现 {_pct(ret)}，最低曾 {_pct(new_min)}）——"
                    f"走还是留的决策点：当初亏它是有原因的，那个原因还在吗？",
        }
        return alert, update
    return None, update


def _rule_theme_gap(holdings: list[dict]) -> list[dict]:
    """规则 1：普通持有桶内、同主题、收益率差 >20pp。"""
    bucket = [h for h in holdings if _field_text(h.get("标签")) == "普通持有"]
    groups: dict[str, list[dict]] = {}
    for h in bucket:
        ret = _parse_ret(h)
        if ret is None:
            continue
        groups.setdefault(_theme_key(h), []).append((h, ret))

    alerts = []
    for theme, members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda x: x[1])
        worst, wr = members[0]
        best, br = members[-1]
        gap = br - wr
        if gap > THEME_GAP:
            alerts.append({
                "rule": 1, "level": "提醒",
                "text": f"· 主题「{theme}」持了多只，收益差 {gap * 100:.1f}pp："
                        f"**{best['标的名称']}** {_pct(br)} vs **{worst['标的名称']}** {_pct(wr)}——"
                        f"为什么留着落后的那只？",
            })
    return alerts


def _rule_single_cap(holdings: list[dict]) -> list[dict]:
    """规则 5：单只市值占比 >15% 保险丝。"""
    mvs = [(h, _market_value(h)) for h in holdings]
    total = sum(mv for _, mv in mvs)
    if total <= 0:
        return []
    alerts = []
    for h, mv in mvs:
        pct = mv / total
        if pct > SINGLE_CAP:
            alerts.append({
                "rule": 5, "level": "提醒",
                "text": f"· **{h.get('标的名称', '?')}** 单只占比 {pct:.1%}（>15% 保险丝）",
            })
    return alerts


def evaluate_rules(holdings: list[dict]) -> tuple[list[dict], list[dict]]:
    """跑三条规则。返回 (alerts, updates)。

    alerts: [{rule, level, text}]，建议级在前。
    updates: [{_record_id, 历史最低收益率, 回本提醒已发}]，只含值有变化的行。
    """
    alerts: list[dict] = []
    updates: list[dict] = []
    for rec in holdings:
        if not rec.get("_record_id"):
            continue
        alert, upd = _rule_breakeven(rec)
        if alert:
            alerts.append(alert)
        if upd:
            updates.append({"_record_id": rec["_record_id"], **upd})

    alerts.extend(_rule_theme_gap(holdings))
    alerts.extend(_rule_single_cap(holdings))

    alerts.sort(key=lambda a: 0 if a["level"] == "建议" else 1)
    return alerts, updates


def render_alerts(alerts: list[dict], max_lines: int = 6) -> str:
    """渲染成简报文本块。空列表 → 空串。"""
    if not alerts:
        return ""
    lines = [f"🎯 **决策参考（{len(alerts)} 条，规则详见 docs/ACTION_RULES.md）**"]
    for a in alerts[:max_lines]:
        lines.append(a["text"])
    if len(alerts) > max_lines:
        lines.append(f"· …另有 {len(alerts) - max_lines} 条")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 飞书接入（读底仓 → 评估 → 回写状态 → 出文本）。fail-silent。
# ═══════════════════════════════════════════════════════════════

def build_rules_alert(client) -> str:
    """简报推送前调用。client 为 None（本地）→ 恒空串。"""
    if client is None:
        return ""
    try:
        holdings = client.list_records("底仓表") or []
        alerts, updates = evaluate_rules(holdings)
        if updates:
            # batch_update_records 会 pop _record_id（就地改输入）→ 传副本
            written = client.batch_update_records("底仓表", [dict(u) for u in updates])
            logger.info("规则引擎回写状态 %d/%d 行", written, len(updates))
        return render_alerts(alerts)
    except Exception as e:  # noqa: BLE001 —— 告警绝不拖垮简报
        logger.warning("规则引擎执行失败（不阻断推送）：%s", e)
        return ""
