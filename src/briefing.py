"""
多时段简报系统 —— P1 核心模块。

六个时段，按市场日历智能熔断：
  morning     08:30 美股收盘复盘 + AI 解读
  midday      12:00 亚太午盘收盘快讯（A股/港股/日韩台）
  closing     14:30 A 股收盘前 30 分钟策略防御带
  evening     21:00 夜盘前瞻 + 恒指最终收盘 + AI 解读
  sat_morning 周六 美股周五收盘复盘
  sun_evening 周日 周末宏观总结 + 周一前瞻

全部资讯来自免费源（金十数据 + 华尔街见闻），零成本。

用法：
  python -m src.briefing morning
  python -m src.briefing closing
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.holiday_gate import (
    holiday_notice,
    is_cn_market_open,
    is_hk_market_open,
    is_us_market_open,
)
from src.news_fetcher import fetch_all_news, _filter_by_keywords, _clean_html
from src.advisor import load_portfolio, calculate_rebalance
from src.notify import FeishuPusher
from src import market_data
from src.macro_calendar import (
    fetch_today_calendar,
    format_macro_signal_line,
    filter_portfolio_relevant,
    calendar_context_for_prompt,
)

logger = logging.getLogger(__name__)
tz_cn = timezone(timedelta(hours=8))


# ═══════════════════════════════════════════════════════════════
# 通用工具
# ═══════════════════════════════════════════════════════════════

def _push(title: str, content: str) -> bool:
    pusher = FeishuPusher()
    if not pusher.is_configured():
        logger.warning("Webhook 未配置，只打印")
        print(f"\n═══ {title} ═══\n{content}")
        return False
    return pusher.send_card(title, content)


# ═══════════════════════════════════════════════════════════════
# 结算回执（#38 L1，2026-09-24）
# ═══════════════════════════════════════════════════════════════
# pending_resolver 是 daily-run.yml 的 **Step 0**（每个时段都先跑），
# 但它的输出**被直接丢弃** → 用户完全不知道系统在他背后改了底仓。
# 2026-09-24 事故即由此而来：一笔支付宝已失败的单被按成功结算
# （静默虚增 57.6 份），直到用户收到支付宝短信才发现。
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
    try:
        raw = Path(_SETTLE_RECEIPT_PATH).read_text(encoding="utf-8")
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


# ═══════════════════════════════════════════════════════════════
# LLM 输出卫生（2026-09-20）
# ═══════════════════════════════════════════════════════════════
# 用户反馈两件事：
#   ① 周报正文里出现「好的，这是根据您的投资宪法和市场信息生成的周报。」这类开场白；
#   ② 🧠 段整块是编号推理过程（1.环境基调 2.持仓分析 3.交叉判断 4.结论），
#      实测占卡片 29%–58%（早/午/夜盘），但用户要的是结论不是过程。
#
# 已在 prompt 层（prompt_templates.CHAIN_OF_THOUGHT 的 <output_style>）要求
# 不输出过程；但主模型之外还有 Qwen 降级链，指令遵循无保证 → 这里兜底剥离。
# ⚠️ 只清「结构性噪声」，不改写内容（不摘要、不删正常句子）。
# ⚠️ 尾部符号必须容忍任意顺序：实测模型写的是 `**思考过程：**`
#（闭合星号在冒号**之后**），早期版本按 `**` 再 `[:：]` 的顺序写，漏判。
_REASONING_HEADER_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*(?:🧠|🤔|💭|📝)?\s*"
    r"(?:顾问|AI|助手)?\s*(?:推理过程|思考过程|思维过程|推理逻辑|分析过程)"
    r"[\s:：]*(?:\*\*|__)?[\s:：]*$",
    re.MULTILINE,
)
# 开场白：只匹配「好的，这是根据…生成的周报。」这类**明确的自述模板**，
# 要求同时出现自述短语 + 文种词，避免误伤正文（如"根据偏离度，A股低配"）
_PREAMBLE_RE = re.compile(
    r"^[^\n。！?]{0,120}?"
    r"(?:以下是|这是根据|以下是根据|好的[，,]这是|好的[，,]以下)"
    r"[^\n。！?]{0,120}?"
    r"(?:周报|简报|解读|报告|内容)"
    r"[^\n。！?]{0,60}?[。！?]\s*"
)
# LLM 偶发 ****加粗****（四星号）→ 归一到 **加粗**，否则飞书原样显示星号
_BOLD4_RE = re.compile(r"\*{4,}\s*([^*\n]+?)\s*\*{4,}")


def _sanitize_llm_output(text: str) -> str:
    """LLM 输出卫生：剥推理过程尾巴 + 去开场白 + 修四星号粗体。"""
    if not text:
        return text

    t = text.strip()

    # 1. 剥推理过程：模型若仍写出"🧠 顾问推理过程"这类独立标题，
    #    从该标题起截断（prompt 已要求"结论在前"，推理只可能是漏出的尾巴）
    m = _REASONING_HEADER_RE.search(t)
    if m:
        head = t[: m.start()].rstrip()
        if len(head) >= 60:
            t = head
        else:
            # 标题几乎在开头 → 说明模型是"先推理后结论"。此时截断会把整段清空，
            # 宁可留下噪声也不能丢结论内容，仅告警提醒人工看一眼。
            logger.warning("推理过程标题出现在输出开头（%d字），放弃截断以免丢内容", len(t))

    # 2. 去开场白（仅尝试一次，且仅在开头）
    t = _PREAMBLE_RE.sub("", t, count=1).lstrip()

    # 3. 四星号加粗归一
    t = _BOLD4_RE.sub(r"**\1**", t)

    # 4. 尾部：清掉 LLM 坏列表残留的孤立 "*" 行与过多空行
    t = re.sub(r"\n[ \t]*\*[ \t]*$", "", t.rstrip())
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _fmt_news(news_list: list[dict], max_items: int = 8) -> str:
    """格式化新闻列表（展示层）。短标题保留全文；英文标题自动翻译为中文。

    🔥 2026-09-23（P1 #4 第 4 刀）：接入**展示层策展** `curate_for_display()`，
    两件事：① 剥掉纯报价行（`纳指期货: 31,073.05 🔺+0.14% 14:33:55` 属于行情，
    读者已在「📊 全球市场」看过）；② 合并同一事件的**多源重复**条目（要闻是多源
    抓取，而 fetch_all_news 只做 `title[:60]` 字面精确去重 → 金十/华尔街见闻/
    [译] 各家措辞不同，同一件事会各留一条）。

    实测 9/21–9/23 的 10 张工作日卡片：要闻块 **7198 → 6029 字（省 16%）**，
    单张最多省 189 字；被正确合并的有「美联储古尔斯比同一场讲话 ×4」「微软 Copilot ×3」
    「伊朗议会副议长 ×3」「荣耀方飞 ×2」，同时 `银河证券/摩根大通/韩国出口`
    这类形似但不同的条目**没有误合**。

    ⚠️ 只作用于展示层。喂 LLM 的 `titles_only` 仍取**全量** `filtered`，不在这里去重 ——
    与板块/宏观/思维链三处改造同一条分层原则（展示要短，喂 AI 要全）。
    """
    from src.news_fetcher import curate_for_display
    items = curate_for_display(news_list, max_items=max_items)
    if not items:
        return "（暂无）"

    # ── 批量检测 & 翻译英文标题 ──
    _translate_english_titles(items)

    lines = []
    for a in items:
        title = _clean_html(a.get("title", ""))
        if not title:
            continue
        if len(title) <= 200:
            display = title
        else:
            display = title[:197] + "…"
        source = a.get("source", "")
        lines.append(f"· {display}  _{source}_")
    return "\n".join(lines)


def _needs_chinese_translation(text: str) -> bool:
    """检测标题是否以非中文为主（需翻译为中文）。"""
    cleaned = _clean_html(text)
    if len(cleaned) < 8:
        return False
    # 统计 CJK 字符和 ASCII 字母
    cjk = sum(1 for c in cleaned if '一' <= c <= '鿿')
    ascii_alpha = sum(1 for c in cleaned if c.isascii() and c.isalpha())
    total = len(cleaned)
    # 大量 ASCII 字母 + 极少 CJK → 英文标题
    return ascii_alpha > total * 0.35 and cjk < 4


def _translate_english_titles(items: list[dict]) -> None:
    """检测并批量翻译英文标题（原地修改 items 的 title 字段）。"""
    to_translate: list[int] = []
    texts: list[str] = []
    for i, a in enumerate(items):
        title = _clean_html(a.get("title", ""))
        if _needs_chinese_translation(title):
            to_translate.append(i)
            texts.append(title)

    if not to_translate:
        return

    try:
        # 🔥 2026-07-07 容灾改造：翻译切到 Qwen3-32B（更轻更快），60s 短超时
        from src.llm import get_translation_client, get_translation_model
        client = get_translation_client()
        if client is None:
            return

        joined = "\n".join(f"[{j+1}] {t}" for j, t in enumerate(texts))
        prompt = (
            "将以下英文新闻标题翻译为简洁的中文（20-40字），保留编号格式 [N] 中文：\n"
            + joined
        )
        resp = client.chat.completions.create(
            model=get_translation_model(), max_tokens=300, temperature=0.1,
            messages=[{"role": "user", "content": prompt}],
        )
        translated = resp.choices[0].message.content.strip()
        # 解析 [N] 中文格式
        import re
        for line in translated.split("\n"):
            m = re.match(r'\[(\d+)\]\s*(.+)', line.strip())
            if m:
                idx = int(m.group(1)) - 1
                cn = m.group(2).strip()
                if 0 <= idx < len(to_translate):
                    i = to_translate[idx]
                    items[i]["title"] = f"[译] {cn}（{items[i].get('title', '')[:40]}）"
    except Exception:
        pass  # 翻译失败不影响主流程，保留原标题


def _sent_truncate(text: str, max_chars: int = 150) -> str:
    """在第一句号处或 max_chars 词边界处截断。"""
    if len(text) <= max_chars:
        return text
    # 找第一个句号
    dot = text[:max_chars].rfind("。")
    if dot > max_chars // 2:
        return text[:dot + 1]
    # 回退到词边界
    cut = text[:max_chars].rstrip()
    last_space = cut.rfind(" ")
    if last_space > max_chars // 2:
        return cut[:last_space] + "…"
    return cut[:max_chars - 3] + "…"


def _truncate_at_sentence_boundary(text: str, min_len: int = 20) -> str:
    """如果文本疑似被 max_tokens 硬截断（末尾无句号/问号/感叹号），
    回退到最近一个完整句子收尾，避免半句话。

    与 _sent_truncate 区别：_sent_truncate 是"强制截到 max_chars 字符"，
    本函数是"只在疑似被截断时，在最近句号收尾"，不主动限制长度。
    用于 LLM 调用 finish_reason=="length" 时的兜底处理。
    """
    if not text or len(text) < min_len:
        return text
    # 末尾已是完整句子，无需处理
    if text[-1] in "。！？.!?\n":
        return text
    # 从后往前找最近的句子结束符
    for i in range(len(text) - 1, min_len - 1, -1):
        if text[i] in "。！？.!?\n":
            return text[:i + 1]
    # 找不到句号就保持原样（比硬切好）
    return text


def _build_portfolio_summary() -> str:
    try:
        pf = load_portfolio()
        rb = calculate_rebalance(pf)
    except Exception:
        return "持仓数据暂不可用"
    lines = [f"总市值 ¥{rb['total_value']:,.0f}"]
    for d in rb["deviation_report"]:
        lines.append(
            f"{d['asset_class']}：实占 {d['actual_weight_pct']}（目标 {d['target_weight_pct']}），"
            f"偏离 {d['deviation_pct']}"
        )
    return "\n".join(lines)


def _build_trade_summary() -> str:
    """读取最近 5 天的交易流水，供 AI 判断是否近期已操作。

    🔥 2026-09-05 P0 改造：本地开发不调飞书 API，返回空字符串 (CLAUDE.md L22)。
    """
    # 🔥 2026-09-05 P0：本地模式直接返回空字符串
    from src.feishu_client import get_feishu_client_or_none
    client = get_feishu_client_or_none()
    if client is None:
        return ""

    try:
        records = client.list_records("交易流水表")
        from datetime import datetime, timezone, timedelta
        tz_cn = timezone(timedelta(hours=8))
        now = datetime.now(tz_cn)
        recent = []
        for r in records:
            # 🔥 2026-09-17：只统计「已确认完成」的交易。
            # 旧版不看状态 → pending 行会被拼成 "buy xxx ¥None" 塞进 AI 上下文。
            status = r.get("状态", "")
            if isinstance(status, list):
                status = status[0] if status else ""
            if str(status) != "completed":
                continue

            ts = r.get("交易时间", "")
            try:
                ts = float(ts)
                if ts > 1e12:
                    ts /= 1000
                dt = datetime.fromtimestamp(ts, tz=tz_cn)
            except (ValueError, TypeError):
                continue
            if (now - dt).days <= 5:
                product = r.get("产品名称", "未知")
                action = r.get("买卖方向", "")
                if isinstance(action, list):
                    action = action[0] if action else ""
                target = r.get("转入标的", "")
                if isinstance(target, list):
                    target = target[0] if target else ""
                try:
                    amt = float(r.get("交易金额") or 0)
                except (ValueError, TypeError):
                    amt = 0.0
                day = dt.strftime("%m/%d")
                # 转换单没有金额、且是"两标的"事件，单独排版
                if str(action) == "convert" and str(target).strip():
                    line = f"{day} 转换 {product} → {target}"
                    if amt:
                        line += f" ¥{amt:,.2f}"
                elif amt:
                    line = f"{day} {action} {product} ¥{amt:,.2f}"
                else:
                    line = f"{day} {action} {product}"
                recent.append(line)
        if recent:
            return "近5日交易记录:\n" + "\n".join(recent[-10:])
    except Exception:
        pass
    return ""


def _trading_label() -> str:
    """动态交易日标签：星期一、节后首日 → '上一交易日'，否则 → '今日'。"""
    now = datetime.now(tz_cn)
    return "上一交易日" if now.weekday() == 0 else "今日"


# _build_global_market_snapshot v2 below (line ~490) — v1 deleted
def _build_vix_block() -> str:
    """独立 VIX 恐慌指数区块。"""
    try:
        vix = market_data.fetch_vix()
        if vix and vix.get("vix"):
            return f"😨 **VIX 恐慌指数**: {vix['vix']:.1f}（{vix['level']}）"
    except Exception:
        pass
    return ""


def _build_us_futures_block() -> str:
    """美股期货实时行情区块。14:30 盘前风向 + 21:00 实时期货。"""
    lines = []
    for sym, name in [("NQ", "纳指期货"), ("ES", "标普期货")]:
        try:
            data = market_data.fetch_nq_futures(sym)
            if data and data.get("change_pct") is not None:
                a = "🔺" if data["change_pct"] > 0 else "🔻" if data["change_pct"] < 0 else "➖"
                time_str = f" {data['time']}" if data.get("time") else ""
                lines.append(f"· {name}: {data['price']:,.2f}　{a}{data['change_pct']:+.2f}%{time_str}")
        except Exception:
            pass
    if not lines:
        return ""
    return "\n".join(lines)


# ── 板块轮动（2026-09-20 改造：揉进 AI 汇总，仅强信号留一行展示）──
#
# 改造背景：独立的「🔄 板块轮动」块曾占夜盘 47.5%/50.3%、午间 44.8%/49.6% 的篇幅，
# 且夜盘与午间两个时段内容高度重复；而 AI 综合解读本来就已经拿到这些数字
# （9/18 夜盘解读里就写了"SoXX 相对纳指领涨 +1.7%"）→ 展示层等于同一份数据发第二遍。
#
# 新形态：
#   展示层 —— 只在出现强信号（|温差| ≥ 2pct）时给一行醒目提示，平淡日完全不显示
#   AI 层  —— 全量温差喂给 LLM，并要求用大白话解析"钱在往哪个方向挪 + 对我持仓意味着什么"
#
# 温差定义：行业涨跌幅 − 同期大盘涨跌幅（相对强弱，不是绝对涨跌）。
# 阈值与 market_data.fetch_sector_deltas 的 signal 判定保持一致（±2pct）。

_SECTOR_STRONG_PCT = 2.0          # 强信号阈值，仅用于文档与自检断言
_SECTOR_AI_MAX_ITEMS = 8          # 喂给 LLM 的板块条数上限（控 prompt 长度）


def _fetch_sector_safe(market_filter: str = "all") -> list[dict]:
    """抓取板块温差数据，失败/未配置一律返回 []（简报绝不因此中断）。

    Args:
        market_filter: "all" 全部 / "us" 仅美股 / "hk_cn" 仅港股+A股
                       （下划线分隔的多市场并集，"hk" / "cn" 单独亦可）
    """
    try:
        deltas = market_data.fetch_sector_deltas()
    except Exception as e:
        logger.debug("[板块] 温差抓取异常: %s", str(e)[:80])
        return []

    if not deltas:
        return []

    if market_filter != "all":
        wanted = {m for m in market_filter.split("_") if m}
        deltas = [d for d in deltas if d.get("market") in wanted]

    return deltas


def _build_sector_signal_line(deltas: list[dict]) -> str:
    """展示层：仅当出现强信号时返回一行提示，否则空串（平淡日完全折叠）。"""
    strong = [d for d in deltas if d.get("signal")]
    if not strong:
        return ""
    strong.sort(key=lambda d: -abs(d.get("delta") or 0))

    items = []
    for d in strong[:3]:
        arrow = "🔺" if d["delta"] > 0 else "🔻"
        items.append(f"{d['label']} {d['sector_pct']:+.1f}%（温差{arrow}{d['delta']:+.1f}pct）")
    tail = f"　等 {len(strong)} 项异动" if len(strong) > 3 else ""
    return "🔄 **板块异动**：" + "　|　".join(items) + tail


def build_sector_for_ai(deltas: list[dict], max_items: int = _SECTOR_AI_MAX_ITEMS) -> str:
    """AI 层：全量温差文本（强信号排前），供 LLM 解析资金流向。

    与展示层刻意的分层：展示要短（一眼扫过），喂 LLM 要全（有判断空间）。
    fast_mode 时段（closing）传更小的 max_items 以控制 prompt 长度。
    """
    if not deltas:
        return ""

    ordered = sorted(deltas, key=lambda d: (not d.get("signal"), -abs(d.get("delta") or 0)))
    parts = []
    for d in ordered[:max_items]:
        sig = f" {d['signal']}" if d.get("signal") else ""
        parts.append(
            f"{d['label']}[{d.get('market', '')}] 行业{d['sector_pct']:+.1f}% / "
            f"基准{d['benchmark_pct']:+.1f}% / 温差{d['delta']:+.1f}pct{sig}"
        )
    return "[板块温差] " + "；".join(parts)


def _build_sector_parts(market_filter: str = "all",
                        max_ai_items: int = _SECTOR_AI_MAX_ITEMS) -> tuple[str, str]:
    """一次抓取 → (展示行, AI 文本)。同一时段只请求一次网络，避免重复限速等待。"""
    deltas = _fetch_sector_safe(market_filter)
    return _build_sector_signal_line(deltas), build_sector_for_ai(deltas, max_ai_items)


# 喂给 LLM 的板块解析要求。
# 用户 2026-09-20 的原话："AI 解读里面要有解析，不然也看不懂"——所以这里不只是
# 把数字丢给模型，而是先把指标含义讲清楚，再强制要求翻译成"钱在往哪挪 + 影响谁"。
#
# 与 prompt_templates.py 宪法 §2.5 的分工：宪法那段是**思维链步骤**（问"哪些板块
# 领涨/领跌"、给阈值判断），只在标准模式生效；这里补的是**输出要求**（必须翻译成
# 大白话并落到持仓），且 fast_mode（收盘前，不加载宪法）也照样注入。
_SECTOR_AI_RULE = (
    "【板块温差的读法】温差 = 行业涨跌幅 − 同期大盘涨跌幅，衡量的是"
    "资金在行业之间的**相对流向**，不是绝对涨跌——行业在涨但跑输大盘，同样说明资金在离开它。"
    "你必须用大白话把这件事落到持仓上：① 钱正在往哪个方向挪"
    "（科技进攻 / 防御 / 周期 / 避险）；② 这对你哪一个持仓大类意味着什么。"
    "不要把数字念一遍就完事。"
    "若没有温差 ≥2pct 的强信号，就用一句话说「板块间无明显轮动，维持均衡」，不要硬编故事。"
)


# 喂给 LLM 的宏观日历解析要求（2026-09-20）。
# 背景：用户反馈周报末尾的「今日宏观日历」原始列表"根本没看懂"——一屏
# "★★ [EUR] French Flash Manufacturing PMI（预期 50.9，前值 51.5）"这种
# 指标名罗列，既没说这是什么、也没说跟自己有什么关系。原始列表已从展示层
# 移除，改为在这里强制模型**先翻译再落到持仓**。
_MACRO_AI_RULE = (
    "【宏观日历的用法】只挑**对你持仓有实质影响**的 1-2 件展开，其余一律不写。"
    "每写一件必须包含三要素：① 这是什么（一句话大白话，例："
    "「德法PMI = 欧洲制造业景气度」）；② 数据往哪个方向走会怎样；"
    "③ 对你哪一个持仓大类意味着什么。"
    "禁止只罗列指标名（如「关注德法PMI、失业金、消费者信心」）；"
    "禁止出现未经翻译的英文指标名；"
    "禁止把★级/预期值/前值照抄进正文。"
    "若没有值得展开的事件，直接说「本周无关键宏观事件」并转向持仓纪律，不要硬凑。"
)


# ═══════════════════════════════════════════════════════════════
# 场外基金→指数实时穿透映射（白天用指数涨跌估算基金变动）
# ═══════════════════════════════════════════════════════════════

# 关键词 → (数据源, 代码, 折扣系数)
# 折扣系数：联接基金通常有跟踪误差，按 0.95 折算
_FUND_INDEX_MAP: list[tuple[list[str], str, str, float]] = [
    (["纳斯达克", "纳指"], "us_index", "^IXIC", 0.95),
    (["标普500", "标普"], "us_index", "^GSPC", 0.95),
    (["新兴市场"], "us_index", "^GSPC", 0.95),
    (["港股通互联网", "恒生互联网"], "hk_spot", "HSTECH", 0.90),
    (["港股通红利", "恒生红利"], "hk_spot", "HSI", 0.85),
    (["港股消费"], "hk_spot", "HSI", 0.80),
    (["沪港深"], "hk_spot", "HSI", 0.80),
    (["上海金", "黄金"], "us_etf", "GLD", 0.90),
    (["红利低波", "红利"], "cn_index", "000922", 0.90),
    (["节能环保", "环保", "低碳"], "cn_etf", "512580", 0.95),
    (["信用添利"], "cn_etf", "511010", 0.60),
]

# 系数	含义
# 0.95	跟踪误差 ~5%。联接基金持有现金、外汇波动、管理费损耗
# 0.90	跟踪误差 ~10%。港股通有汇率+额度限制，红利策略偏离度更大
# 0.85	跟踪误差 ~15%。红利策略跟恒生不完全同向
# 0.80	跟踪误差 ~20%。沪港深含 A 股，恒生仅是部分参考

# 缓存：基金代码 → 估算涨跌幅（当天有效）
_fund_estimate_cache: dict[str, float] = {}
_ESTIMATE_CACHE_DATE = ""


def _fetch_fund_nav_change(code: str) -> dict | None:
    """🔥 2026-09-04 蛋卷基金接口：按代码查真实净值涨跌（全基金覆盖）。

    覆盖指数映射表以外的产品（债券基金/A股主动股票/QDII 等）。
    Returns: {"pct": float, "date": "YYYY-MM-DD", "is_today": bool}，失败返回 None。
    is_today=True 表示当日净值已发布（晚间场景可直接用真实值）。
    """
    if not code or not str(code).strip().isdigit():
        return None
    try:
        import requests
        r = requests.get(
            f"https://danjuanfunds.com/djapi/fund/{code}",
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)"},
            timeout=6,
        )
        fd = (r.json().get("data", {}) or {}).get("fund_derived", {}) or {}
        pct_raw, date_raw = fd.get("nav_grtd"), fd.get("end_date")
        if pct_raw in (None, "") or date_raw in (None, ""):
            return None
        pct = float(pct_raw)
        import math
        if math.isnan(pct):
            return None
        date_str = str(date_raw)[:10]
        today_str = datetime.now(tz_cn).strftime("%Y-%m-%d")
        return {"pct": pct, "date": date_str, "is_today": date_str == today_str}
    except Exception:
        return None


def _estimate_fund_realtime_pct(code: str, name: str, prefer_nav: bool = False) -> float | None:
    """估算场外基金今日涨跌（%）。

    🔥 2026-09-04 重构（三层策略，解决"部分产品永远估不出"问题）：
    1. prefer_nav=True（晚间，净值应已出）：先查蛋卷当日真实净值
    2. 盘中：关键词 → 指数/ETF 实时映射估算（扩充覆盖环保/信用债/港股消费/新兴市场）
    3. 债券/货币类兜底 0.0（日波动极小，误差可忽略）

    Returns:
        估算涨跌幅（%），无匹配返回 None
    """
    global _fund_estimate_cache, _ESTIMATE_CACHE_DATE
    today_str = datetime.now(tz_cn).strftime("%Y%m%d")
    if _ESTIMATE_CACHE_DATE != today_str:
        _fund_estimate_cache = {}
        _ESTIMATE_CACHE_DATE = today_str

    cache_key = f"{code}:nav" if prefer_nav else f"{code}:est"
    if cache_key in _fund_estimate_cache:
        return _fund_estimate_cache[cache_key]

    # ── 第 1 层：蛋卷当日真实净值（仅 prefer_nav 场景，净值已出时最准）──
    if prefer_nav:
        nav = _fetch_fund_nav_change(code)
        if nav and nav["is_today"]:
            _fund_estimate_cache[cache_key] = nav["pct"]
            return nav["pct"]

    for keywords, source, ticker, ratio in _FUND_INDEX_MAP:
        if any(kw in name for kw in keywords):
            try:
                pct = None
                if source == "us_index":
                    data = market_data.fetch_us_index(ticker)
                    if data:
                        pct = data["change_pct"]
                elif source == "us_etf":
                    data = market_data.fetch_us_etf(ticker)
                    if data:
                        pct = data["change_pct"]
                elif source == "hk_spot":
                    # 🔥 2026-09-23：港股休市时新浪 spot 返回的是**上一交易日**的涨跌幅，
                    #    拿它估算"当日"基金涨跌是错的 → 不取值，落到 None（宁可不给估算）。
                    #    ⚠️ 港股休市日 ≠ A股休市日。
                    if is_hk_market_open():
                        # 2026-09-23 超时保护：见 src/net_guard.py
                        from src.net_guard import import_ak
                        _ak = import_ak()
                        df = _ak.stock_hk_index_spot_sina()
                        target_name = {"HSTECH": "恒生科技指数", "HSI": "恒生指数"}.get(ticker, ticker)
                        rows = df[df['名称']==target_name]
                        if len(rows)>0:
                            pct = float(rows.iloc[0]['涨跌幅'])
                elif source == "cn_index":
                    # 🔥 2026-09-23：A 股休市时同理（日线取到的是上一交易日收 vs 前一日的涨跌）
                    if is_cn_market_open():
                        # 2026-09-23 超时保护：见 src/net_guard.py
                        from src.net_guard import import_ak
                        _ak = import_ak()
                        import os as _os
                        for _k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','all_proxy','ALL_PROXY'):
                            _os.environ.pop(_k, None)
                        df = _ak.stock_zh_index_daily_tx(symbol=f'sh{ticker}')
                        if len(df) >= 2:
                            prev = float(df['close'].iloc[-2])
                            today = float(df['close'].iloc[-1])
                            pct = round((today-prev)/prev*100, 2)
                elif source == "cn_etf":
                    if is_cn_market_open():
                        data = market_data.fetch_cn_etf(ticker)
                        if data:
                            pct = data["change_pct"]

                if pct is not None:
                    # 🔥 2026-07-15：NaN 守卫——行情源可能返回 nan（数据缺失/市场休市时）
                    import math
                    if math.isnan(float(pct)):
                        _fund_estimate_cache[cache_key] = None
                        return None
                    estimate = round(float(pct) * ratio, 2)
                    _fund_estimate_cache[cache_key] = estimate
                    return estimate
            except Exception:
                pass
            break  # 匹配到一个映射就停，不继续尝试

    # ── 第 3 层：债券/货币类兜底（日波动 ±0.05% 量级，估 0 误差可忽略）──
    if any(kw in name for kw in ("债券", "债", "货币", "现金")):
        _fund_estimate_cache[cache_key] = 0.0
        return 0.0

    _fund_estimate_cache[cache_key] = None  # 标记已查过
    return None


def _exchange_rate_footnote(exchange_rates: dict | None = None) -> str:
    """生成汇率折算脚注，仅当有非 CNY 持仓时显示。"""
    if not exchange_rates or len(exchange_rates) <= 1:  # 只有 CNY 时跳过
        return ""
    today_str = datetime.now(tz_cn).strftime("%Y-%m-%d")
    parts = []
    for cur, rate in sorted(exchange_rates.items()):
        if cur == "CNY":
            continue
        parts.append(f"{cur}/CNY={rate:.4f}")
    if not parts:
        return ""
    return f"\n\n*汇率折算基准日：{today_str}　({'　'.join(parts)})"


def _short_name(name: str) -> str:
    """产品名缩短：去掉括号后缀与份额等级(A/C)，截到 8 字。"""
    import re as _re
    n = _re.sub(r"[（(].*?[)）]", "", str(name))
    n = _re.sub(r"\s*[A-Ca-c]$", "", n).strip()
    return n[:8]


def _portfolio_value_summary(label: str = "auto") -> str:
    """🔥 2026-09-04 瘦身版：只交付"本期盈亏一个数字 + 主要贡献 + 总市值"。

    用户反馈逐行持仓清单太长成为阅读负担——完整持仓随时可在飞书多维表格查看。
    数据源（三层，保证每只产品都能算出当日盈亏）：
      - 场内ETF/个股：盘中实时行情
      - 场外基金(盘中)：指数/ETF 关键词映射估算 + 债券/货币兜底
      - 场外基金(晚间)：蛋卷当日真实净值优先，未出则估算兜底

    Args:
        label: "auto" → 根据当前时间自动选；"yesterday" → 昨日实际；"midday" → 盘中估算；"today" → 晚间净值
    """
    try:
        pf = load_portfolio()
        rb = calculate_rebalance(pf)
    except Exception:
        return "持仓数据暂不可用"

    # 自动判断：<12:00 用昨日 | ≥12:00且<20:00 用午盘 | ≥20:00 用今日
    if label == "auto":
        now = datetime.now(tz_cn)
        if now.hour < 12:
            label = "yesterday"
        elif now.hour >= 20:
            label = "today"
        else:
            label = "midday"

    positions = rb.get("positions", [])
    if not positions:
        return ""

    # ── 盘中：为 ETF/个股抓取实时涨跌（避免用飞书缓存的昨日数据）──
    if label in ("midday", "today"):
        from src import market_data as _md
        for pos in positions:
            vehicle = pos.get("investment_vehicle", "")
            if vehicle not in ("场内ETF", "个股"):
                continue
            code = pos.get("code", "")
            if not code:
                continue
            try:
                if code.isdigit() and len(code) == 5:
                    data = _md.fetch_hk_stock(code)
                elif code.isdigit() and len(code) == 6:
                    data = _md.fetch_cn_etf(code)
                elif code.isalpha():
                    data = _md.fetch_us_etf(code)
                else:
                    continue
                if data and data.get("change_pct") is not None:
                    pos["daily_change_pct"] = data["change_pct"]
            except Exception:
                pass

    # ── 逐只确定本期涨跌 → 计算总盈亏与贡献 ──
    contributions: list[tuple[str, float]] = []
    total_pnl = 0.0
    pending: list[str] = []
    for pos in positions:
        name = pos.get("name", "?")
        mv = pos.get("market_value", 0) or 0
        vehicle = pos.get("investment_vehicle", "")
        pct = None

        if label == "yesterday":
            pct = pos.get("daily_change_pct")
        elif vehicle in ("场内ETF", "个股"):
            pct = pos.get("daily_change_pct")  # 上面已实时刷新
        else:
            # 场外基金：晚间先查蛋卷当日真实净值，盘中走指数映射估算
            pct = _estimate_fund_realtime_pct(
                pos.get("code", ""), name,
                prefer_nav=(label == "today"),
            )

        if pct is None:
            pending.append(name)
            continue
        amt = mv * float(pct) / 100
        total_pnl += amt
        contributions.append((name, amt))

    # ── 输出瘦身版 ──
    day_label = "昨日" if label == "yesterday" else "今日"
    src_note = {"midday": "（盘中估算）", "today": "（净值确认）", "yesterday": "（实际）"}.get(label, "")

    lines = ["**💰 持仓速览**"]
    if contributions:
        arrow = "🔺" if total_pnl > 0 else "🔻" if total_pnl < 0 else "➖"
        lines.append(f"💵 {day_label}盈亏{src_note}：{arrow} ¥{total_pnl:+,.0f}")
        # 主要贡献：绝对值 top3 + 其余合计
        ranked = sorted(contributions, key=lambda x: abs(x[1]), reverse=True)
        def _amt(a: float) -> str:
            return f"{'+' if a > 0 else ''}¥{a:,.0f}"
        if len(ranked) > 3:
            rest = sum(a for _, a in ranked[3:])
            parts = [f"{_short_name(n)} {_amt(a)}" for n, a in ranked[:3]] + [f"其余 {_amt(rest)}"]
        else:
            parts = [f"{_short_name(n)} {_amt(a)}" for n, a in ranked]
        lines.append("　" + " ｜ ".join(parts))
    else:
        lines.append(f"💵 {day_label}盈亏：数据待更新")

    total_mv = sum(p.get("market_value", 0) or 0 for p in positions)
    lines.append(f"　总市值 ¥{total_mv:,.0f}")

    if pending:
        shown = "、".join(_short_name(n) for n in pending[:3])
        more = "等" if len(pending) > 3 else ""
        lines.append(f"　⏳ 待更新：{shown}{more}")

    # 汇率脚注
    footnote = _exchange_rate_footnote(rb.get("exchange_rates"))
    if footnote:
        lines.append(footnote)

    return "\n".join(lines)


def _build_fallback_insight(context: str, news_titles: str) -> str:
    """🔥 2026-07-07 容灾降级：LLM 超时/不可用时，用纯文本脱水摘要替代 AI 解读。

    不调任何外部 API，直接从 context 和 news_titles 中提取关键数字，
    拼成一个可读的纯数据摘要推给飞书。确保「通道必达」——宁可推少，不能不推。
    """
    # 提取 context 中 <market_data> 段的前 500 字（包含 VIX/涨跌等硬数据）
    import re
    market_snippet = ""
    if context:
        match = re.search(r"<market_data>(.*?)</market_data>", context, re.DOTALL)
        if match:
            raw = match.group(1).strip()
            market_snippet = raw[:500]

    # 提取中文新闻标题前 8 条
    cn_headlines = []
    for line in news_titles.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith("·") and len(stripped) > 10:
            cn_headlines.append(stripped[:120])
    headlines_text = "\n".join(f"  · {h}" for h in cn_headlines[:8])

    # 提取板块温差信号
    # 2026-09-20：板块改造后喂给 AI 的是一整行「[板块温差] ...」（可含多个 🔥/⚠️），
    # 旧代码统一砍 120 字符会把后半段板块截成半截数字 → 命中该前缀的行单独放宽额度。
    sector_lines = []
    for line in context.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("[板块温差]"):
            sector_lines.append(line[:400])
        elif "⚠️" in line or "🔥" in line:
            sector_lines.append(line[:120])
    sector_text = "\n".join(sector_lines[:5])

    # 用 str.join 拼装，避免 Python 3.12+ f-string 内嵌 \n 的 SyntaxError
    parts = ["⚠️ **AI 解读暂时不可用（超时/服务忙），以下为系统自动生成的脱水数据摘要**", ""]
    if market_snippet:
        parts.append(f"**市场行情**:")
        parts.append(market_snippet)
        parts.append("")
    else:
        parts.append("*(市场数据暂缺)*")
        parts.append("")
    if sector_text:
        parts.append(f"**板块温差信号**:")
        parts.append(sector_text)
        parts.append("")
    if headlines_text:
        parts.append(f"**今日要闻标题**:")
        parts.append(headlines_text)
    else:
        parts.append("*(暂无新闻)*")
    parts.append("")
    parts.append("---")
    parts.append("> 💡 数据直接来自行情源和快讯源，未经 AI 加工。下一次简报将恢复 AI 解读。")
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# 量化硬信号 → LLM 提示注入（D 任务，2026-09-05）
# ═══════════════════════════════════════════════════════════════

# 信号优先级排序：触发行动的排前，HOLD 排后
_SIGNAL_PRIORITY = {
    "TRIGGER_STRONG_BUY": 0,
    "TRIGGER_BUY":        1,
    "TRIGGER_SELL":       2,
    "HOLD_AND_WAIT":      3,
}

# 简称映射（与 prompt_templates.py 保持一致：固收→美股→A股→港股→避险）
_ASSET_SHORT = {
    "固收资产": "固收",
    "美股资产": "美股",
    "A股资产":  "A股",
    "港股资产": "港股",
    "避险商品": "避险商品",
}


def _short_cls(cls: str) -> str:
    return _ASSET_SHORT.get(cls, cls)


def _build_hard_signals_block(verdict: dict | None) -> str:
    """把 judge() 的 signals 列表转成 LLM 可读的结构化硬信号。

    设计目标：
      - D9 的投资宪法是"软"约束（讲理念），这里补"硬"约束（具体判定）
      - LLM 拿到新闻解读时，必须与量化系统的硬信号对齐，不能自相矛盾
      - 例如：硬信号说"美股 TRIGGER_BUY，但冷却期未过" → LLM 就不该建议本周加仓美股

    Args:
        verdict: judge() / judge_from_feishu() 返回的完整 dict（含 signals 列表）

    Returns:
        格式化的 <hard_signals> 段字符串。verdict 为空时返回空串（注入位置不显示该段）。
    """
    if not verdict or not isinstance(verdict, dict):
        return ""

    signals = verdict.get("signals") or []
    if not signals:
        return ""

    # 按信号优先级排序
    sorted_sigs = sorted(
        signals,
        key=lambda s: _SIGNAL_PRIORITY.get(s.get("signal", ""), 9),
    )

    lines = ["<hard_signals>"]
    lines.append("【量化系统硬判定】以下是各持仓大类的偏离度与系统建议（请勿与之矛盾）：")
    lines.append("")

    for s in sorted_sigs:
        cls = s.get("asset_class", "")
        target = s.get("target_weight", "")
        actual = s.get("actual_weight", "")
        dev = s.get("deviation_pct", "")
        label = s.get("signal_label", "")
        override = s.get("override") or ""
        timing = s.get("timing") or ""
        cooldown = s.get("cooldown_status") or ""

        # 偏离度状态 emoji
        try:
            dev_val = float(str(dev).replace("%", "").replace("+", ""))
            if dev_val > 5:
                status = "⚠️ 超配"
            elif dev_val < -5:
                status = "🔻 低配"
            else:
                status = "✅ 正常"
        except (ValueError, AttributeError):
            status = ""

        # 额外约束（override / timing / cooldown）合并
        # ⚠️ 2026-09-17：strategy.judge() 已把 timing 拼进 override 串里
        #    （" | ".join(overrides) + " | " + timing_msg），此处若再无脑追加
        #    timing 会让同一条约束出现两遍，LLM 可能误解为两条不同限制。
        extras_parts = [override]
        if timing and not (override and timing in str(override)):
            extras_parts.append(timing)
        extras_parts.append(cooldown)
        extras = " ｜ ".join(p for p in extras_parts if p)
        extras_short = f"\n    {extras}" if extras else ""

        lines.append(f"◆ {_short_cls(cls)}：实占 {actual}（目标 {target}）偏离 {dev}　{status}　{label}{extras_short}")

    lines.append("")

    # 整体判定
    action = verdict.get("overall_verdict", "HOLD")
    overall = (
        "整体判定：触发配置 — 至少 1 大类需行动"
        if action == "ACT"
        else "整体判定：维持 — 所有大类偏离均在 ±5% 阈值内，长底仓按兵不动"
    )
    lines.append(overall)

    # 增量资金优先方向（如果有）
    priority = verdict.get("priority_target", "")
    if priority:
        lines.append(f"增量资金优先方向：{priority}")

    lines.append("</hard_signals>")

    return "\n".join(lines)


def _ai_insight(context: str, news_titles: str, max_tokens: int = 1024,
                macro_context: str = "", fast_mode: bool = False,
                hard_signals: str = "",
                diff_brief: str = "",
                sector_brief: str = "") -> str:
    """LLM 生成持仓+新闻解读（可结合宏观日历）。D9 重构：引入投资宪法+思维链。

    🔥 2026-07-07 容灾改造：LLM 超时/异常 → 自动降级到 _build_fallback_insight()
    🔥 2026-07-16 fast_mode：跳过宪法+CoT，用于 closing 等轻量快速时段
    🔥 2026-09-05 hard_signals：注入量化系统的硬判定（偏离度+信号+冷却期），让 LLM 不与之矛盾
    🔥 2026-09-05 diff_brief (F 改造)：注入"vs 上次推送的变化"，避免重复昨日结论
    🔥 2026-09-20 sector_brief：板块温差揉进 AI 汇总（展示层已取消独立板块块），
       并强制要求用大白话解析"资金往哪走 + 对哪个持仓大类意味着什么"
    """
    if not news_titles.strip():
        return ""

    pf_summary = _build_portfolio_summary()

    # ── fast_mode：轻量 prompt，不经过 build_analysis_prompt ──
    if fast_mode:
        # 只用核心数据，总 prompt 控制在 800 字以内
        fast_data = news_titles[:400]
        # F 改造：注入 diff_brief，让 LLM 知道"vs 上次有啥变化"
        diff_section = f"\n【vs 上次推送的变化】\n{diff_brief}\n" if diff_brief else ""
        # 2026-09-20：板块温差揉进 AI 层（展示层已取消独立板块块）
        sector_section = f"\n{sector_brief}\n{_SECTOR_AI_RULE}\n" if sector_brief else ""
        fast_prompt = (
            f"你是量化投资顾问。当前语境：{context[:200]}\n"
            f"行情/信号摘要：{fast_data}\n"
            f"持仓偏离度：{pf_summary[:200]}\n"
            f"{sector_section}"
            f"{diff_section}"
            f"要求：100-150字大白话，提1-2个具体持仓大类的影响，"
            f"结尾说一句最值得关注的事。直接输出正文，不要前缀。"
        )
        # DeepSeek 主模型
        try:
            from src.llm import get_llm_client, get_llm_model
            client = get_llm_client()
            if client is not None:
                resp = client.chat.completions.create(
                    model=get_llm_model(), max_tokens=max_tokens,
                    temperature=0.3,
                    messages=[{"role": "user", "content": fast_prompt}],
                )
                content = resp.choices[0].message.content.strip()
                # 检测是否被 max_tokens 硬截断（finish_reason=length），如是则在最近句号收尾避免半句话
                finish_reason = getattr(resp.choices[0], "finish_reason", None)
                if finish_reason == "length":
                    logger.warning("fast_mode DeepSeek 输出被 max_tokens 截断 (%d字)，在句子边界收尾", len(content))
                    content = _truncate_at_sentence_boundary(content)
                if len(content) >= 10:
                    return _sanitize_llm_output(content)
                logger.warning("fast_mode DeepSeek 返回过短 (%d字): %s", len(content), content[:80])
        except Exception as e:
            logger.warning("fast_mode DeepSeek 异常: %s，尝试 Qwen 降级", str(e)[:80])

        # 降级：备模型 Qwen3.5-9B（9B 能力有限，用填空式短 prompt）
        try:
            from src.llm import get_fallback_llm_client, get_fallback_llm_model
            f_client = get_fallback_llm_client()
            if f_client is not None:
                ultra_short = (
                    f"你是量化助手。请从以下数据中挑1-2个最重要的变化，"
                    f"用2-3句大白话（50-80字）说清对持仓的影响。只输出正文。\n\n"
                    f"【行情】{news_titles[:300]}\n"
                    f"【持仓偏离】{pf_summary[:200]}"
                )
                f_resp = f_client.chat.completions.create(
                    model=get_fallback_llm_model(), max_tokens=512,
                    temperature=0.3,
                    messages=[{"role": "user", "content": ultra_short}],
                )
                content = f_resp.choices[0].message.content.strip()
                finish_reason = getattr(f_resp.choices[0], "finish_reason", None)
                if finish_reason == "length":
                    logger.warning("fast_mode 备模型输出被 max_tokens 截断 (%d字)，在句子边界收尾", len(content))
                    content = _truncate_at_sentence_boundary(content)
                if len(content) >= 20:
                    logger.info("fast_mode 备模型 Qwen3.5-9B 降级解读成功")
                    return "[备模型降级] " + _sanitize_llm_output(content)
                logger.warning("fast_mode 备模型返回过短 (%d字): %r", len(content), content[:80])
        except Exception as e2:
            logger.error("fast_mode 备模型降级失败: %r，降到纯文本摘要", e2)

        return _build_fallback_insight(context, news_titles)

    # ── 标准模式（morning/evening）：完整宪法+思维链 ──
    # 拼接市场行情数据（用于 CoT 交叉验证）
    market_text = news_titles[:1000]
    # 2026-09-20：板块温差数据进 AI 层（展示层已取消独立板块块）
    if sector_brief:
        market_text = f"{market_text}\n\n{sector_brief}"
    if macro_context:
        market_text = f"宏观日历:\n{macro_context[:500]}\n\n新闻:\n{market_text}"

    extra_rules = (
        "- 只看新闻标题，推测对持仓大类可能的影响\n"
        "- 如果某条新闻明显利好或利空某类资产，直接说\"这对你的XX持仓是机会/风险，因为...\"\n"
        "- 用大白话写，禁止术语。200-350 字\n"
        "- 如果当日有宏观经济日历事件，必须结合该事件分析对持仓的短期影响，标注⚠️波动预警\n"
        "- 如果新闻自相矛盾，指出矛盾并建议\"以不变应万变，按纪律执行\"\n"
        "- 【硬约束】必须尊重【量化系统硬信号】段的判定；如新闻分析与硬信号冲突（如硬信号说\"美股冷却期未过\"，但新闻让你加仓美股），"
        "以硬信号为准，并简明说明\"虽然新闻利好，但冷却期/趋势左侧/已超配 等约束下本周按兵不动\"\n"
        "- 直接输出正文，不要前缀"
    )
    # 板块解析要求置顶：用户明确要求"AI 解读里要有解析，不然也看不懂"
    if sector_brief:
        extra_rules = _SECTOR_AI_RULE + "\n" + extra_rules
    # 宏观日历同样强制"先翻译再落到持仓"（2026-09-20）
    if macro_context:
        extra_rules = _MACRO_AI_RULE + "\n" + extra_rules

    from src.prompt_templates import build_analysis_prompt
    prompt = build_analysis_prompt(
        role=f"你是量化投资顾问。任务：把当天的财经新闻与真实持仓对照，给出有洞察力的解读。当前语境：{context}",
        holdings_text=pf_summary,
        market_text=market_text,
        extra_rules=extra_rules,
        diff_text=diff_brief,  # F 改造：注入 vs 上次的差异，让 LLM 走"叙事演进"路径
    )

    # ── D 任务：在投资宪法之后注入 hard_signals 段（让 LLM 先看纪律再看硬判定）──
    if hard_signals:
        prompt = prompt.replace(
            "</investment_constitution>",
            f"</investment_constitution>\n\n{hard_signals}",
            1,
        )

    try:
        from src.llm import get_llm_client, get_llm_model
        client = get_llm_client()
        if client is None:
            return _build_fallback_insight(context, news_titles)

        resp = client.chat.completions.create(
            model=get_llm_model(), max_tokens=max_tokens, temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        content = resp.choices[0].message.content.strip()
        # 检测是否被 max_tokens 硬截断（finish_reason=length），如是则在最近句号收尾避免半句话
        finish_reason = getattr(resp.choices[0], "finish_reason", None)
        if finish_reason == "length":
            logger.warning("DeepSeek 输出被 max_tokens 截断 (%d字)，在句子边界收尾", len(content))
            content = _truncate_at_sentence_boundary(content)
        if len(content) >= 10:
            # 2026-09-20：剥推理过程/开场白/四星号（prompt 已要求，此处兜底）
            return _sanitize_llm_output(content)
        logger.warning("DeepSeek 返回过短 (%d字): %s", len(content), content[:80])
    except Exception as e:
        # 🔥 2026-07-14 两层降级：DeepSeek 超时 → Qwen3.6-27B 短 prompt 重试
        # Qwen3.6-27B 和 DeepSeek 不同 GPU 池，高峰不拥堵，质量远高于纯文本兜底
        logger.warning("主模型 DeepSeek 超时/异常: %s，尝试 Qwen3.6-27B 降级", str(e)[:80])

    # ── 降级层：备模型 Qwen3.5-9B（9B 能力有限，填空式 prompt）──
    # 9B 模型不要求"写分析"，给硬数据 + 明确选项，挑最值得说的一件事
    try:
        from src.llm import get_fallback_llm_client, get_fallback_llm_model
        f_client = get_fallback_llm_client()
        if f_client is not None:
            short_prompt = (
                f"你是量化投资助手。以下是今日关键数据，请从中挑1-2个最重要的变化，"
                f"用2-3句大白话（60-100字）说清楚对持仓的影响。只输出正文，不要前缀。\n\n"
                f"【今日市场】\n{news_titles[:400]}\n\n"
                f"【持仓偏离】\n{pf_summary[:250]}\n\n"
                f"【当前时段】{context[:150]}"
            )
            f_resp = f_client.chat.completions.create(
                model=get_fallback_llm_model(), max_tokens=512,
                temperature=0.3,
                messages=[{"role": "user", "content": short_prompt}],
            )
            content = f_resp.choices[0].message.content.strip()
            finish_reason = getattr(f_resp.choices[0], "finish_reason", None)
            if finish_reason == "length":
                logger.warning("备模型输出被 max_tokens 截断 (%d字)，在句子边界收尾", len(content))
                content = _truncate_at_sentence_boundary(content)
            # 提高门槛到 20 字，避免"今天市场波动大注意风险"这类废话
            if len(content) >= 20:
                logger.info("备模型 Qwen3.5-9B 降级解读成功 (%d字)", len(content))
                return "[备模型降级] " + _sanitize_llm_output(content)
            logger.warning("备模型返回过短 (%d字): %r", len(content), content[:80])
    except Exception as e2:
        logger.error("备模型 Qwen3.5-9B 降级失败: %r，降到纯文本摘要", e2)

    return _build_fallback_insight(context, news_titles)


def _skip_msg(reason: str, slot_name: str) -> str | None:
    """如果闭市，返回一条轻量提示卡片。返回 None 表示不发任何推送。"""
    if reason:
        _push(f"{slot_name} — 休市", reason)
    return reason


# ═══════════════════════════════════════════════════════════════
# 六个时段
# ═══════════════════════════════════════════════════════════════

def _build_morning() -> str:
    """08:30 早间简报 + AI 综合解读（含宏观日历）。"""
    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    articles = fetch_all_news(max_results=50)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=8)
    news_block = _fmt_news(filtered, max_items=8)
    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:8])

    # ── 1. VIX ──
    vix_block = _build_vix_block()
    vix_line = "\n" + vix_block + "\n" if vix_block else ""

    # ── 2. 全球市场 ──
    # 早间 8:30 亚太尚未开盘，数据均为上一个交易日收盘
    market_context = _build_global_market_snapshot(prefix="上一交易日收盘")
    market_block = "\n" + market_context + "\n" if market_context else ""

    # ── 2b. 板块温差（仅美股隔夜：8:30 时 A股/港股尚未开盘，取其温差是噪声）──
    sector_line, sector_for_ai = _build_sector_parts(market_filter="us")
    sector_block = "\n" + sector_line + "\n" if sector_line else ""

    # ── 3. 昨日财报 ──
    earnings_block = ""
    yday = []
    try:
        from src.earnings_calendar import fetch_yesterdays_earnings, format_yesterdays_earnings
        yday = fetch_yesterdays_earnings()
        if yday:
            earnings_block = "\n" + format_yesterdays_earnings(yday) + "\n"
    except Exception:
        pass

    # ── 3. 宏观日历 ──
    # 2026-09-20：展示层只留 ★★★ 关键事件一行（无则完全静默），原始指标列表
    # 不再上卡片；数据过滤掉区域性噪声后交给 AI 翻译成大白话（见 _MACRO_AI_RULE）。
    macro_events = filter_portfolio_relevant(fetch_today_calendar(min_impact="Medium"))
    macro_display = format_macro_signal_line(macro_events, scope="今日")
    macro_prompt = calendar_context_for_prompt(macro_events, pf)
    macro_block = "\n" + macro_display + "\n" if macro_display else ""

    # ── 4. 持仓 ──
    value_summary = _portfolio_value_summary()

    # ── 5. 雷达（仅展示信号列表，LLM 解读并入下面的综合解读）──
    radar_block = ""
    try:
        from src.radar import scan_radar, build_radar_brief
        radar_result = scan_radar(dry_run=False)
        if radar_result["signal_items"]:
            _sig_priority = {"🔵 底部反转": 0, "🟡 关注": 1, "🟢 趋势加速": 2}
            _sorted = sorted(
                radar_result["signal_items"],
                key=lambda s: _sig_priority.get(s.get("buy_signal") or s.get("chase_signal", ""), 9)
            )
            _top = _sorted[:5]
            _more = f"\n（另有 {len(radar_result['signal_items']) - 5} 个信号未列出）" if len(_sorted) > 5 else ""
            radar_raw = build_radar_brief(_top) + _more
            # 🔥 2026-07-07：不再单独调 _radar_insight()，信号直接喂给下面的综合解读
            radar_block = "\n" + radar_raw if radar_raw else ""
    except Exception:
        pass

    # ── 6. 国际快讯（已关联持仓）──
    global_block = ""
    try:
        from src.global_news import _build_global_news_brief
        global_news_block = _build_global_news_brief()
        global_block = "\n" + global_news_block + "\n" if global_news_block else ""
    except Exception:
        pass

    # ── 7. AI 综合解读（所有数据就绪后，一次调用）──
    earnings_titles = " ".join(f"{e['ticker']} {e.get('name','')}" for e in yday[:5]) if yday else ""
    radar_snippet = radar_block[:800] if radar_block else ""
    # 🔥 2026-09-15 分层：展示层给 60 字短句，AI 层用完整摘要（5 条 × 150 字）
    global_for_ai = ""
    try:
        from src.global_news import build_global_news_for_ai
        global_for_ai = build_global_news_for_ai()
    except Exception:
        pass
    pf_summary = "\n".join(f"{p.get('name','')[:12]} {p.get('asset_class','')}" for p in pf[:10]) if pf else ""
    trades = _build_trade_summary()
    # 2026-09-20：板块温差经 sector_brief 参数单独注入 AI（不混进 full_context，避免重复）
    full_context = f"{titles_only} {earnings_titles} {trades} {market_context[:300]} {pf_summary} {radar_snippet} {global_for_ai}"
    # ── D 任务：调 judge_from_feishu 拿硬信号，注入 LLM ──
    hard_signals = ""
    _morning_verdict = {}
    try:
        from src.strategy import judge_from_feishu
        _morning_verdict = judge_from_feishu()
        hard_signals = _build_hard_signals_block(_morning_verdict)
    except Exception as e:
        logger.warning("morning 段 judge_from_feishu 失败，硬信号注入跳过: %s", str(e)[:80])

    # 🔥 2026-09-15 E 校正：签名改信息面指纹（新闻/行情/快讯）。
    # 旧版传 None 只看持仓指标 → 固收组合日常波动 < 阈值 → 天天"维持不动"。
    metrics = _extract_metrics_from_verdict(_morning_verdict) or _extract_metrics_from_pf(pf)
    info_fingerprint = f"新闻:{titles_only[:400]}|行情:{market_context[:150]}|快讯:{global_for_ai[:250]}"
    diff = _diff_against_last("morning", _make_signature(info_fingerprint), metrics)
    if diff["has_change"]:
        insight = _ai_insight(
            "早间简报——请综合所有信息（隔夜新闻/昨日财报/近5日交易记录/全球市场/持仓/宏观日历/雷达信号/国际快讯），"
            "给出一段对今天持仓的综合解读，必须提及对具体持仓大类的影响。"
            "如果交易记录显示某大类近期已操作过，在建议中提醒'3天内同一大类已经操作过，按纪律等冷却期'。"
            "结尾用一句话说今天最值得关注的1-2件事。",
            full_context, macro_context=macro_prompt,
            hard_signals=hard_signals, diff_brief=_format_diff_brief(diff),
            sector_brief=sector_for_ai,
        )
    else:
        # 信息面与持仓均无变化（假期/同日重跑）：一行说明，避免整段空白
        insight = "今日较上次推送无显著变化（信息面与持仓指标均稳定），按纪律维持不动。"
    insight_block = "\n🧠 **AI 综合解读**\n" + insight + "\n" if insight else ""

    # 🔥 2026-07-07：快速关注已合并到综合解读中，不再单独调 LLM
    # 原 L764-769 focus = _ai_insight("早间——请给出今天白天最值得关注的1-2件事...") 已删除

    # 🔥 2026-09-23：早间是**唯一没有任何节假日门控**的时段
    #    （midday/closing 走 _CN_GATED，evening 在 builder 内 return "SKIP"）。
    #    A股/港股休市时这里补一行提示，否则读者会把卡片里的 A股/港股数字当成今天的。
    #    见 TODO §1.12 缺口 ②。
    _holiday_notice = holiday_notice()
    holiday_line = f"\n{_holiday_notice}" if _holiday_notice else ""

    card = f"""☀️ **{today} 早间简报**　|　{now.strftime('%H:%M')}{holiday_line}

