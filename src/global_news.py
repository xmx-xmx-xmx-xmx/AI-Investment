# -*- coding: utf-8 -*-
"""
国际 RSS 信息流 —— D3 核心模块。

接入 3 条英文 RSS 源，LLM 一次性完成持仓匹配+翻译+语义去重，
输出 3-6 条中文摘要注入简报「🌐 国际快讯」区块。

用法：
    python -m src.global_news             # 抓取→匹配→翻译→输出
    python -m src.global_news --dry-run   # 只抓取，不调 LLM
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# RSS 源配置
# ═══════════════════════════════════════════════════════════════

RSS_FEEDS = [
    {
        "name": "Yahoo Finance",
        "url": "https://finance.yahoo.com/news/rssindex",
    },
    {
        "name": "Reuters Business",
        "url": "https://news.google.com/rss/search?q=site:reuters.com+business+markets&hl=en",
    },
    {
        "name": "Semiconductor",
        "url": "https://news.google.com/rss/search?q=semiconductor+chip+industry&hl=en&gl=US&ceid=US:en",
    },
]

# ═══════════════════════════════════════════════════════════════
# 关键词预筛（零 token 成本）
# ═══════════════════════════════════════════════════════════════

_FINANCE_KEYWORDS = {
    "stock", "market", "fed", "rate", "inflation", "cpi", "gdp", "earnings",
    "revenue", "profit", "chip", "semiconductor", "tsmc", "samsung",
    "sk hynix", "micron", "asml", "nvidia", "amd", "intel", "apple", "microsoft",
    "google", "amazon", "meta", "tesla", "oil", "gold", "commodity", "bond",
    "treasury", "yield", "dollar", "yen", "euro", "yuan", "ecb", "boj", "pboc",
    "trade", "tariff", "iran", "opec", "energy", "ai", "data center",
    "hbm", "wafer", "foundry", "dram", "nand", "etf", "index",
    "s&p", "nasdaq", "dow", "nikkei", "kospi", "hang seng", "shanghai",
    "economy", "pmi", "jobs", "payroll", "bank", "credit", "debt",
    "investment", "investor", "fund", "asset", "stocks", "shares",
    "merger", "acquisition", "takeover", "ipo", "spac",
    "bitcoin", "crypto", "ethereum", "blockchain",
    "supply chain", "shortage", "inventory", "output", "production",
    "layoff", "hiring", "labor", "worker", "strike",
}

_NON_FINANCE_KEYWORDS = {
    "sports", "football", "basketball", "soccer", "tennis", "golf",
    "celebrity", "wedding", "movie", "film", "concert", "festival",
    "weather", "hurricane", "earthquake", "recipe", "restaurant",
    "crime", "police", "murder", "assault",
}


def _is_finance_article(title: str) -> bool:
    """判断标题是否属于财经新闻。"""
    lower = title.lower()
    # 先排除明显非财经
    for nf in _NON_FINANCE_KEYWORDS:
        if nf in lower:
            return False
    # 再看是否含财经关键词
    for fk in _FINANCE_KEYWORDS:
        if fk in lower:
            return True
    return False


# ═══════════════════════════════════════════════════════════════
# RSS 抓取 + 预筛
# ═══════════════════════════════════════════════════════════════

def fetch_rss_feeds() -> list[dict]:
    """拉取 3 条英文 RSS 源，预筛合并去重。

    Returns:
        [{"title": "...", "link": "...", "published": "...", "source": "Reuters"}, ...]
        全部失败返回空列表。
    """
    import feedparser

    all_articles = []
    seen = set()

    tz_cn = timezone(timedelta(hours=8))

    for feed in RSS_FEEDS:
        try:
            f = feedparser.parse(feed["url"])
            if not f.entries:
                logger.warning("[%s] 无内容 (status=%s)", feed["name"], getattr(f, "status", "?"))
                continue

            count = 0
            for entry in f.entries:
                title = (getattr(entry, "title", None) or "").strip()
                if not title or len(title) < 10:
                    continue
                if not _is_finance_article(title):
                    continue

                # 按标题前 80 字去重（含前缀匹配，不同源的同一新闻去重）
                key = title[:80].lower()
                skip = False
                for existing in seen:
                    if key == existing or key.startswith(existing) or existing.startswith(key):
                        skip = True
                        break
                if skip:
                    continue
                seen.add(key)

                published = getattr(entry, "published", None) or getattr(entry, "updated", None) or ""
                link = getattr(entry, "link", None) or ""

                all_articles.append({
                    "title": title,
                    "link": link,
                    "published": published,
                    "source": feed["name"],
                })
                count += 1

            logger.info("[%s] %d 条 → 预筛保留 %d 条", feed["name"], len(f.entries), count)
        except Exception as e:
            logger.warning("[%s] 抓取失败: %s", feed["name"], str(e)[:100])

        time.sleep(0.3)

    logger.info("RSS 合计: %d 条（已预筛去重）", len(all_articles))
    return all_articles


# ═══════════════════════════════════════════════════════════════
# LLM 匹配 + 翻译 + 语义去重（一次调用完成）
# ═══════════════════════════════════════════════════════════════

def match_and_translate(
    articles: list[dict],
    holdings: list[dict],
    radar_items: list[dict],
    cn_titles: list[str],
) -> list[dict]:
    """LLM 一次性完成：持仓匹配 + 中文翻译 + 语义去重。

    Args:
        articles: RSS 预筛结果 [{"title", "link", "source", "published"}, ...]
        holdings: 底仓持仓列表 [{"name", "code", "asset_class"}, ...]
        radar_items: 雷达标的信息 [{"name", "code"}, ...]
        cn_titles: 当天中文快讯标题列表（用于语义去重）

    Returns:
        [{"title", "cn_summary", "match_target", "source", "url"}, ...]
        skip=true 的条目被过滤掉，只保留匹配且不重复的。
    """
    if not articles:
        return []
    if not holdings and not radar_items:
        return []

    # 构建持仓/雷达摘要
    hold_lines = []
    for h in holdings:
        hold_lines.append(f"  - {h['name']}({h.get('code','')}) [{h.get('asset_class','')}]")
    radar_lines = []
    for r in radar_items:
        radar_lines.append(f"  - {r['name']}({r.get('code','')})")

    hold_text = "\n".join(hold_lines) if hold_lines else "(无持仓)"
    radar_text = "\n".join(radar_lines) if radar_lines else "(无雷达标的)"
    cn_text = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(cn_titles[:15]))

    # 构建待匹配文章列表（限制数量，避免 token 溢出）
    article_lines = []
    max_articles = 60
    for i, a in enumerate(articles[:max_articles]):
        article_lines.append(f"{i}. [{a.get('source','?')}] {a['title']}")
    article_text = "\n".join(article_lines)

    prompt = f"""<system_role>
