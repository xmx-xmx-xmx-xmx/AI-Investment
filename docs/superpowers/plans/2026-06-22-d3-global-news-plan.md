# D3 国际 RSS 信息流 —— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建 `src/global_news.py`，接入 3 条英文 RSS 源，LLM 一次性完成持仓匹配+翻译+语义去重，输出 3-6 条中文摘要注入简报「🌐 国际快讯」区块。

**Architecture:** `global_news.py` 纯计算模块——拉 RSS→预筛→LLM 匹配翻译去重→返回结构化结果。`briefing.py` 在 morning/closing/evening 三时段调用 `fetch_global_news()` 并注入独立区块。

**Tech Stack:** Python 3.11+, feedparser, openai (SiliconFlow), pytest + monkeypatch

## Global Constraints

- feedparser 已存在于 pyproject.toml（`feedparser>=6.0.12`），不新增依赖
- 不动 `price_updater.py` / `strategy.py` / `market_data.py`
- 遵循现有代码风格：Optional/dict 返回、try/except、logger 日志级别
- Use `uv run pytest` for running tests
- RSS 全部拉取失败时不阻断简报，优雅降级
- LLM 不可用时优雅降级（返回空列表）
- 单次 LLM 调用 `max_tokens=1200`，总预算 ~3k-5k token/天

---

### Task 1: 创建测试文件骨架 + `fetch_rss_feeds` 测试

**Files:**
- Create: `tests/test_global_news.py`

**Interfaces:**
- Produces: `test_fetch_rss_empty_client` 等测试（依赖 Task 2 实现后通过）

- [ ] **Step 1: 写入测试文件**

```python
# -*- coding: utf-8 -*-
"""global_news.py 单元测试 —— D3 国际 RSS 信息流"""

from __future__ import annotations

import pytest


# ═══════════════════════════════════════════════════════════════
# RSS 抓取 + 预筛
# ═══════════════════════════════════════════════════════════════

class TestFetchRssFeeds:
    """fetch_rss_feeds() RSS 拉取 + 关键词预筛"""

    def test_all_feeds_fail_returns_empty(self, monkeypatch):
        """3 条 RSS 全部失败 → 返回空列表"""
        import feedparser

        def mock_parse_empty(url):
            return type("obj", (), {"entries": [], "status": 500})()

        monkeypatch.setattr(feedparser, "parse", mock_parse_empty)

        from src.global_news import fetch_rss_feeds
        result = fetch_rss_feeds()
        assert result == []

    def test_feeds_return_filtered_articles(self, monkeypatch):
        """RSS 返回混合内容 → 预筛保留财经相关"""
        import feedparser

        class FakeEntry:
            def __init__(self, title, link, published=""):
                self.title = title
                self.link = link
                self.published = published

        def mock_parse(url):
            entries = [
                FakeEntry("Fed signals rate cut delay amid inflation concerns",
                          "http://reuters.com/1", "2026-06-22T10:00:00Z"),
                FakeEntry("Local football team wins championship",
                          "http://sports.com/1", "2026-06-22T09:00:00Z"),
                FakeEntry("TSMC reports record Q2 revenue on AI chip boom",
                          "http://yahoo.com/1", "2026-06-22T08:00:00Z"),
                FakeEntry("Celebrity wedding draws massive crowds",
                          "http://entertainment.com/1", "2026-06-22T07:00:00Z"),
            ]
            return type("obj", (), {"entries": entries, "status": 200})()

        monkeypatch.setattr("feedparser.parse", mock_parse)

        from src.global_news import fetch_rss_feeds
        result = fetch_rss_feeds()
        # 应该只保留财经相关的 2 条
        assert len(result) == 2
        titles = [r["title"] for r in result]
        assert any("Fed" in t for t in titles)
        assert any("TSMC" in t for t in titles)
        # 体育和娱乐被去掉
        assert not any("football" in t.lower() for t in titles)
        assert not any("wedding" in t.lower() for t in titles)

    def test_dedup_by_title_prefix(self, monkeypatch):
        """相同标题前缀 → 去重只保留首次出现的"""
        import feedparser

        class FakeEntry:
            def __init__(self, title, link, published="", source="Reuters"):
                self.title = title
                self.link = link
                self.published = published
                self.source = source

        entries1 = [FakeEntry("Fed signals rate cut delay amid inflation concerns",
                             "http://a.com/1", "2026-06-22T10:00:00Z")]
        entries2 = [FakeEntry("Fed signals rate cut delay amid inflation concerns - Reuters",
                             "http://b.com/1", "2026-06-22T10:05:00Z")]
        entries3 = [FakeEntry("Oil prices drop on Iran deal hopes",
                             "http://c.com/1", "2026-06-22T09:00:00Z")]

        call_count = [0]

        def mock_parse(url):
            call_count[0] += 1
            if call_count[0] == 1:
                return type("obj", (), {"entries": entries1, "status": 200})()
            elif call_count[0] == 2:
                return type("obj", (), {"entries": entries2, "status": 200})()
            else:
                return type("obj", (), {"entries": entries3, "status": 200})()

        monkeypatch.setattr("feedparser.parse", mock_parse)

        from src.global_news import fetch_rss_feeds
        result = fetch_rss_feeds()
        # Fed 只在第一次出现时保留，第二次被去重
        assert len(result) == 2
        fed_articles = [r for r in result if "Fed" in r["title"]]
        assert len(fed_articles) == 1
```  ```

- [ ] **Step 2: 运行确认失败**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_global_news.py::TestFetchRssFeeds::test_all_feeds_fail_returns_empty -v
```