{vix_line}
{market_block}{sector_block}
**📰 隔夜要闻**
{news_block}
{earnings_block}
{macro_block}
{radar_block}
{global_block}
{value_summary}
{insight_block}> 📐 上午 12:00 推送午间快讯"""

    # E 改造：写本时段快照（签名 = 信息面指纹，卡片含时间戳不可作对比基准）
    _save_snapshot("morning", _make_signature(info_fingerprint), metrics)
    return card


def _build_asia_pacific_market() -> str:
    """亚太市场中午 12:00 实时快照（使用实时/盘中数据源）。"""
    lines = ["**🌏 亚太午盘**"]
    label = "（上午盘收盘）"

    # ── A 股（11:30 上午盘收盘，用新浪实时数据）──
    cn_lines = []
    try:
        import os as _os
        for _k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','all_proxy','ALL_PROXY'):
            _os.environ.pop(_k, None)
        # 2026-09-23 超时保护：见 src/net_guard.py
        from src.net_guard import import_ak
        _ak = import_ak()
        df = _ak.stock_zh_index_spot_sina()
        target_names = {'上证指数': 'sh000001', '深证成指': 'sz399001', '创业板指': 'sz399006'}
        if '名称' in df.columns:
            for name in target_names:
                rows = df[df['名称'] == name]
                if len(rows) > 0:
                    r = rows.iloc[0]
                    price = float(r['最新价'])
                    pct = float(r['涨跌幅'])
                    arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                    cn_lines.append(f"· {name}: {price:,.2f}　{arrow}{pct:+.2f}%")
    except Exception:
        pass
    if cn_lines:
        lines.append("\n**A 股（上午盘收盘）**")
        lines.extend(cn_lines)

    # ── 港股（12:00 上午盘收盘，用新浪实时数据）──
    # 🔥 2026-09-23：港股休市时新浪 spot 接口会返回**上一交易日**的值，
    #    配上「上午盘收盘」的标题会让读者误以为是今天的行情 → 休市日改为显式标注。
    #    ⚠️ 港股休市日 ≠ A股休市日（耶稣受难节 / 佛诞 / 圣诞 等 A股反而开市）。
    if not is_hk_market_open():
        lines.append("\n**港股**：今日休市（无当日行情）")
    else:
        hk_lines = []
        try:
            # 2026-09-23 超时保护：见 src/net_guard.py
            from src.net_guard import import_ak
            _ak = import_ak()
            df = _ak.stock_hk_index_spot_sina()
            target_names = {'恒生指数': 'HSI', '恒生科技指数': 'HSTECH'}
            if '名称' in df.columns:
                for name in target_names:
                    rows = df[df['名称'] == name]
                    if len(rows) > 0:
                        r = rows.iloc[0]
                        price = float(r['最新价'])
                        pct = float(r['涨跌幅'])
                        arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                        hk_lines.append(f"· {name}: {price:,.2f}　{arrow}{pct:+.2f}%")
        except Exception:
            pass
        if hk_lines:
            lines.append("\n**港股（上午盘收盘）**")
            lines.extend(hk_lines)

    # ── 日经/KOSPI/台湾（优先 .info 实时价 → 日线兜底）──
    apac_lines = []
    for ticker, name in [('^N225','日经225'), ('^KS11','韩国KOSPI'), ('^TWII','台湾加权')]:
        try:
            # 2026-09-23 超时保护：见 src/net_guard.py
            from src.net_guard import import_yf
            yf = import_yf()
            t = yf.Ticker(ticker)
            info = t.info
            now_price = info.get('regularMarketPrice') or info.get('currentPrice')
            prev_close = info.get('previousClose') or info.get('regularMarketPreviousClose')
            if now_price and prev_close:
                pct = round((now_price-prev_close)/prev_close*100,2)
                arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                apac_lines.append(f"· {name}: {now_price:,.2f}　{arrow}{pct:+.2f}%（实时）")
        except Exception:
            try:
                df = yf.Ticker(ticker).history(period='5d')
                if len(df) >= 2:
                    prev = float(df['Close'].iloc[-2])
                    today = float(df['Close'].iloc[-1])
                    pct = round((today-prev)/prev*100,2)
                    arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                    apac_lines.append(f"· {name}: {today:,.2f}　{arrow}{pct:+.2f}%（收盘）")
            except Exception:
                pass
    if apac_lines:
        lines.append("\n**亚太其他（实时）**")
        lines.extend(apac_lines)

    if len(lines) == 1:
        return ""
    return "\n".join(lines)

def _build_global_market_snapshot(prefix: str = '') -> str:
    """全球市场快照。prefix=''时为各时段默认标签。"""
    lines = ["**📊 全球市场**"]

    # ── 亚太收盘 ──
    apac_lines = []
    try:
        import os as _os
        for _k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','all_proxy','ALL_PROXY'):
            _os.environ.pop(_k, None)
        # 2026-09-23 超时保护：见 src/net_guard.py
        from src.net_guard import import_ak
        _ak = import_ak()
        for sym, name in [('sh000001','上证指数'), ('sz399001','深证成指'), ('sz399006','创业板指')]:
            try:
                df = _ak.stock_zh_index_daily_tx(symbol=sym)
                if len(df) >= 2:
                    prev = float(df['close'].iloc[-2])
                    today = float(df['close'].iloc[-1])
                    pct = round((today-prev)/prev*100,2)
                    arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                    apac_lines.append(f"· {name}: {today:,.2f}　{arrow}{pct:+.2f}%")
            except Exception:
                pass
    except Exception:
        pass
    # 🔥 2026-09-23：港股休市时 spot / 日线取到的都是**上一交易日**的数据，
    #    小标题写的是「亚太收盘」（读起来像今日），加标注避免误读。
    _hk_stale = "" if is_hk_market_open() else "（上一交易日）"
    for sym, name in [('HSI','恒生指数'), ('HSTECH','恒生科技')]:
        try:
            # 2026-09-23 超时保护：见 src/net_guard.py
            from src.net_guard import import_ak
            _ak = import_ak()
            # 优先用实时 spot（晚间/午间都是当前盘面）
            df = _ak.stock_hk_index_spot_sina()
            rows = df[df['名称']==name]
            if len(rows)>0:
                r = rows.iloc[0]
                price = float(r['最新价'])
                pct = float(r['涨跌幅'])
                arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                apac_lines.append(f"· {name}: {price:,.2f}　{arrow}{pct:+.2f}%{_hk_stale}")
                continue
        except Exception:
            pass
        try:
            df = _ak.stock_hk_index_daily_sina(symbol=sym)
            if len(df) >= 2:
                prev = float(df['close'].iloc[-2])
                today = float(df['close'].iloc[-1])
                pct = round((today-prev)/prev*100,2)
                arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                apac_lines.append(f"· {name}: {today:,.2f}　{arrow}{pct:+.2f}%{_hk_stale}")
        except Exception:
            pass
    for ticker, name in [('^N225','日经225'), ('^KS11','韩国KOSPI'), ('^TWII','台湾加权')]:
        try:
            # 2026-09-23 超时保护：见 src/net_guard.py
            from src.net_guard import import_yf
            yf = import_yf()
            t = yf.Ticker(ticker)
            info = t.info
            now_price = info.get('regularMarketPrice') or info.get('currentPrice')
            prev_close = info.get('previousClose') or info.get('regularMarketPreviousClose')
            if now_price and prev_close:
                pct = round((now_price-prev_close)/prev_close*100,2)
                arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                apac_lines.append(f"· {name}: {now_price:,.2f}　{arrow}{pct:+.2f}%")
        except Exception:
            try:
                df = yf.Ticker(ticker).history(period='5d')
                if len(df) >= 2:
                    prev = float(df['Close'].iloc[-2])
                    today = float(df['Close'].iloc[-1])
                    pct = round((today-prev)/prev*100,2)
                    arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                    apac_lines.append(f"· {name}: {today:,.2f}　{arrow}{pct:+.2f}%")
            except Exception:
                pass
    if apac_lines:
        lines.append("\n**亚太收盘**")
        lines.extend(apac_lines)

    # ── 美股 ──
    us_lines = []
    for ticker, name in [('^GSPC','标普500'), ('^IXIC','纳斯达克')]:
        try:
            data = market_data.fetch_us_index(ticker)
            if data:
                a = "🔺" if data['change_pct'] > 0 else "🔻" if data['change_pct'] < 0 else "➖"
                us_lines.append(f"· {name}: {data['close']:,.2f}　{a}{data['change_pct']:+.2f}%")
        except Exception:
            pass
    for ticker, name in [('^DJI','道琼斯'), ('^SOX','费城半导体')]:
        try:
            data = market_data.fetch_us_index(ticker)
            if data:
                a = "🔺" if data['change_pct'] > 0 else "🔻" if data['change_pct'] < 0 else "➖"
                us_lines.append(f"· {name}: {data['close']:,.2f}　{a}{data['change_pct']:+.2f}%")
        except Exception:
            pass
    if us_lines:
        lines.append("\n**美股收盘**")
        lines.extend(us_lines)

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _build_midday() -> str:
    """12:00 亚太午盘收盘快讯。需要 A 股开市。"""
    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    # ── 亚太市场 ──
    apac_market = _build_asia_pacific_market()
    apac_block = "\n" + apac_market + "\n" if apac_market else ""

    articles = fetch_all_news(max_results=40)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=6)
    news_block = _fmt_news(filtered, max_items=6)
    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:6])

    # ── 板块温差（仅港股+A股实时温差；美股为隔夜数据不重复展示）──
    # 2026-09-20 改造：不再独立成块（曾是午间卡片 45%+ 篇幅且与夜盘重复），
    # 数据全部并入下方「午间快评」由 AI 解析，仅强信号时保留一行提示。
    sector_line, sector_for_ai = _build_sector_parts(market_filter="hk_cn")
    sector_block = f"\n{sector_line}\n" if sector_line else ""

    # AI 快评 (max_tokens=800 避免 max_tokens 截断导致吞字)
    insight = _ai_insight(
        "午间要闻——请根据上午新闻和亚太市场表现给出对下午A股走势的1-2点观察",
        titles_only, max_tokens=800, sector_brief=sector_for_ai,
    )
    insight_block = f"\n🧠 **午间快评**\n{insight}\n" if insight else ""

    value_summary = _portfolio_value_summary()

    return f"""🌤️ **{today} 午间快讯**　|　{now.strftime('%H:%M')}