你是金融信息筛选助手。下面是今天的英文财经新闻标题（按编号索引），
以及用户的持仓列表和雷达观察列表。

请完成三个任务：
1. 判断哪些新闻和用户的持仓/雷达标的有关（匹配）
2. 对有关的中文输出一句中文摘要（30-50字）
3. 判断是否和已有的中文快讯重复（语义去重）

输出严格 JSON 数组，每条元素格式：
{{"title_idx": 0, "match_target": "关联的持仓或雷达标的名称", "cn_summary": "中文摘要(30-50字)", "skip": true/false, "skip_reason": ""}}

</system_role>

<hard_rules>
- 只输出和持仓/雷达有关的新闻，不相关的不要输出
- skip=true 的情况：①明显不相关 ②和中文快讯说的是同一件事
- skip_reason 在 skip=true 时必须填写（如 "不相关" / "中文快讯#3已覆盖"）
- 持仓未覆盖的领域（如日本央行、欧洲央行）如果出现在新闻中也标注关联为"国际宏观(参考)"
- cn_summary 在 skip=false 时填写，30-50 字中文
- 直接输出 JSON 数组，不要 ```json``` 包裹，不要有任何前缀后缀
</hard_rules>

<holdings>
{hold_text}
</holdings>

<radar>
{radar_text}
</radar>

<candidate_articles>
{article_text}
</candidate_articles>