预期：`FAILED` — `ModuleNotFoundError: No module named 'src.global_news'`

- [ ] **Step 3: 提交**

```bash
git add tests/test_global_news.py
git commit -m "test: add global_news RSS fetch + filter tests (expect fail until Task 2)"
```

---

### Task 2: 实现 `fetch_rss_feeds()` — RSS 拉取 + 预筛

**Files:**
- Create: `src/global_news.py`（模块骨架 + `fetch_rss_feeds` 函数）
- Test: `tests/test_global_news.py`（Task 1 已创建，3 个测试应该全 PASS）

**Interfaces:**
- Produces:
  - `RSS_FEEDS: list[dict]` — 3 条 RSS 源配置
  - `_FINANCE_KEYWORDS: set[str]` — 预筛关键词
  - `_is_finance_article(title: str) -> bool` — 判断是否财经新闻
  - `fetch_rss_feeds() -> list[dict]` — 返回 `[{"title", "link", "published", "source"}, ...]`

- [ ] **Step 1: 创建 `src/global_news.py`**

```python
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
                logger.warning("[%s] 无内容 (status=%s)", feed["name"], f.get("status", "?"))
                continue

            count = 0
            for entry in f.entries:
                title = (entry.get("title") or "").strip()
                if not title or len(title) < 10:
                    continue
                if not _is_finance_article(title):
                    continue

                # 按标题前 80 字去重
                key = title[:80].lower()
                if key in seen:
                    continue
                seen.add(key)

                published = entry.get("published") or entry.get("updated") or ""
                link = entry.get("link") or ""

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
```

- [ ] **Step 2: 运行测试**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_global_news.py::TestFetchRssFeeds -v
```

预期：3 个测试全部 PASS

- [ ] **Step 3: 提交**

```bash
git add src/global_news.py tests/test_global_news.py
git commit -m "feat: add global_news RSS fetch + keyword pre-filter"
```

---

### Task 3: 实现 LLM 匹配+翻译+去重

**Files:**
- Modify: `src/global_news.py`（追加 `match_and_translate` 函数）
- Test: `tests/test_global_news.py`（追加测试类）

**Interfaces:**
- Consumes: `llm.get_llm_client()` / `llm.get_llm_model()`、`advisor.load_portfolio()`（从飞书读持仓）
- Produces: `match_and_translate(articles, holdings, radar_items, cn_titles) -> list[dict]` — 返回 `[{"title", "cn_summary", "match_target", "source", "url"}, ...]`

- [ ] **Step 1: 先追加 LLM 匹配测试**

在 `tests/test_global_news.py` 末尾追加：

```python