{apac_block}{sector_block}
**📰 上午要闻**
{news_block}
{value_summary}
{insight_block}
**💡 下午关注**
· 亚太市场午后走势
· 14:30 收盘前报告（15:00 场外基金截单）"""


def _build_closing() -> str:
    """14:30 A 股收盘前 30 分钟策略防御带 -- 仓位健康 + 雷达扫描 + 市场基准。"""
    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    from src.strategy import judge_from_feishu
    verdict = judge_from_feishu()

    articles = fetch_all_news(max_results=30)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=5)
    news_block = _fmt_news(filtered, max_items=5)

    # ── 仓位健康报告（只做偏离度展示）──
    health = verdict.get("health_report", "")
    health_block = f"\n{health}\n" if health else ""

    # ── 持仓市值 ──
    value_summary = _portfolio_value_summary()

    # ── 雷达扫描（仅展示信号列表，LLM 解读并入下面的综合解读）──
    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:8])
    radar_block = ""
    try:
        from src.radar import scan_radar, build_radar_brief
        radar_result = scan_radar(dry_run=False)
        if radar_result["signal_items"]:
            _sig_priority = {"🔵 底部反转": 0, "🟡 关注": 1, "🟢 趋势加速": 2}
            _sorted = sorted(
                radar_result["signal_items"],
                key=lambda s: _sig_priority.get(s.get("buy_signal") or s.get("chase_signal", ""), 9)
            )
            _top = _sorted[:5]
            _more = f"\n（另有 {len(radar_result['signal_items']) - 5} 个信号未列出）" if len(_sorted) > 5 else ""
            radar_raw = build_radar_brief(_top) + _more
            radar_block = f"\n{radar_raw}\n" if radar_raw else ""
    except Exception:
        pass

    # ── 美股盘前风向（14:30看盘中，不展示昨日收盘）──
    futures_raw = _build_us_futures_block()
    futures_block = f"\n🌙 **美股盘前风向**\n{futures_raw}\n" if futures_raw else ""

    # ── 板块温差（2026-09-20：揉进 AI 汇总，仅强信号留一行）──
    # fast_mode 语境短，AI 层只取前 3 个板块控 prompt 长度
    sector_line, sector_for_ai = _build_sector_parts(max_ai_items=3)
    sector_block = f"\n{sector_line}\n" if sector_line else ""
    sector_raw = sector_for_ai          # 兼容下方指纹与 AI 上下文（原为展示块文本）

    # ── 国际 RSS ──
    global_block = ""
    try:
        from src.global_news import _build_global_news_brief
        global_news_block = _build_global_news_brief()
        global_block = f"\n{global_news_block}\n" if global_news_block else ""
    except Exception:
        pass

    # ── AI 综合解读（🔥 fast_mode：收盘前30分钟轻量快速，不加载宪法/思维链）──
    # 只喂核心信号和新闻标题，总 prompt 控制在 800 字以内
    radar_snippet = radar_block[:300] if radar_block else ""
    futures_snippet = futures_raw[:150] if futures_raw else ""
    # 2026-09-20：板块不再进 slim_context（改由 sector_brief 单独注入，
    # 避免与 fast_prompt 的 sector_section 重复占篇幅）
    sector_snippet = ""
    # 🔥 2026-09-15 分层：AI 层用完整国际快讯（截 400 字适配 fast_mode 轻量语境）
    global_for_ai = ""
    try:
        from src.global_news import build_global_news_for_ai
        global_for_ai = build_global_news_for_ai()[:400]
    except Exception:
        pass
    slim_context = f"{titles_only[:200]} {futures_snippet} {radar_snippet} {global_for_ai}"

    # 🔥 2026-09-15 E 校正：签名改信息面指纹（收盘时点：当日新闻 + 盘前风向 + 板块）
    metrics = _extract_metrics_from_verdict(verdict)
    info_fingerprint = f"新闻:{titles_only[:300]}|期货:{futures_raw[:100]}|板块:{sector_raw[:200]}|快讯:{global_for_ai[:150]}"
    diff = _diff_against_last("closing", _make_signature(info_fingerprint), metrics)
    if diff["has_change"]:
        insight = _ai_insight(
            "收盘前30分钟——请快速综合以下信号给出建议",
            slim_context, max_tokens=500, fast_mode=True, diff_brief=_format_diff_brief(diff),
            sector_brief=sector_for_ai)
    else:
        insight = "今日较上次推送无显著变化（信息面与持仓指标均稳定），收盘前按纪律维持不动。"
    insight_block = f"\n🧠 **AI 综合解读**\n{insight}\n" if insight else ""

    # 🔥 2026-07-07：快速关注已合并到综合解读中，不再单独调 LLM
    # 原 L1075-1079 focus = _ai_insight("收盘前——请给出今天剩下的时间最值得关注的1件事...") 已删除

    card = f"""⚡ **{today} 收盘前指令**　|　{now.strftime('%H:%M')}　⏰ 距 15:00 截单还有 30 分钟

