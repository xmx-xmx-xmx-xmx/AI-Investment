"""Briefing facade: orchestrates scheduled investment briefing generation.

This module serves as the primary entry point and facade for the briefing system,
re-exporting components from `src.briefing_modules` for full backward compatibility.

Usage:
  python -m src.briefing morning
  python -m src.briefing closing
  python -m src.briefing --dry-run morning
  python -m src.briefing closing --compact
  python -m src.briefing closing --full
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone, timedelta

# ── Module-level state variables ──
_last_closing_signal: str = ""
_STYLE_OVERRIDE: str | None = None

tz_cn = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)

# ── Re-exports from src.briefing_modules.headers ──
from src.briefing_modules.headers import (
    get_briefing_style,
    _is_compact_style,
    _push,
    _extract_quote_pct,
    _extract_card_pnl,
    _build_dynamic_header,
)

# ── Re-exports from src.briefing_modules.receipt ──
from src.briefing_modules.receipt import (
    _SETTLE_RECEIPT_PATH,
    _fmt_settle_item,
    _build_settlement_receipt,
    _inject_receipt_after_title,
)

# ── Re-exports from src.briefing_modules.news ──
from src.briefing_modules.news import (
    _REASONING_HEADER_RE,
    _PREAMBLE_RE,
    _BOLD4_RE,
    _sanitize_llm_output,
    _news_link_block,
    _fmt_news,
    _distill_news_line,
    _build_digest_news_block,
    _needs_chinese_translation,
    _translate_english_titles,
    _truncate_at_sentence_boundary,
)

# ── Re-exports from src.briefing_modules.market_blocks ──
from src.briefing_modules.market_blocks import (
    _build_vix_block,
    _build_us_futures_block,
    _build_asia_pacific_market,
    _build_global_market_snapshot,
    _get_us_dst_reminder,
)

# ── Re-exports from src.briefing_modules.sector_blocks ──
from src.briefing_modules.sector_blocks import (
    _SECTOR_STRONG_PCT,
    _SECTOR_AI_MAX_ITEMS,
    _fetch_sector_safe,
    _build_sector_signal_line,
    build_sector_for_ai,
    _build_sector_parts,
    _SECTOR_AI_RULE,
    _MACRO_AI_RULE,
)

# ── Re-exports from src.briefing_modules.valuation ──
from src.briefing_modules.valuation import (
    _FUND_INDEX_MAP,
    _fund_estimate_cache,
    _ESTIMATE_CACHE_DATE,
    _build_portfolio_summary,
    _build_trade_summary,
    _fetch_fund_nav_change,
    _estimate_fund_realtime_pct,
    _exchange_rate_footnote,
    _short_name,
    _portfolio_value_summary,
    _build_weekly_return,
)

# ── Re-exports from src.briefing_modules.signals ──
from src.briefing_modules.signals import (
    _SIGNAL_PRIORITY,
    _ASSET_SHORT,
    _short_cls,
    _build_hard_signals_block,
)

# ── Re-exports from src.briefing_modules.snapshots ──
from src.briefing_modules.snapshots import (
    _METRIC_THRESHOLDS,
    _make_signature,
    _load_prev_snapshot,
    _save_snapshot,
    _diff_against_last,
    _format_diff_brief,
    _extract_metrics_from_verdict,
    _extract_metrics_from_pf,
)

# ── Re-exports from src.briefing_modules.narrator ──
from src.briefing_modules.narrator import (
    _build_fallback_insight,
    _ai_insight,
)

# ── Re-exports from src.briefing_modules.decision_log ──
from src.briefing_modules.decision_log import (
    record_briefing_decision,
)

# ── Re-exports from src.briefing_modules.slots ──
from src.briefing_modules.slots import (
    _build_morning,
    _build_midday,
    _build_closing,
    _build_evening,
    _build_sat_morning,
    _build_sun_evening,
    BRIEFINGS,
    _CN_GATED,
)

# ── Re-exports for external backward compatibility (monkeypatching & direct imports) ──
from src import market_data
from src.advisor import load_portfolio, calculate_rebalance
from src.holiday_gate import (
    holiday_notice,
    is_cn_market_open,
    is_hk_market_open,
    is_us_market_open,
)
from src.news_fetcher import _clean_html, _filter_by_keywords, fetch_all_news
from src.notify import FeishuPusher


def main():
    global _STYLE_OVERRIDE
    _STYLE_OVERRIDE = None

    from dotenv import load_dotenv
    load_dotenv()

    args = [a for a in sys.argv[1:] if a]
    dry_run = "--dry-run" in args
    mode = ""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--dry-run":
            pass
        elif arg in ("--full", "--compact"):
            _STYLE_OVERRIDE = arg.lstrip("-")
        elif arg.startswith("--style="):
            val = arg.split("=", 1)[1].strip().lower()
            if val in ("compact", "full"):
                _STYLE_OVERRIDE = val
        elif arg == "--style" and i + 1 < len(args):
            val = args[i + 1].strip().lower()
            if val in ("compact", "full"):
                _STYLE_OVERRIDE = val
            i += 1
        elif not arg.startswith("-") and not mode:
            mode = arg
        i += 1

    if not mode or mode not in BRIEFINGS:
        print("用法: python -m src.briefing [morning|midday|closing|evening|sat_morning|sun_evening] [--compact|--full] [--dry-run]")
        sys.exit(1)

    title, builder = BRIEFINGS[mode]
    style = get_briefing_style()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",
    )

    # ── 节假日熔断 ──
    if mode in _CN_GATED and not is_cn_market_open():
        logger.info("A 股今日休市，跳过 %s", title)
        print(f"\n   ⛔ A 股休市，{title} 跳过\n")
        return

    print(f"\n{'='*50}\n   📨 {title} [{style}]")

    # ── 现价强制刷新（所有模式通用） ──
    logger.info("先刷新现价…")
    try:
        from src.price_updater import update_all_prices
        update_all_prices(dry_run=False)
    except Exception as e:
        logger.warning("现价更新失败（不影响后续）: %s", e)

    logger.info("加载持仓 + 抓取新闻…")
    card = builder()

    if card == "SKIP":
        print(f"\n   ⛔ 休市，{title} 跳过\n{'='*50}")
        return

    # ── 结算回执前置（#38 L1）──
    receipt = _build_settlement_receipt()
    if receipt:
        card = _inject_receipt_after_title(card, receipt)
        logger.info("已注入结算回执（%d 行）", receipt.count("\n") + 1)

    # ── #4b 底仓增量对账（只告警不改数）──
    from src.feishu_client import get_feishu_client_or_none
    from src.reconcile import build_reconcile_alert

    recon_alert = build_reconcile_alert(get_feishu_client_or_none())
    if recon_alert:
        card = _inject_receipt_after_title(card, recon_alert)
        logger.warning("已注入对账告警（%d 行）", recon_alert.count("\n") + 1)

    # ── #33 规则引擎（规则 1/3/5，Q4-C 分级：回本给建议、结构性只提醒）──
    from src.rules_engine import build_rules_alert

    rules_alert = build_rules_alert(
        get_feishu_client_or_none(),
        include_theme_gap=((mode == "sat_morning") if _is_compact_style() else True),
    )
    if rules_alert:
        card = _inject_receipt_after_title(card, rules_alert)
        logger.info("已注入决策参考（%d 行）", rules_alert.count("\n") + 1)

    dyn_title, dyn_template = _build_dynamic_header(
        mode,
        title,
        card,
        rules_alert=rules_alert,
        recon_alert=recon_alert,
        receipt=receipt,
    )

    if dry_run:
        logger.info("[DRY RUN] 跳过飞书推送")
        tag = f" [{dyn_template}]" if dyn_template else ""
        print(f"\n═══ {dyn_title}{tag} ═══\n{card}")
    else:
        logger.info("推送到飞书群… (%s [%s])", dyn_title, dyn_template or "default")
        _push(dyn_title, card, template=dyn_template)

    # ── #30 决策日志落盘（全自动记录系统建议、规则触发与资产盘子）──
    try:
        log_entry = record_briefing_decision(
            slot=mode,
            card=card,
            dyn_title=dyn_title,
            dyn_template=dyn_template,
            rules_alert=rules_alert,
            dry_run=dry_run,
        )
        logger.info("决策日志已落盘: %s", log_entry.get("决策结论"))
    except Exception as e:
        logger.warning("决策日志落盘异常 (不影响主流程): %s", e)

    print(f"\n   ✅ 推送完成\n{'='*50}")


if __name__ == "__main__":
    main()
