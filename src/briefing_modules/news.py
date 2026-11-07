"""News presentation, distillation, translation, and output sanitation."""

from __future__ import annotations

import logging
import re

from src.news_fetcher import _clean_html

logger = logging.getLogger(__name__)

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


def _news_link_block(articles: list[dict]) -> str:
    """规则 6：资讯·持仓关联（docs/ACTION_RULES.md #6）。

    吃**全量**快讯而非展示层精选 filtered（与"展示要短、判定要全"同源）；
    返回空串 = 无命中/本地模式/异常（news_mapper 内 fail-silent）。
    """
    from src.news_mapper import build_news_links_block
    block = build_news_links_block(articles)
    return f"\n{block}\n" if block else ""


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
    from src import briefing
    translate_fn = getattr(briefing, "_translate_english_titles", _translate_english_titles)
    translate_fn(items)

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


def _distill_news_line(raw: str, max_len: int = 38) -> str:
    """将单条快讯提炼为一句话纯中文要点（剥电讯套话、取【标题】或首句、控字数）。"""
    t = _clean_html(raw or "").strip()
    if not t:
        return ""
    t = re.sub(r"^[·•\-*]+\s*", "", t)
    t = re.sub(r"^\[译\]\s*", "", t)
    # 剥末尾英文原文括号，如 （Fed's Goolsbee says ...）
    t = re.sub(r"[（(][^）)\n]*[A-Za-z]{3,}[^）)\n]*[)）]\s*$", "", t).strip()
    # 剥末尾短媒体出处括号，如 （路透）
    t = re.sub(r"[（(][^）)\n]{1,12}[)）]\s*$", "", t).strip()

    # 金十等电讯常见【编辑提炼的一句话标题】+ 长导语：优先直接取【】内标题
    m = re.match(r"^【([^】]{4,80})】", t)
    if m:
        t = m.group(1).strip()
    else:
        t = re.sub(
            r"^(?:金十数据|财联社|华尔街见闻|格隆汇|智通财经|新浪财经)"
            r"(?:\d{1,2}月\d{1,2}日)?(?:讯|电)?[，,：:\s]*",
            "",
            t,
        )
        sm = re.match(r"^(.+?)[。！？；]", t)
        if sm:
            t = sm.group(1).strip()

    t = t.rstrip("。；，, ")
    if len(t) > max_len:
        t = t[: max_len - 1].rstrip("，,、：: ") + "…"
    return t


def _build_digest_news_block(
    cn_articles: list[dict],
    global_brief: str = "",
    max_bullets: int = 4,
    max_len: int = 38,
) -> str:
    """合并国内快讯与国际 RSS 快讯为统一「核心要闻」块（3-4 条一句话要点）。

    ⚠️ 严格遵循「展示要短，喂 AI 要全」分层原则：
       本函数仅服务卡片展示层（不改动入参列表）；喂给 `_ai_insight()` 与
       `info_fingerprint` 的 `titles_only` / `global_for_ai` 仍使用全量数据。
    """
    from src.news_fetcher import curate_for_display, same_event

    curated_cn = [
        dict(a) for a in curate_for_display(cn_articles, max_items=max(max_bullets * 2, 6))
    ]
    global_lines: list[str] = []
    if global_brief:
        for line in global_brief.splitlines():
            s = line.strip()
            if s.startswith("·"):
                cleaned = s.lstrip("·").strip()
                if cleaned:
                    global_lines.append(cleaned)

    if global_lines:
        global_quota = min(len(global_lines), 2 if max_bullets >= 4 else 1)
    else:
        global_quota = 0
    cn_quota = max(0, max_bullets - global_quota)

    if curated_cn:
        from src import briefing
        translate_fn = getattr(briefing, "_translate_english_titles", _translate_english_titles)
        translate_fn(curated_cn)

    bullets: list[str] = []
    seen_raw: list[str] = []

    def _try_add(raw_text: str) -> None:
        if len(bullets) >= max_bullets or not raw_text:
            return
        if any(same_event(raw_text, prev) for prev in seen_raw):
            return
        line = _distill_news_line(raw_text, max_len=max_len)
        if not line:
            return
        seen_raw.append(raw_text)
        bullets.append(line)

    for a in curated_cn[:cn_quota]:
        _try_add(a.get("title", ""))

    for g in global_lines:
        _try_add(g)

    # 若国际快讯与国内重事件导致未满额，用剩余国内条目回填（不超过 max_bullets）
    if len(bullets) < max_bullets:
        for a in curated_cn[cn_quota:]:
            _try_add(a.get("title", ""))

    if not bullets:
        return "（暂无）"
    return "\n".join(f"· {b}" for b in bullets)


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
        from src.llm import chat_completion_with_fallback
        joined = "\n".join(f"[{j+1}] {t}" for j, t in enumerate(texts))
        prompt = (
            "将以下英文新闻标题翻译为简洁的中文（20-40字），保留编号格式 [N] 中文：\n"
            + joined
        )
        translated, _ = chat_completion_with_fallback(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.1,
            thinking=False,
        )
        # 解析 [N] 中文格式
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


def _truncate_at_sentence_boundary(text: str, min_len: int = 20) -> str:
    """如果文本疑似被 max_tokens 硬截断（末尾无句号/问号/感叹号），
    回退到最近一个完整句子收尾，避免半句话。
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