# ═══════════════════════════════════════════════════════════════
# LLM 匹配 + 翻译 + 去重
# ═══════════════════════════════════════════════════════════════

class TestMatchAndTranslate:
    """match_and_translate() LLM 匹配翻译去重"""

    def test_empty_articles_returns_empty(self, monkeypatch):
        """无预筛文章 → 空列表，不调 LLM"""
        from src.global_news import match_and_translate
        result = match_and_translate([], [], [], [])
        assert result == []

    def test_no_holdings_or_radar(self, monkeypatch):
        """无持仓无雷达 → 空列表（没有匹配对象）"""
        from src.global_news import match_and_translate
        articles = [{"title": "Some stock news", "link": "", "source": "Reuters"}]
        result = match_and_translate(articles, [], [], [])
        assert result == []

    def test_llm_returns_valid_result(self, monkeypatch):
        """LLM 正常返回 → 解析为结构化列表"""
        articles = [
            {"title": "TSMC reports record Q2 revenue on AI chip boom",
             "link": "http://reuters.com/1", "source": "Reuters"},
            {"title": "Fed signals rate cut delay",
             "link": "http://reuters.com/2", "source": "Reuters"},
            {"title": "Oil prices drop on Iran deal hopes",
             "link": "http://reuters.com/3", "source": "Reuters"},
        ]
        holdings = [{"name": "纳指100ETF", "code": "019442", "asset_class": "美股资产"}]
        radar_items = [{"name": "国投瑞银新能源", "code": "007690"}]
        cn_titles = [
            "美联储官员暗示降息时间表可能推迟",
            "伊朗核谈判进展中，油价回落至68美元/桶",
        ]

        # Mock LLM 返回 JSON
        mock_response = """[
  {"title_idx": 0, "match_target": "半导体行业(雷达)", "cn_summary": "台积电Q2营收创新高，AI芯片需求持续强劲", "skip": false, "skip_reason": ""},
  {"title_idx": 1, "match_target": "美股资产", "cn_summary": "", "skip": true, "skip_reason": "中文快讯#1已覆盖同一事件"},
  {"title_idx": 2, "match_target": "避险商品", "cn_summary": "伊朗核谈判取得进展，油价走低至68美元", "skip": false, "skip_reason": ""}
]"""

        class FakeResp:
            pass

        fake_choice = FakeResp()
        fake_choice.message = FakeResp()
        fake_choice.message.content = mock_response

        fake_client = FakeResp()
        fake_client.chat = FakeResp()
        fake_client.chat.completions = FakeResp()
        fake_client.chat.completions.create = lambda model, max_tokens, temperature, messages: type(
            "obj", (), {"choices": [fake_choice]}
        )()

        monkeypatch.setattr("src.llm.get_llm_client", lambda: fake_client)
        monkeypatch.setattr("src.llm.get_llm_model", lambda: "test-model")

        from src.global_news import match_and_translate
        result = match_and_translate(articles, holdings, radar_items, cn_titles)

        # 应该保留 skip=false 的 2 条
        assert len(result) == 2
        assert result[0]["cn_summary"] != ""
        assert result[0]["title"] == "TSMC reports record Q2 revenue on AI chip boom"
        assert result[1]["title"] == "Oil prices drop on Iran deal hopes"

    def test_llm_unavailable_returns_empty(self, monkeypatch):
        """LLM 不可用 → 返回空列表"""
        monkeypatch.setattr("src.llm.get_llm_client", lambda: None)

        from src.global_news import match_and_translate
        articles = [{"title": "Some stock news", "link": "", "source": "Reuters"}]
        result = match_and_translate(articles, [{"name": "test"}], [], [])
        assert result == []

    def test_llm_returns_malformed_json(self, monkeypatch):
        """LLM 返回非标准 JSON → 优雅降级，返回空列表"""
        class FakeResp:
            pass

        fake_choice = FakeResp()
        fake_choice.message = FakeResp()
        fake_choice.message.content = "some garbage not json"

        fake_client = FakeResp()
        fake_client.chat = FakeResp()
        fake_client.chat.completions = FakeResp()
        fake_client.chat.completions.create = lambda model, max_tokens, temperature, messages: type(
            "obj", (), {"choices": [fake_choice]}
        )()

        monkeypatch.setattr("src.llm.get_llm_client", lambda: fake_client)
        monkeypatch.setattr("src.llm.get_llm_model", lambda: "test-model")

        from src.global_news import match_and_translate
        articles = [{"title": "Some news", "link": "", "source": "Reuters"}]
        result = match_and_translate(articles, [{"name": "test"}], [], [])
        assert result == []