<chinese_headlines>
{cn_text}
</chinese_headlines>"""

    try:
        from src.llm import get_llm_client, get_llm_model
        client = get_llm_client()
        if client is None:
            logger.warning("[global_news] LLM 不可用，跳过匹配翻译")
            return []

        resp = client.chat.completions.create(
            model=get_llm_model(),
            max_tokens=2000,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content.strip()

        # 尝试解析 JSON
        import json as _json
        import re

        # LLM 有时返回带 ```json``` 包裹的内容
        if raw.startswith("```"):
            match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
            if match:
                raw = match.group(1).strip()

        # 尝试修复被截断的 JSON（找到最后一个完整对象，补上右括号）
        raw = raw.strip()
        if not raw.startswith("["):
            # 可能 LLM 返回了前缀文字，尝试提取 JSON 数组
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                raw = match.group()
            else:
                logger.warning("[global_news] 无法从输出中提取 JSON 数组")
                return []

        # 找到最后一个完整的对象（以 "} 结尾的）
        last_good = raw.rfind('"}')
        if last_good > 0:
            end = raw.find("}", last_good) + 1
            if end > 0 and end < len(raw):
                raw = raw[:end] + "\n]"

        parsed = _json.loads(raw)

        # 过滤 + 组装结果
        results = []
        for item in parsed:
            if item.get("skip"):
                continue
            idx = item.get("title_idx", -1)
            if idx < 0 or idx >= len(articles):
                continue
            article = articles[idx]
            results.append({
                "title": article["title"],
                "cn_summary": item.get("cn_summary", ""),
                "match_target": item.get("match_target", ""),
                "source": article.get("source", ""),
                "url": article.get("link", ""),
            })

        if not results:
            logger.info("[global_news] 匹配翻译完成，无相关新闻")
        else:
            logger.info("[global_news] 匹配翻译完成，产出 %d 条", len(results))
        return results

    except Exception as e:
        logger.warning("[global_news] LLM 匹配翻译失败: %s", str(e)[:120])
        return []


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def fetch_global_news() -> list[dict]:
    """完整流水线：RSS 抓取 → 预筛 → LLM 匹配翻译去重。

    Returns:
        [{"title", "cn_summary", "match_target", "source", "url"}, ...]
    """
    # 1. RSS 抓取 + 预筛
    articles = fetch_rss_feeds()
    if not articles:
        logger.info("[global_news] 无预筛文章，跳过 LLM")
        return []

    # 2. 获取持仓 + 雷达标的
    from src.advisor import load_portfolio

    try:
        holdings = load_portfolio()
    except Exception:
        logger.warning("[global_news] 持仓加载失败")
        holdings = []

    # 雷达标的
    from src.feishu_client import FeishuClient

    try:
        client = FeishuClient()
        radar_records = client.list_records("雷达观测表")
        radar_items = [{"name": r.get("标的名称", ""), "code": r.get("标的代码", "")}
                       for r in radar_records]
    except Exception:
        logger.warning("[global_news] 雷达标的加载失败")
        radar_items = []

    # 3. 获取当天中文快讯标题（用于语义去重）
    from src.news_fetcher import fetch_all_news

    try:
        cn_articles = fetch_all_news(max_results=20)
        cn_titles = [a["title"] for a in cn_articles]
    except Exception:
        logger.warning("[global_news] 中文快讯获取失败")
        cn_titles = []

    # 4. LLM 匹配 + 翻译 + 去重
    return match_and_translate(articles, holdings, radar_items, cn_titles)


def _build_global_news_brief() -> str:
    """构建「🌐 国际快讯」简报文本。不调 LLM——调 LLM 由调用方决定。"""
    result = fetch_global_news()
    if not result:
        return ""

    lines = ["🌐 **国际快讯**"]
    for r in result:
        linked = f" → 关联: {r['match_target']}" if r.get("match_target") else ""
        lines.append(f"\n· {r['cn_summary']}")
        if linked:
            lines.append(f"  {linked}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════

def main():
    from dotenv import load_dotenv
    load_dotenv()

    import argparse
    parser = argparse.ArgumentParser(description="国际 RSS 信息流")
    parser.add_argument("--dry-run", action="store_true", help="只抓RSS不调LLM")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    print()
    print("=" * 56)
    print("   🌐 国际 RSS 信息流")
    if args.dry_run:
        print("   [DRY RUN 模式 —— 只抓不译]")
    print("=" * 56)
    print()

    if args.dry_run:
        articles = fetch_rss_feeds()
        print(f"预筛结果: {len(articles)} 条")
        for a in articles[:10]:
            print(f"  [{a['source']}] {a['title'][:100]}")
        print(f"\n  ... 共 {len(articles)} 条")
    else:
        result = fetch_global_news()
        print(f"国际快讯: {len(result)} 条")
        for r in result:
            print(f"\n  📰 {r['cn_summary']}")
            print(f"     源: {r['source']} | 关联: {r['match_target']}")

    print()
    print("=" * 56)


if __name__ == "__main__":
    main()
