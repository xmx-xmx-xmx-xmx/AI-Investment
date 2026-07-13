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
    {
        "name": "Memory/Chip Prices",
        "url": "https://news.google.com/rss/search?q=dram+memory+chip+nand+hbm+price&hl=en&gl=US&ceid=US:en",
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
    "hbm", "wafer", "foundry", "dram", "nand", "mlcc", "capacitor",
    "fluorochemical", "fluorine", "ptfe", "lithium", "battery",
    "etf", "index",
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
# 🔥 2026-07-07 容灾改造：高频关键词分层评分
#
# 问题：4 个 RSS 源可返回 100-260 条，全部送入 LLM 耗时 >2 分钟
# 方案：
#   1. 一级关键词 → +10 分（直接命中持仓/风控核心标的）
#   2. 二级关键词 → +5 分（与宏观经济/市场情绪强相关）
#   3. 基础分 +1（通过了 _is_finance_article 的）
#   4. 按评分排序 → 截断到 30 条 → 送入 LLM 的前 60→30 条
# 兜底：5 条随机低分文章混入，防止漏掉突发新闻
# ═══════════════════════════════════════════════════════════════

_HIGH_PRIORITY = {
    # ═══ 半导体/芯片（直接影响 SOXX/03486/03121/03076 持仓） ═══
    "semiconductor", "chip", "nvidia", "tsmc", "samsung", "sk hynix",
    "micron", "asml", "amd", "intel", "broadcom", "qualcomm",
    "dram", "nand", "hbm", "wafer", "foundry", "memory chip",
    "flash memory", "storage chip",
    # ═══ 持仓直接关联 ═══
    "apple", "xiaomi", "mi ", "microsoft", "google", "amazon", "meta",
    "tesla", "mlcc", "capacitor", "lithium", "battery",
    # ═══ 宏观/利率（影响固收 50%） ═══
    "federal reserve", "fed rate", "rate cut", "rate hike",
    "inflation", "cpi", "ppi", "jobless", "payroll", "nonfarm",
    "treasury yield", "bond yield", "yield curve",
    # ═══ 中国政策（影响 A 股/港股 20% 权重） ═══
    "china stimulus", "pboc", "mlf", "lpr", "china economy",
    # ═══ 黄金（直接持仓 009505 上海金） ═══
    "gold price", "gold rally", "gold selloff", "gold hit",
    # ═══ 亚洲股市 ═══
    "hong kong", "hang seng", "hsi", "hstech",
    "kospi", "kosdaq", "taiwan semi",
    # ═══ 贸易/制裁 ═══
    "tariff", "trade war", "sanction", "export control", "chip ban",
    # ═══ 新能源/原材料 ═══
    "rare earth", "ev subsidy", "solar", "energy storage",
}

_MEDIUM_PRIORITY = {
    "ai chip", "data center", "server", "gpu",
    "oil", "crude", "opec", "energy",
    "crypto", "bitcoin", "ethereum",
    "dollar index", "dxy", "yen", "yuan", "euro",
    "layoff", "earnings beat", "revenue growth",
    "merger", "acquisition", "takeover", "ipo",
    "etf", "fund flow", "inflow", "outflow",
}


def _score_article(title: str) -> int:
    """对 RSS 标题做快速分层评分。基础分 1（通过财经筛选），
    一级命中 +10，二级命中 +5。总分为 0 的丢弃。"""
    lower = title.lower()
    score = 1  # 基础分

    for kw in _HIGH_PRIORITY:
        if kw in lower:
            score += 10
            break  # 一级命中一次就够了

    for kw in _MEDIUM_PRIORITY:
        if kw in lower:
            score += 5
            break

    return score


# ═══════════════════════════════════════════════════════════════
# RSS 抓取 + 预筛
# ═══════════════════════════════════════════════════════════════

def fetch_rss_feeds() -> list[dict]:
    """拉取 4 条英文 RSS 源，预筛 + 关键词评分 + 去重 + 截断到 30 条。

    评分后将 100+ 条压缩到 30 条，大幅减少下游 LLM 匹配翻译耗时。
    兜底：随机保留 5 条低分文章防止遗漏突发事件。

    Returns:
        [{"title": "...", "link": "...", "published": "...", "source": "Reuters", "score": 15}, ...]
        全部失败返回空列表。
    """
    import feedparser
    import random
    import socket

    # 🔥 2026-07-07：feedparser 底层 urllib 默认无超时，设全局 socket 超时
    socket.setdefaulttimeout(15)

    all_articles = []
    seen = set()

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

                # 🔥 2026-07-07：分层评分
                score = _score_article(title)

                all_articles.append({
                    "title": title,
                    "link": link,
                    "published": published,
                    "source": feed["name"],
                    "score": score,
                })
                count += 1

            logger.info("[%s] %d 条 → 预筛保留 %d 条", feed["name"], len(f.entries), count)
        except Exception as e:
            logger.warning("[%s] 抓取失败: %s", feed["name"], str(e)[:100])

        time.sleep(0.3)

    # 恢复默认（避免影响其他模块）
    socket.setdefaulttimeout(None)

    # 🔥 2026-07-07：按评分排序 + 截断到 30 条 + 5 条低分随机兜底
    total_before = len(all_articles)
    all_articles.sort(key=lambda a: a.get("score", 0), reverse=True)

    max_articles = 30
    if len(all_articles) > max_articles:
        high_scored = [a for a in all_articles if a.get("score", 0) >= 10]
        mid_scored = [a for a in all_articles if 1 <= a.get("score", 0) < 10]
        # 高分全保留 + 中分补满到 25 + 低分随机 5 条兜底
        result = high_scored[:max_articles]
        remaining = max_articles - len(result)
        if remaining > 0 and mid_scored:
            result += mid_scored[:remaining]
        # 兜底：从低分/零散文章中随机抽 5 条
        low_scored = [a for a in all_articles if a.get("score", 0) < 1]
        if low_scored:
            sample_n = min(5, len(low_scored))
            result += random.sample(low_scored, sample_n)
        all_articles = result

    logger.info(
        "RSS 合计: %d 条 → %d 条（评分截断 + 去重）",
        total_before, len(all_articles),
    )
    return all_articles


# ═══════════════════════════════════════════════════════════════
# LLM 匹配 + 翻译 + 语义去重
# ═══════════════════════════════════════════════════════════════

def _parse_llm_json(raw: str) -> list[dict]:
    """安全解析 LLM 返回的 JSON 数组，处理常见畸形。"""
    import json as _json
    import re

    if raw.startswith("```"):
        match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
        if match:
            raw = match.group(1).strip()

    raw = raw.strip()
    if not raw.startswith("["):
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            raw = match.group()
        else:
            return []

    # 修复被截断的 JSON（找到最后一个完整对象）
    last_good = raw.rfind('"}')
    if last_good > 0:
        end = raw.find("}", last_good) + 1
        if 0 < end < len(raw):
            raw = raw[:end] + "\n]"

    try:
        return _json.loads(raw)
    except Exception:
        return []


def _match_one_batch(
    batch_articles: list[dict],
    start_idx: int,
    hold_text: str,
    radar_text: str,
    cn_text: str,
) -> list[dict]:
    """对一批文章（~10 条）做 LLM 匹配翻译。独立函数，供并发调用。"""
    article_lines = []
    local_to_global: dict[int, int] = {}  # prompt 内编号 → articles 原始下标
    for i, a in enumerate(batch_articles):
        global_idx = start_idx + i
        article_lines.append(f"{i}. [{a.get('source','?')}] {a['title']}")
        local_to_global[i] = global_idx

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
{chr(10).join(article_lines)}
</candidate_articles>

<chinese_headlines>
{cn_text}
</chinese_headlines>"""

    from src.llm import get_llm_client, get_llm_model
    client = get_llm_client()
    if client is None:
        raise RuntimeError("LLM 不可用")

    resp = client.chat.completions.create(
        model=get_llm_model(),
        max_tokens=800,  # 10 条匹配只需 800，比原来的 2000 少
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.choices[0].message.content.strip()
    parsed = _parse_llm_json(raw)
    if not parsed:
        raise RuntimeError("无法解析 LLM 返回的 JSON")

    results = []
    for item in parsed:
        if item.get("skip"):
            continue
        local_idx = item.get("title_idx", -1)
        if local_idx < 0 or local_idx >= len(batch_articles):
            continue
        global_idx = local_to_global.get(local_idx)
        if global_idx is None:
            continue
        results.append({
            "global_idx": global_idx,
            "cn_summary": item.get("cn_summary", ""),
            "match_target": item.get("match_target", ""),
        })
    return results


def match_and_translate(
    articles: list[dict],
    holdings: list[dict],
    radar_items: list[dict],
    cn_titles: list[str],
) -> list[dict]:
    """LLM 匹配翻译——并发分批次，每批 ~10 条，总耗时 = 最慢批次。

    🔥 2026-07-13 优化：从「30 条一次性塞进 prompt」改为「3 批 × 10 条并发」。
    原方案 prompt 5000+ token，单次 LLM 耗时 >90s 频繁超时。
    新方案每批 prompt ~2000 token，3 批并发跑，最慢的 ~30s，总计 30s。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not articles:
        return []
    if not holdings and not radar_items:
        return []

    max_articles = 30
    batch_size = 10
    articles = articles[:max_articles]

    # 构建共享上下文
    hold_lines = [f"  - {h['name']}({h.get('code','')}) [{h.get('asset_class','')}]" for h in holdings]
    radar_lines = [f"  - {r['name']}({r.get('code','')})" for r in radar_items]
    hold_text = "\n".join(hold_lines) if hold_lines else "(无持仓)"
    radar_text = "\n".join(radar_lines) if radar_lines else "(无雷达标的)"
    cn_text = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(cn_titles[:15]))

    # 分批并发，收集结果
    # 🔥 2026-07-13：as_completed timeout=50s。任意批次超时不阻塞已完成批次，
    #   TimeoutError 后直接收集已有结果——不致于像外层硬超时那样全军覆没。
    from concurrent.futures import TimeoutError as FutureTimeoutError
    all_results: list[dict] = []
    futures_map: dict = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for batch_start in range(0, len(articles), batch_size):
            batch = articles[batch_start:batch_start + batch_size]
            f = pool.submit(
                _match_one_batch, batch, batch_start,
                hold_text, radar_text, cn_text,
            )
            futures_map[f] = batch_start

        try:
            for f in as_completed(futures_map, timeout=50):
                batch_start = futures_map[f]
                try:
                    batch_results = f.result()
                    if batch_results:
                        all_results.extend(batch_results)
                    logger.info("[global_news] 批次 [%d-%d] 完成: %d 条匹配",
                               batch_start, batch_start + batch_size - 1, len(batch_results or []))
                except Exception as e:
                    logger.warning("[global_news] 批次 [%d-%d] 失败: %s",
                                  batch_start, batch_start + batch_size - 1, str(e)[:100])
        except FutureTimeoutError:
            logger.warning("[global_news] 部分批次 %ds 内未完成，收集已完成结果 (%d 条)",
                          50, len(all_results))

    if not all_results:
        logger.info("[global_news] 匹配翻译完成，无相关新闻")
        return []

    # 按 global_idx 排序 + 组装
    all_results.sort(key=lambda x: x.get("global_idx", 999))
    results = []
    seen_titles = set()
    for r in all_results:
        global_idx = r["global_idx"]
        if global_idx >= len(articles):
            continue
        article = articles[global_idx]
        key = article["title"][:60].lower()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        results.append({
            "title": article["title"],
            "cn_summary": r["cn_summary"],
            "match_target": r["match_target"],
            "source": article.get("source", ""),
            "url": article.get("link", ""),
        })

    logger.info("[global_news] 匹配翻译完成，产出 %d 条", len(results))
    return results


# 🔥 2026-07-13：不再用外层 with_timeout 硬超时
# 原因：其中一个批次卡住会丢弃所有已完成批次的结果。改用 as_completed(timeout=50)，
# Token 超时只丢弃卡住的那批，已完成的结果正常返回。


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