**📰 午间要闻**
{news_block}
{futures_block}{sector_block}{radar_block}
{global_block}
{health_block}
{value_summary}
{insight_block}
🔔 总市值 ¥{verdict['total_value']:,.2f}　|　长底仓只买不卖{_exchange_rate_footnote(verdict.get('exchange_rates'))}

> 以上结论由量化系统计算，仅供参考，不构成投资建议"""

    # E 改造：写本时段快照（签名 = 信息面指纹，覆盖上次）
    _save_snapshot("closing", _make_signature(info_fingerprint), metrics)
    return card


def _build_evening() -> str:
    """21:00 夜盘前瞻 + AI 综合解读。需要美股开市。"""
    if not is_us_market_open():
        return "SKIP"

    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    articles = fetch_all_news(max_results=40)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=8)
    news_block = _fmt_news(filtered, max_items=8)
    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:8])

    # ── 1. VIX 恐慌指数 ──
    vix_block = _build_vix_block()
    vix_line = f"\n{vix_block}\n" if vix_block else ""

    # ── 2. 美股期货实时 ──
    futures_raw = _build_us_futures_block()
    futures_block = f"\n📡 **美股期货实时**\n{futures_raw}\n" if futures_raw else ""

    # ── 3. 板块温差（2026-09-20：揉进 AI 汇总，仅强信号留一行）──
    sector_line, sector_for_ai = _build_sector_parts()
    sector_block = f"\n{sector_line}\n" if sector_line else ""
    sector_raw = sector_for_ai          # 兼容下方 AI 上下文（原为展示块文本）

    # ── 4. 全球市场 ──
    market_context = _build_global_market_snapshot()
    market_block = f"\n{market_context}\n" if market_context else ""

    # ── 5. 近期财报提示 ──
    earnings_block = ""
    today_earnings = []
    try:
        from src.earnings_calendar import fetch_weekly_earnings, format_weekly_earnings
        today_earnings = fetch_weekly_earnings(days_ahead=1)
        if today_earnings:
            earnings_block = "\n" + format_weekly_earnings(today_earnings) + "\n"
    except Exception:
        pass

    # ── 3. 持仓市值 ──
    value_summary = _portfolio_value_summary()

    # ── 4. 雷达扫描（仅展示信号列表，LLM 解读并入下面的综合解读）──
    radar_block = ""
    try:
        from src.radar import scan_radar, build_radar_brief
        radar_result = scan_radar(dry_run=False)
        if radar_result["signal_items"]:
            _sig_priority = {"🔵 底部反转": 0, "🟡 关注": 1, "🟢 趋势加速": 2}
            _sorted = sorted(
                radar_result["signal_items"],
                key=lambda s: _sig_priority.get(s.get("buy_signal") or s.get("chase_signal", ""), 9)
            )
            _top = _sorted[:5]
            _more = f"\n（另有 {len(radar_result['signal_items']) - 5} 个信号未列出）" if len(_sorted) > 5 else ""
            radar_raw = build_radar_brief(_top) + _more
            radar_block = f"\n{radar_raw}\n" if radar_raw else ""
    except Exception:
        pass

    # ── 5. 国际快讯（已关联持仓）──
    global_block = ""
    try:
        from src.global_news import _build_global_news_brief
        global_news_block = _build_global_news_brief()
        global_block = f"\n{global_news_block}\n" if global_news_block else ""
    except Exception:
        pass

    # ── 6. AI 综合解读（汇总所有信息，结合持仓）──
    earnings_titles = " ".join(f"{e['ticker']}{e.get('name','')}" for e in today_earnings[:5]) if today_earnings else ""
    market_snippet = market_context[:300] if market_context else ""
    radar_snippet = radar_block[:800] if radar_block else ""
    # 🔥 2026-09-15 分层：展示层给 60 字短句，AI 层用完整摘要（5 条 × 150 字）
    global_for_ai = ""
    try:
        from src.global_news import build_global_news_for_ai
        global_for_ai = build_global_news_for_ai()
    except Exception:
        pass
    pf_summary = "\n".join(f"{p.get('name','')[:12]} {p.get('asset_class','')}" for p in pf[:10]) if pf else ""
    trades = _build_trade_summary()
    futures_snippet = futures_raw[:200] if futures_raw else ""
    # 2026-09-20：板块不再混进 full_context（改由 sector_brief 单独注入，避免重复占位）
    full_context = f"{titles_only} {trades} {earnings_titles} {futures_snippet} {market_snippet} {pf_summary} {radar_snippet} {global_for_ai}"

    # ── D 任务：调 judge_from_feishu 拿硬信号，注入 LLM ──
    hard_signals = ""
    _evening_verdict = {}
    try:
        from src.strategy import judge_from_feishu
        _evening_verdict = judge_from_feishu()
        hard_signals = _build_hard_signals_block(_evening_verdict)
    except Exception as e:
        logger.warning("evening 段 judge_from_feishu 失败，硬信号注入跳过: %s", str(e)[:80])

    # 🔥 2026-09-15 E 校正：签名改信息面指纹（夜盘时点：当日新闻 + 美股盘前 + 快讯）
    metrics = _extract_metrics_from_verdict(_evening_verdict) or _extract_metrics_from_pf(pf)
    info_fingerprint = f"新闻:{titles_only[:400]}|期货:{futures_raw[:100]}|快讯:{global_for_ai[:250]}"
    diff = _diff_against_last("evening", _make_signature(info_fingerprint), metrics)
    if diff["has_change"]:
        insight = _ai_insight(
            "今晚夜盘前瞻——请综合以下所有信息（国内新闻/近5日交易记录/国际快讯/全球市场/持仓/雷达信号/近期财报），"
            "给出一段对今晚美股和明天持仓的综合解读，必须提及对具体持仓大类的影响。"
            "结尾用一句话说今晚/明天最值得关注的1-2件事",
            full_context, hard_signals=hard_signals, diff_brief=_format_diff_brief(diff),
            sector_brief=sector_for_ai,
        )
    else:
        insight = "今日较上次推送无显著变化（信息面与持仓指标均稳定），夜盘按纪律维持不动。"
    insight_block = f"\n🧠 **AI 综合解读**\n{insight}\n" if insight else ""

    # 🔥 2026-07-07：快速关注已合并到综合解读中，不再单独调 LLM
    # 原 L1182-1187 focus = _ai_insight("今晚——请给出今晚/明天最值得关注的1-2件事...") 已删除

    card = f"""🌆 **{today} 夜盘前瞻**　|　{now.strftime('%H:%M')}