```

- [ ] **Step 2: 运行确认失败（`match_and_translate` 尚未实现）**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_global_news.py::TestMatchAndTranslate::test_llm_returns_valid_result -v
```

预期：`FAILED` — `ImportError`

- [ ] **Step 3: 实现 `match_and_translate`**

在 `src/global_news.py` 的 `fetch_rss_feeds()` 之后追加：

```python

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

    # 构建待匹配文章列表
    article_lines = []
    for i, a in enumerate(articles):
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
            max_tokens=1200,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content.strip()

        # 尝试解析 JSON
        # LLM 有时返回带 ```json``` 包裹的内容
        if raw.startswith("```"):
            import re
            match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
            if match:
                raw = match.group(1).strip()

        import json as _json
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
```

- [ ] **Step 4: 运行全部测试**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_global_news.py -v
```

预期：8 个测试全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/global_news.py tests/test_global_news.py
git commit -m "feat: add LLM match+translate+dedup for global news"
```

---

### Task 4: 实现 `fetch_global_news()` 主入口 + CLI

**Files:**
- Modify: `src/global_news.py`（追加主入口 + main）
- Test: `tests/test_global_news.py`（追加集成测试）

**Interfaces:**
- Produces: `fetch_global_news() -> list[dict]` — 完整流水线入口

- [ ] **Step 1: 追加集成测试**

在 `tests/test_global_news.py` 末尾追加：

```python

class TestFetchGlobalNews:
    """fetch_global_news() 完整流水线"""

    def test_full_pipeline_no_articles(self, monkeypatch):
        """RSS 全部失败 → 空列表"""
        def mock_fetch_feeds():
            return []

        monkeypatch.setattr("src.global_news.fetch_rss_feeds", mock_fetch_feeds)

        from src.global_news import fetch_global_news
        result = fetch_global_news()
        assert result == []

    def test_full_pipeline_with_articles(self, monkeypatch):
        """有预筛结果 → 走 LLM → 产出"""
        def mock_fetch_feeds():
            return [
                {"title": "TSMC record revenue", "link": "http://a.com",
                 "source": "Reuters", "published": "2026-06-22"}
            ]

        def mock_match(articles, holdings, radar_items, cn_titles):
            return [
                {"title": "TSMC record revenue", "cn_summary": "台积电营收创新高",
                 "match_target": "半导体行业(雷达)", "source": "Reuters",
                 "url": "http://a.com"}
            ]

        # mock load_portfolio
        monkeypatch.setattr("src.advisor.load_portfolio",
                            lambda client=None: [{"name": "纳指100", "code": "019442",
                                                   "asset_class": "美股资产"}])
        monkeypatch.setattr("src.global_news.fetch_rss_feeds", mock_fetch_feeds)
        monkeypatch.setattr("src.global_news.match_and_translate", mock_match)

        from src.global_news import fetch_global_news
        result = fetch_global_news()
        assert len(result) == 1
        assert result[0]["cn_summary"] == "台积电营收创新高"
