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