{value_summary}
{vix_line}
{futures_block}{sector_block}{market_block}
**📰 今日要闻**
{news_block}
{earnings_block}
{radar_block}
{global_block}
{insight_block}> ☀️ 明早 08:30 推送美股隔夜收盘复盘"""

    # E 改造：写本时段快照（签名 = 信息面指纹）
    _save_snapshot("evening", _make_signature(info_fingerprint), metrics)
    return card


def _build_sat_morning() -> str:
    """周六 08:30 周五美股收盘复盘。"""
    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    vix = market_data.fetch_vix()
    vix_str = f"{vix['vix']:.1f}（{vix['level']}）" if vix and vix.get("vix") else "获取失败"
    spx = market_data.fetch_us_etf("SPY")
    qqq = market_data.fetch_us_etf("QQQ")
    spx_str = f"${spx['close']:.2f}（{spx['change_pct']:+.2f}%）" if spx else "获取失败"
    qqq_str = f"${qqq['close']:.2f}（{qqq['change_pct']:+.2f}%）" if qqq else "获取失败"

    articles = fetch_all_news(max_results=40)
    pf = load_portfolio()
    filtered = _filter_by_keywords(articles, pf, top_n=6)
    news_block = _fmt_news(filtered, max_items=6)
    titles_only = " ".join(_clean_html(a.get("title", "")) for a in filtered[:6])

    insight = _ai_insight(
        "周五美股收盘总结——本周美股表现如何？对下周持仓有什么影响？",
        titles_only, max_tokens=800,
    )

    insight_block = f"\n🧠 **本周美股回顾**\n{insight}\n" if insight else ""

    return f"""📅 **{today} 周末复盘**　|　{now.strftime('%H:%M')}

