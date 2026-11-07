"""Header styling, dynamic titles, and notification push helpers."""

from __future__ import annotations

import logging
import os
import re

from src.notify import FeishuPusher

logger = logging.getLogger(__name__)


def get_briefing_style() -> str:
    """获取当前简报详略模式：'compact'（精简版，默认）或 'full'（完整版）。

    优先级：CLI 参数 (--full/--compact) > 环境变量 BRIEFING_STYLE > constants.DEFAULT_BRIEFING_STYLE。
    """
    from src import briefing
    style_override = getattr(briefing, "_STYLE_OVERRIDE", None)
    if style_override in ("compact", "full"):
        return style_override
    env_style = os.environ.get("BRIEFING_STYLE", "").strip().lower()
    if env_style in ("compact", "full"):
        return env_style
    from src.constants import DEFAULT_BRIEFING_STYLE
    return DEFAULT_BRIEFING_STYLE if DEFAULT_BRIEFING_STYLE in ("compact", "full") else "compact"


def _is_compact_style() -> bool:
    return get_briefing_style() != "full"


def _push(title: str, content: str, template: str = "") -> bool:
    pusher = FeishuPusher()
    if not pusher.is_configured():
        logger.warning("Webhook 未配置，只打印")
        tag = f" [{template}]" if template else ""
        print(f"\n═══ {title}{tag} ═══\n{content}")
        return False
    return pusher.send_card(title, content, template=template)


def _extract_quote_pct(card: str, label: str) -> str:
    """从卡片行情行（`· ... 🔺+0.52%`）提取指定指数涨跌幅，格式化为 `+0.5%` 供锁屏标题使用。"""
    if not card:
        return ""
    m = re.search(
        rf"^\s*·[^\n]*?{re.escape(label)}[^｜|\n]*?[🔺🔻➖]\s*([+\-]?\d+(?:\.\d+)?)%",
        card,
        re.MULTILINE,
    )
    if not m:
        return ""
    try:
        return f"{float(m.group(1)):+.1f}%"
    except ValueError:
        return ""


def _extract_card_pnl(card: str) -> str:
    """从卡片持仓速览行提取本期盈亏金额（如 `¥+65`、`¥-152`），无则返回空串。"""
    if not card:
        return ""
    m = re.search(
        r"💵\s*(?:昨日|今日)盈亏[^：:\n]*[：:]\s*[🔺🔻➖]?\s*(¥[+\-]?[\d,]+)",
        card,
    )
    return m.group(1) if m else ""


def _build_dynamic_header(
    mode: str,
    default_title: str,
    card: str,
    *,
    rules_alert: str = "",
    recon_alert: str = "",
    receipt: str = "",
    closing_signal: str | None = None,
) -> tuple[str, str]:
    """生成飞书卡片锁屏摘要标题与红绿灯表头配色 `(title, template)`。

    设计目标（2026-10-31 忙人降噪补丁）：
    - 上班繁忙或弱市没耐心点开卡片时，看一眼手机锁屏推送标题即可获知：
      ① 关键指数涨跌；② 持仓盈亏；③ 14:30 是否有需要动手的触发提醒（绿=躺平 / 橙=点开看）。
    - 数据缺失或离线冒烟时平滑回退到静态 `default_title`。
    """
    from src import briefing
    last_sig = getattr(briefing, "_last_closing_signal", "")
    sig = last_sig if closing_signal is None else closing_signal
    pnl = _extract_card_pnl(card)
    has_recon_issue = bool(recon_alert) or ("⚠️" in (receipt or ""))
    has_rule_issue = bool(rules_alert)

    settle_seg = ""
    if receipt:
        m_settle = re.search(r"本时段自动入账\s*(\d+)\s*笔", receipt)
        if m_settle:
            settle_seg = f"入账{m_settle.group(1)}笔"

    if mode == "closing":
        if has_recon_issue:
            lead, tpl = "🟠 14:30 账本需核对", "orange"
        elif has_rule_issue:
            lead, tpl = "🟠 14:30 有决策提醒", "orange"
        elif sig or ("🔵 底部反转" in (card or "")):
            lead, tpl = "🟠 14:30 出现企稳信号", "orange"
        else:
            lead, tpl = "🟢 14:30 无操作", "green"
        segs = [lead]
        if pnl:
            segs.append(f"盘中估 {pnl}")
        if settle_seg:
            segs.append(settle_seg)
        return "｜".join(segs), tpl

    if mode == "morning":
        nq = _extract_quote_pct(card, "纳斯达克")
        sp = _extract_quote_pct(card, "标普500")
        us_seg = " ".join(x for x in [f"纳指 {nq}" if nq else "", f"标普 {sp}" if sp else ""] if x)
        pnl_seg = f"昨 {pnl}" if pnl else ""
        segs = [x for x in ["☀️ 早报", us_seg, pnl_seg, settle_seg] if x]
        title = "｜".join(segs) if len(segs) > 1 else default_title
        tpl = "orange" if (has_recon_issue or has_rule_issue) else "blue"
        return title, tpl

    if mode == "midday":
        sh = _extract_quote_pct(card, "上证")
        cyb = _extract_quote_pct(card, "创业板")
        hsi = _extract_quote_pct(card, "恒指") or _extract_quote_pct(card, "恒生指数")
        hstech = _extract_quote_pct(card, "恒科") or _extract_quote_pct(card, "恒生科技")
        mkt_items = []
        if sh:
            mkt_items.append(f"沪{sh}")
        if cyb:
            mkt_items.append(f"创{cyb}")
        if hsi:
            mkt_items.append(f"恒{hsi}")
        if hstech:
            mkt_items.append(f"科{hstech}")
        mkt_seg = " ".join(mkt_items)
        pnl_seg = f"估 {pnl}" if pnl else ""
        segs = [x for x in ["🌤️ 午休速览", mkt_seg, pnl_seg, settle_seg] if x]
        title = "｜".join(segs) if len(segs) > 1 else default_title
        tpl = "orange" if (has_recon_issue or has_rule_issue) else "wathet"
        return title, tpl

    if mode == "evening":
        nq_fut = _extract_quote_pct(card, "纳指期货")
        pnl_seg = f"今日确认 {pnl}" if pnl else ""
        fut_seg = f"纳指期 {nq_fut}" if nq_fut else ""
        segs = [x for x in ["🌆 夜盘", pnl_seg, fut_seg, settle_seg] if x]
        title = "｜".join(segs) if len(segs) > 1 else default_title
        tpl = "orange" if (has_recon_issue or has_rule_issue) else "indigo"
        return title, tpl

    tpl = "orange" if (has_recon_issue or has_rule_issue) else "wathet"
    if settle_seg:
        return f"{default_title}｜{settle_seg}", tpl
    return default_title, tpl