```

- [ ] **Step 2: 实现 `fetch_global_news` + `main`**

在 `src/global_news.py` 末尾追加：

```python

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
```

- [ ] **Step 3: 运行全部测试**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_global_news.py -v
```

预期：10 个测试全部 PASS

- [ ] **Step 4: 提交**

```bash
git add src/global_news.py tests/test_global_news.py
git commit -m "feat: add fetch_global_news() main entry + CLI for global RSS pipeline"
```

---

### Task 5: 简报注入 + 更新 TODO.md

**Files:**
- Modify: `src/briefing.py`（`_build_morning` / `_build_closing` / `_build_evening` 注入国际快讯）
- Modify: `TODO.md`（标记 D3 完成）

- [ ] **Step 1: 在 `_build_morning` 注入国际快讯**

当前 `_build_morning` 的 return 字符串是 L331 附近：
```
{news_block}{macro_block}{radar_block}{market_block}{insight_block}{focus_block}...
```

在 `market_block` 之后、`insight_block` 之前插入 `global_news_block`。

找到 `market_block = ...` 的位置（约 L321），在其后追加：

```python
    # ── 国际 RSS ──
    from src.global_news import _build_global_news_brief
    global_news_block = _build_global_news_brief()
    global_block = f"\n{global_news_block}\n" if global_news_block else ""
```

然后修改 return 字符串，在 `market_block` 后插入 `global_block`：

```python
{news_block}{macro_block}{radar_block}{market_block}{global_block}{insight_block}{focus_block}
```

- [ ] **Step 2: 在 `_build_closing` 注入国际快讯**

找到 `_build_closing` 中的 return 字符串（约 L405）：
```
{news_block}{radar_block}
```

改为：
```
{news_block}{radar_block}{global_block}
```

并在 return 前追加相同的 `from src.global_news import _build_global_news_brief; global_news_block = _build_global_news_brief(); global_block = ...`

- [ ] **Step 3: 在 `_build_evening` 注入国际快讯**

找到 `_build_evening` 中的 return 字符串（约 L463）：
```
{news_block}{radar_block}{insight_block}{focus_block}
```

改为：
```
{news_block}{radar_block}{global_block}{insight_block}{focus_block}
```

并在已有的 `market_block` 之后追加相同的 global_news_block 代码。

- [ ] **Step 4: 运行全部测试确认无回归**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/ -v
```

预期：全部 PASS（~158 个测试）

- [ ] **Step 5: 更新 TODO.md**

将 D3 标记完成：

```markdown
- [x] **D3. 国际 RSS 信息流**
  - [x] `src/global_news.py`：3 条 RSS（Yahoo Finance / Reuters / Semiconductor）
  - [x] 关键词预筛 → LLM 匹配翻译去重 → 简报「🌐 国际快讯」
  - [x] Token 熔断：单次 LLM max_tokens=1200，无匹配时不调 LLM
```

同时在可用命令区追加：

```bash
python -m src.global_news           # 国际 RSS 流水线
python -m src.global_news --dry-run # 只抓不译
```

- [ ] **Step 6: 最终提交**

```bash
git add src/briefing.py TODO.md
git commit -m "feat: inject global news into morning/closing/evening briefings; mark D3 complete"
```

---

### Task 6: 最终验证

- [ ] **Step 1: 运行全部测试**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/ -v
```

- [ ] **Step 2: 手动验证 `--dry-run`**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run python -m src.global_news --dry-run
```

预期：输出预筛文章列表

- [ ] **Step 3: 手动验证完整流水线（⚠️ 会消耗 LLM token）**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run python -m src.global_news
```

预期：输出匹配翻译结果

---

## 变更文件汇总

| 文件 | 操作 | Tasks |
|------|------|-------|
| `src/global_news.py` | 新建 | Task 2, 3, 4 |
| `tests/test_global_news.py` | 新建 | Task 1, 3, 4 |
| `src/briefing.py` | 修改 | Task 5 |
| `TODO.md` | 修改 | Task 5 |