**🇺🇸 周五美股收盘**
· 标普500：{spx_str}
· 纳斯达克100：{qqq_str}
· VIX：{vix_str}

**📰 本周要闻**
{news_block}
{insight_block}> ☀️ 周日 20:00 推送下周前瞻"""


def _build_weekly_return(pf: list[dict]) -> str:
    """计算本周持仓收益 vs 基准。"""
    from src.radar import _fetch_historical_prices

    total_start = 0.0
    total_end = 0.0
    lines = []

    for p in pf:
        shares = float(p.get("shares", 0))
        latest = float(p.get("latest_price", 0))
        code = p.get("code", "")
        name = p.get("name", "")
        if shares <= 0 or not code:
            continue
        mv_end = shares * latest
        total_end += mv_end

        hist = _fetch_historical_prices(code, days=10)
        if hist and len(hist["prices"]) >= 8:
            # 约 7 天前价格
            price_7d_ago = hist["prices"][-min(8, len(hist["prices"]))]
            mv_start = shares * price_7d_ago
            total_start += mv_start
        else:
            total_start += mv_end  # 无历史数据，假设不变

    if total_start <= 0:
        return ""

    week_pnl = total_end - total_start
    week_pct = (week_pnl / total_start) * 100
    arrow = "🔺" if week_pnl > 0 else "🔻" if week_pnl < 0 else "➖"
    lines.append(f"**📊 本周仓位盘点**")
    lines.append(f"总市值 ¥{total_end:,.0f}　本周 {arrow} ¥{week_pnl:+,.0f}（{week_pct:+.1f}%）")

    return "\n".join(lines)


def _build_sun_evening() -> str:
    """周日 20:00 周报 —— 仓位盘点 + 周末要闻 + 宏观回顾 + 下周关注。"""
    now = datetime.now(tz_cn)
    today = now.strftime("%Y-%m-%d")

    pf = load_portfolio()

    # ── 1. 仓位盘点 ──
    weekly_return = _build_weekly_return(pf)

    # ── 2. 仓位安全垫分布（静态，不引导操作）──
    from src.strategy import judge_from_feishu
    verdict = judge_from_feishu()
    health = verdict.get("health_report", "")
    # 剔除「增量资金优先方向」这类无法在周日执行的动态话术
    health = health.replace("【增量资金优先方向】", "").strip()

    # ── 3. 周末要闻复盘：专门拉周六+周日的新闻 ──
    weekend_articles = fetch_all_news(max_results=40)
    weekend_filtered = _filter_by_keywords(weekend_articles, pf, top_n=6)
    weekend_titles = " ".join(_clean_html(a.get("title", "")) for a in weekend_filtered[:6])

    weekend_news_summary = ""
    if weekend_titles.strip():
        try:
            from src.llm import get_llm_client, get_llm_model
            client = get_llm_client()
            if client:
                resp = client.chat.completions.create(
                    model=get_llm_model(), max_tokens=150, temperature=0.3,
                    messages=[{"role": "user", "content": f"""你是量化投资顾问。以下是周末两天的财经新闻标题。
