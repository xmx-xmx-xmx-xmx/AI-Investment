"""LLM narrative insight generation, fallback dehydration, and reasoning sanitation."""

from __future__ import annotations

import logging
import re

from src.briefing_modules.headers import _is_compact_style
from src.briefing_modules.news import _sanitize_llm_output, _truncate_at_sentence_boundary
from src.briefing_modules.sector_blocks import _SECTOR_AI_RULE, _MACRO_AI_RULE
from src.briefing_modules.valuation import _build_portfolio_summary

logger = logging.getLogger(__name__)


def _build_fallback_insight(context: str, news_titles: str) -> str:
    """容灾降级：LLM 超时/不可用时，用纯文本脱水摘要替代 AI 解读。

    不调任何外部 API，直接从 context 和 news_titles 中提取关键数字，
    拼成一个可读的纯数据摘要推给飞书。确保「通道必达」——宁可推少，不能不推。
    """
    market_snippet = ""
    if context:
        match = re.search(r"<market_data>(.*?)</market_data>", context, re.DOTALL)
        if match:
            raw = match.group(1).strip()
            market_snippet = raw[:500]

    cn_headlines = []
    for line in news_titles.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith("·") and len(stripped) > 10:
            cn_headlines.append(stripped[:120])
    headlines_text = "\n".join(f"  · {h}" for h in cn_headlines[:8])

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


def _ai_insight(context: str, news_titles: str, max_tokens: int = 1024,
                macro_context: str = "", fast_mode: bool = False,
                hard_signals: str = "",
                diff_brief: str = "",
                sector_brief: str = "") -> str:
    """LLM 生成持仓+新闻解读（可结合宏观日历）。D9 重构：引入投资宪法+思维链。"""
    if not news_titles.strip():
        return ""

    from src import briefing
    pf_sum_fn = getattr(briefing, "_build_portfolio_summary", _build_portfolio_summary)
    pf_summary = pf_sum_fn()

    # ── fast_mode：轻量 prompt，不经过 build_analysis_prompt ──
    if fast_mode:
        fast_data = news_titles[:400]
        diff_section = f"\n【vs 上次推送的变化】\n{diff_brief}\n" if diff_brief else ""
        sector_section = f"\n{sector_brief}\n{_SECTOR_AI_RULE}\n" if sector_brief else ""
        if _is_compact_style():
            len_rule = "40-70字（1-2句大白话）" if max_tokens <= 220 else "80-120字大白话"
        else:
            len_rule = "100-150字大白话"
        fast_prompt = (
            f"你是量化投资顾问。当前语境：{context[:200]}\n"
            f"行情/信号摘要：{fast_data}\n"
            f"持仓偏离度：{pf_summary[:200]}\n"
            f"{sector_section}"
            f"{diff_section}"
            f"要求：{len_rule}，提1-2个具体持仓大类的影响，"
            f"结尾说一句最值得关注的事。直接输出正文，不要前缀。"
        )
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
                finish_reason = getattr(resp.choices[0], "finish_reason", None)
                if finish_reason == "length":
                    logger.warning("fast_mode DeepSeek 输出被 max_tokens 截断 (%d字)，在句子边界收尾", len(content))
                    content = _truncate_at_sentence_boundary(content)
                if len(content) >= 10:
                    return _sanitize_llm_output(content)
                logger.warning("fast_mode DeepSeek 返回过短 (%d字): %s", len(content), content[:80])
        except Exception as e:
            logger.warning("fast_mode DeepSeek 异常: %s，尝试 Qwen 降级", str(e)[:80])

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
    market_text = news_titles[:1000]
    if sector_brief:
        market_text = f"{market_text}\n\n{sector_brief}"
    if macro_context:
        market_text = f"宏观日历:\n{macro_context[:500]}\n\n新闻:\n{market_text}"

    word_budget = "120-200 字，结论先行，不写套话" if _is_compact_style() else "200-350 字"
    extra_rules = (
        "- 只看新闻标题，推测对持仓大类可能的影响\n"
        "- 如果某条新闻明显利好或利空某类资产，直接说\"这对你的XX持仓是机会/风险，因为...\"\n"
        f"- 用大白话写，禁止术语。{word_budget}\n"
        "- 如果当日有宏观经济日历事件，必须结合该事件分析对持仓的短期影响，标注⚠️波动预警\n"
        "- 如果新闻自相矛盾，指出矛盾并建议\"以不变应万变，按纪律执行\"\n"
        "- 【硬约束】必须尊重【量化系统硬信号】段的判定；如新闻分析与硬信号冲突（如硬信号说\"美股冷却期未过\"，但新闻让你加仓美股），"
        "以硬信号为准，并简明说明\"虽然新闻利好，但冷却期/趋势左侧/已超配 等约束下本周按兵不动\"\n"
        "- 直接输出正文，不要前缀"
    )
    if sector_brief:
        extra_rules = _SECTOR_AI_RULE + "\n" + extra_rules
    if macro_context:
        extra_rules = _MACRO_AI_RULE + "\n" + extra_rules

    from src.prompt_templates import build_analysis_prompt
    prompt = build_analysis_prompt(
        role=f"你是量化投资顾问。任务：把当天的财经新闻与真实持仓对照，给出有洞察力的解读。当前语境：{context}",
        holdings_text=pf_summary,
        market_text=market_text,
        extra_rules=extra_rules,
        diff_text=diff_brief,
    )

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
        finish_reason = getattr(resp.choices[0], "finish_reason", None)
        if finish_reason == "length":
            logger.warning("DeepSeek 输出被 max_tokens 截断 (%d字)，在句子边界收尾", len(content))
            content = _truncate_at_sentence_boundary(content)
        if len(content) >= 10:
            return _sanitize_llm_output(content)
        logger.warning("DeepSeek 返回过短 (%d字): %s", len(content), content[:80])
    except Exception as e:
        logger.warning("主模型 DeepSeek 超时/异常: %s，尝试 Qwen3.6-27B 降级", str(e)[:80])

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
            if len(content) >= 20:
                logger.info("备模型 Qwen3.5-9B 降级解读成功 (%d字)", len(content))
                return "[备模型降级] " + _sanitize_llm_output(content)
            logger.warning("备模型返回过短 (%d字): %r", len(content), content[:80])
    except Exception as e2:
        logger.error("备模型 Qwen3.5-9B 降级失败: %r，降到纯文本摘要", e2)

    return _build_fallback_insight(context, news_titles)