提炼最重要的 2-3 条高价值资讯，每条约 20 字，用大白话写。

<weekend_news>
{weekend_titles[:800]}
</weekend_news>

直接输出，每条一行，格式：· xxx。不要前缀。"""}],
                )
                weekend_news_summary = _sanitize_llm_output(resp.choices[0].message.content)
        except Exception:
            pass
    weekend_block = f"\n📅 **周末要闻复盘**\n{weekend_news_summary}\n" if weekend_news_summary else ""

    # ── 4. 宏观日历 ──
    # 2026-09-20：展示层只留 ★★★ 一行（无则完全静默）；原「今日宏观日历」原始
    # 列表已删——它标题写死"今日"却在周日展示**下周**事件，且大半是德法 PMI
    # 这类对持仓只有间接链条的区域数据，用户明确反馈"根本没看懂"。
    # 数据过滤后交给 AI 用大白话解析（_MACRO_AI_RULE），并补齐持仓预警。
    from src.macro_calendar import (
        fetch_past_calendar,
        fetch_upcoming_calendar,
        format_macro_signal_line,
        filter_portfolio_relevant,
        calendar_context_for_prompt,
    )
    past_events = fetch_past_calendar(min_impact="Medium", days_behind=7)
    future_events = filter_portfolio_relevant(
        fetch_upcoming_calendar(min_impact="Medium", days_ahead=7)
    )
    future_macro_display = format_macro_signal_line(future_events, scope="下周")

    # ── 5. 国际要闻 ──
    global_news_text = ""
    try:
        from src.global_news import _build_global_news_brief
        gnb = _build_global_news_brief()
        if gnb:
            global_news_text = gnb
    except Exception:
        pass

    # ── 6. 下周财报 ──
    earnings_block = ""
    try:
        from src.earnings_calendar import fetch_weekly_earnings, format_weekly_earnings
        wk_earnings = fetch_weekly_earnings(days_ahead=7)
        if wk_earnings:
            earnings_block = format_weekly_earnings(wk_earnings)
    except Exception:
        pass

    # ── 7. LLM 综合：宏观回顾 + 下周关注（交叉推演周末要闻+日历）──
    past_summary = "\n".join(
        f"· {e.get('date','')} {e.get('title','')} [{e.get('stars','')}]"
        for e in past_events[:8]
    ) if past_events else "(本周无重大宏观事件)"

    future_summary = calendar_context_for_prompt(future_events, pf) if future_events else ""

    llm_block = ""
    if past_events or future_events or global_news_text or weekend_news_summary:
        try:
            from src.llm import get_llm_client, get_llm_model
            client = get_llm_client()
            if client:
                extra_rules = (
                    "- 第一部分「本周宏观回顾」：从已发生宏观事件中挑最重要的2-3件，每件1句话+对持仓大类的影响\n"
                    "- 第二部分「下周防守与狙击要点」：必须结合周末要闻复盘+下周宏观日历+下周财报，交叉推演2-3条方向性提示\n"
                    "  格式：如果X发生→Y大类会怎样→你应该Z。禁止\"适当关注\"这类废话\n"
                    "- 总共200-250字\n"
                    "- 输出格式：\n"
                    "  📅 本周宏观回顾\n"
                    "  · （事件1 + 对持仓的影响）\n"
                    "  · （事件2 + 对持仓的影响）\n\n"
                    "  🛡️ 下周防守与狙击要点\n"
                    "  · （推演1：触发条件→影响→方向）\n"
                    "  · （推演2：触发条件→影响→方向）\n"
                    + _MACRO_AI_RULE
                )

                from src.prompt_templates import build_analysis_prompt
                prompt = build_analysis_prompt(
                    role="你是量化投资顾问。请根据以下信息产出周报的宏观回顾和下周防守要点。",
                    holdings_text=f"{weekly_return}\n\n仓位安全垫:\n{health[:300]}",
                    # 日历数据走 macro_text（渲染为 <macro_calendar> 块），
                    # 与 _MACRO_AI_RULE 里"【宏观日历】"的指代保持一致
                    macro_text=f"已发生事件:\n{past_summary}\n\n未来日历:\n{future_summary}",
                    news_text=f"周末要闻:\n{weekend_news_summary[:300] if weekend_news_summary else '(无)'}\n\n"
                              f"下周财报:\n{earnings_block[:300] if earnings_block else '(无)'}\n\n"
                              f"国际快讯:\n{global_news_text[:400] if global_news_text else '(无)'}",
                    extra_rules=extra_rules,
                )
                resp = client.chat.completions.create(
                    model=get_llm_model(), max_tokens=1024, temperature=0.3,
                    messages=[{"role": "user", "content": prompt}],
                )
                llm_block = resp.choices[0].message.content.strip()
                # 检测是否被 max_tokens 硬截断（finish_reason=length），如是则在最近句号收尾避免半句话
                _fr = getattr(resp.choices[0], "finish_reason", None)
                if _fr == "length":
                    logger.warning("周报 LLM 输出被 max_tokens 截断 (%d字)，在句子边界收尾", len(llm_block))
                    llm_block = _truncate_at_sentence_boundary(llm_block)
                # 2026-09-20：周报是推理过程泄漏最严重的卡（实测占 58%）
                # → 剥推理尾巴 + 去开场白 + 修四星号
                llm_block = _sanitize_llm_output(llm_block)
        except Exception:
            pass

    card = f"""📅 **{today} 周报**

{weekly_return}

**🛡️ 当前仓位安全垫分布**
{health}

{weekend_block}
{llm_block}

{earnings_block}

{future_macro_display}"""

    # E 改造：写本时段快照（周报 diff 价值不大，但保持快照连续性）
    # 签名用周报正文（去掉含时间戳的标题行）——周报正文每周必然不同，
    # 与"卡片含 HH:MM 恒变"不同，正文差异才反映真实信息更新
    metrics = _extract_metrics_from_verdict(verdict) if verdict else {}
    _body_for_sig = "\n".join(card.split("\n")[1:60])
    _save_snapshot("sun_evening", _make_signature(_body_for_sig), metrics)
    return card


# ═══════════════════════════════════════════════════════════════
# 变化感知 + 快照 (E 改造, 2026-09-05; 2026-09-15 二次校正)
# ═══════════════════════════════════════════════════════════════
#
# 思路：
#   - 每个时段推送时把"信息面指纹"(新闻标题+行情+快讯的 hash) 和 key_metrics
#     (总市值/偏离度) 写入快照 (飞书 / 本地 fixture)
#   - 下次推送开头读上一期快照，做 diff
#   - diff 无变化 → 跳过 AI 解读 (真正无新信息的日子：假期/同日重跑)
#   - diff 有变化 → 调 LLM，注入 diff 摘要
#
# 🔥 2026-09-15 二次校正（用户实测 10 天反馈"天天无显著变化，无有效信息"）：
#   - 旧版 signature 传 None → 只按持仓指标判定；而 7 万固收为主组合日常
#     波动 Δ市值 ~¥30 / Δ偏离度 ~0.1%，永远低于阈值 → 天天跳过 LLM。
#   - 新版：signature = 信息面指纹（当日新闻标题/行情/快讯的 hash）。
#     新闻每天必不同 → 正常交易日都会调 LLM 解读当日新信息；
#     只有信息面+持仓都无变化（假期/同日重跑）才跳过。
#   - 教训：卡片签名不可用作对比（卡片含 HH:MM 时间戳，恒不相同）；
#     持仓指标稳定性 ≠ 信息无更新——用户要的信息量来自信息面。

# diff 阈值：超过这些值才视为"有变化"
_METRIC_THRESHOLDS = {
    "total_value": 50,        # ¥50（原 100：7 万组合日常波动常在阈值下）
    "deviation_us": 0.3,      # 0.3%（原 0.5：偏离度日波动 0.1-0.2%，原阈值几乎不可触发）
    "deviation_cn": 0.3,
    "deviation_hk": 0.3,
    "deviation_bond": 0.3,
    "deviation_safe": 0.3,
}


def _make_signature(card_text: str) -> str:
    """根据 card 文本生成短签名。前 800 字符足够区分（数据卡基本稳定）。"""
    sample = card_text[:800]
    return hashlib.md5(sample.encode("utf-8")).hexdigest()[:12]


def _load_prev_snapshot(slot: str) -> dict | None:
    """读某 slot 上次推送的快照。封装 feishu_client，本地/生产自动分发。"""
    from src.feishu_client import read_briefing_snapshot
    return read_briefing_snapshot(slot)


def _save_snapshot(slot: str, signature: str, key_metrics: dict) -> None:
    """写本时段快照。"""
    from src.feishu_client import write_briefing_snapshot
    write_briefing_snapshot(slot, key_metrics, signature)


def _diff_against_last(slot: str, current_signature: str | None,
                       current_metrics: dict) -> dict:
    """对比当前 vs 上次快照。

    Args:
        slot: 时段名
        current_signature: 本次信息面指纹（新闻标题+行情+快讯的 hash）。
            传 None 表示"调用点拿不到信息面"（极端容错），此时只按 metrics 判定。
        current_metrics: 本次关键指标

    Returns:
        {
            "has_change": bool,            # 总判定：是否有显著变化
            "signature_changed": bool,     # 信息面（新闻/行情）是否更新
            "metric_changes": [str, ...],  # 人类可读的变化列表
            "prev_signature": str,
            "prev_metrics": dict,
            "is_first_run": bool,          # 是否首次推送（无历史）
        }
    """
    prev = _load_prev_snapshot(slot)
    if prev is None:
        # 首次推送（或上次推送失败）—— 视为有变化，避免错过第一次 LLM
        return {
            "has_change": True,
            "signature_changed": True,
            "metric_changes": ["首次推送，无历史对比"],
            "prev_signature": "",
            "prev_metrics": {},
            "is_first_run": True,
        }

    prev_sig = prev.get("signature", "")
    prev_metrics = prev.get("payload", {})

    # 签名未知（极端容错）时不参与判定，只看 metrics。
    # ⚠️ 2026-09-15 校正：signature 语义已改为"信息面指纹"（当日新闻+行情+快讯
    #    的 hash）。新闻每天必不同 → 正常交易日恒触发 LLM 解读当日新信息。
    sig_changed = current_signature is not None and prev_sig != current_signature

    metric_changes: list[str] = []
    for k, v in current_metrics.items():
        old = prev_metrics.get(k)
        if old is None:
            metric_changes.append(f"新增指标 {k}={v}")
            continue
        try:
            delta = abs(float(v) - float(old))
        except (TypeError, ValueError):
            continue
        thr = _METRIC_THRESHOLDS.get(k, 0.5)
        if delta >= thr:
            metric_changes.append(f"{k}: {old} → {v} (Δ{delta:+.2f})")

    return {
        "has_change": sig_changed or bool(metric_changes),
        "signature_changed": sig_changed,
        "metric_changes": metric_changes,
        "prev_signature": prev_sig,
        "prev_metrics": prev_metrics,
        "is_first_run": False,
    }


def _format_diff_brief(diff: dict) -> str:
    """把 diff 转成给 LLM 看的"上下文卡"。"""
    if diff["is_first_run"]:
        return "（首次推送，无历史对比）"
    lines = []
    if diff["signature_changed"]:
        lines.append("- 信息面（新闻/行情/快讯）较上次推送有更新，请解读当日新信息")
    for ch in diff["metric_changes"][:5]:
        lines.append(f"- 持仓指标变化：{ch}")
    if not lines:
        lines.append("- 信息面与持仓指标均无显著变化")
    return "\n".join(lines)


def _extract_metrics_from_verdict(verdict: dict) -> dict:
    """从 judge() verdict 提取 diff 用的 key_metrics。

    Returns:
        {"total_value": float, "deviation_us": float, "deviation_cn": float, ...}
    """
    metrics: dict = {}
    total = verdict.get("total_value", 0)
    if total:
        metrics["total_value"] = round(float(total), 2)
    for sig in verdict.get("signals", []) or []:
        cls_short = _short_cls(sig.get("asset_class", ""))
        # 优先用 deviation_pct 字段（已经是 %）；否则 actual-target 算
        dev_text = sig.get("deviation_pct", "")
        try:
            if dev_text:
                dev = float(str(dev_text).rstrip("%"))
            else:
                actual = float(str(sig.get("actual_weight", "0")).rstrip("%"))
                target = float(str(sig.get("target_weight", "0")).rstrip("%"))
                dev = actual - target
            metrics[f"deviation_{cls_short}"] = round(dev, 2)
        except Exception:
            continue
    return metrics


def _extract_metrics_from_pf(pf: list[dict] | None) -> dict:
    """无 verdict 时 fallback：从 pf + 默认 target 算 metrics。"""
    if not pf:
        return {}
    from src.advisor import calculate_rebalance
    from src.constants import TARGET_WEIGHTS
    try:
        rb = calculate_rebalance(pf)
    except Exception:
        return {"total_value": 0}
    total = rb.get("total_value", 0)
    metrics = {"total_value": round(float(total), 2)} if total else {}
    for d in rb.get("deviation_report", []) or []:
        cls = d.get("asset_class", "")
        cls_short = _short_cls(cls)
        try:
            dev = float(str(d.get("deviation_pct", "0")).rstrip("%"))
            metrics[f"deviation_{cls_short}"] = round(dev, 2)
        except Exception:
            continue
    return metrics


# ═══════════════════════════════════════════════════════════════
# 路由与入口
# ═══════════════════════════════════════════════════════════════

BRIEFINGS = {
    "morning":      ("☀️ 早间简报", _build_morning),
    "midday":       ("🌤️ 午间快讯", _build_midday),
    "closing":      ("⚡ 收盘前指令", _build_closing),
    "evening":      ("🌆 夜盘前瞻", _build_evening),
    "sat_morning":  ("📅 周末复盘", _build_sat_morning),
    "sun_evening":  ("📅 下周前瞻", _build_sun_evening),
}

# 需要 A 股开市才运行的时段
_CN_GATED = {"midday", "closing"}
# 注：美股熔断**不在这里** —— 它在 `_build_evening()` 内部直接 `return "SKIP"`。
#     原先还有一个 `_US_GATED = {"evening", "sat_morning"}`，已于 2026-09-23 删除：
#     它全仓零调用，而且**设计上是错的** —— 周六永远不是美股交易日，
#     若真按它门控，周末复盘会每周都被跳过。详见 TODO §1.12。


def main():
    from dotenv import load_dotenv
    load_dotenv()

    if len(sys.argv) < 2 or sys.argv[1] not in BRIEFINGS:
        print("用法: python -m src.briefing [morning|midday|closing|evening|sat_morning|sun_evening]")
        sys.exit(1)

    mode = sys.argv[1]
    title, builder = BRIEFINGS[mode]

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",
    )

    # ── 节假日熔断 ──
    if mode in _CN_GATED and not is_cn_market_open():
        logger.info("A 股今日休市，跳过 %s", title)
        print(f"\n   ⛔ A 股休市，{title} 跳过\n")
        return

    print(f"\n{'='*50}\n   📨 {title}")

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
    # Step 0 的自动入账结果本来就最该被看见，所以摆到标题行正下方，
    # 而不是跟其他 block 一起沉到正文里（那正是这次事故能藏住的原因）。
    receipt = _build_settlement_receipt()
    if receipt:
        card = _inject_receipt_after_title(card, receipt)
        logger.info("已注入结算回执（%d 行）", receipt.count("\n") + 1)

    logger.info("推送到飞书群…")
    _push(title, card)
    print(f"\n   ✅ 推送完成\n{'='*50}")


if __name__ == "__main__":
    main()
