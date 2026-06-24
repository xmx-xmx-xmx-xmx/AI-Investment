"""
资讯抓取引擎 —— 免费优先，AI 增强备选。

数据源优先级：
  1. 金十数据（免费快讯，无需 Key）
  2. 华尔街见闻（免费快讯，无需 Key）
  3. Tavily（AI 增强搜索，每月 1000 次，有 Key 时启用）
  4. SearXNG 公共实例（兜底，无需 Key）

用法：
    from src.news_fetcher import fetch_portfolio_news
    articles = fetch_portfolio_news(portfolio)
    → [{title, snippet, url, date, source}, ...]
"""

from __future__ import annotations

import logging
import os
import random
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")


# ═══════════════════════════════════════════════════════════════
# 1. 金十数据（免费，无需 Key）
# ═══════════════════════════════════════════════════════════════

def _fetch_jin10_news(max_results: int = 30) -> list[dict]:
    """金十数据快讯——JS 接口免费可用。"""
    try:
        url = "https://www.jin10.com/flash_newest.js"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.jin10.com/"}
        resp = requests.get(url, headers=headers, timeout=10)

        # 格式：var newest = [{...}] 这种 JS 声明
        text = resp.text
        # 提取 JSON 数组
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []

        items = _safe_json_parse(match.group())
        if not isinstance(items, list):
            return []

        results = []
        for item in items[:max_results]:
            data = item.get("data", {})
            content = data.get("content", "")
            title = data.get("title") or ""
            if not content and not title:
                continue

            display = title or content
            tz_cn = timezone(timedelta(hours=8))
            try:
                ts = item.get("time", "")
                if ts:
                    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz_cn)
                else:
                    dt = datetime.now(tz_cn)
            except ValueError:
                dt = datetime.now(tz_cn)

            display = _clean_html(display)
            content = _clean_html(content)
            if not display:
                continue
            results.append({
                "title": display[:120],
                "snippet": content[:200],
                "date": dt.strftime("%Y-%m-%d %H:%M"),
                "source": "金十数据",
            })

        logger.info("[金十数据] %d 条快讯", len(results))
        return results
    except Exception as e:
        logger.warning("[金十数据] 获取失败: %s", str(e)[:100])
        return []


# ═══════════════════════════════════════════════════════════════
# 2. 华尔街见闻（免费，无需 Key）
# ═══════════════════════════════════════════════════════════════

def _fetch_wallstreetcn_news(max_results: int = 30) -> list[dict]:
    """华尔街见闻全球快讯——JSON API 免费。"""
    try:
        url = (
            "https://api-one.wallstcn.com/apiv1/content/lives"
            "?channel=global-channel&client=pc&limit=30&first_page=true"
        )
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        items = data.get("data", {}).get("items", [])
        if not items:
            return []

        # 按时间倒序——最新在最前
        items_sorted = sorted(items, key=lambda x: x.get("display_time", 0), reverse=True)

        results = []
        tz_cn = timezone(timedelta(hours=8))
        for item in items_sorted[:max_results]:
            title = item.get("title") or ""
            content = item.get("content_text") or ""
            text = (title or content or "").strip()
            # 去掉 HTML 标签
            text = re.sub(r"<[^>]+>", "", text)
            if not text or len(text) < 5:
                continue

            ts = item.get("display_time", 0)
            try:
                dt = datetime.fromtimestamp(ts, tz=tz_cn)
                date_str = dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError):
                date_str = ""

            results.append({
                "title": text[:120],
                "snippet": text[:200],
                "url": f"https://wallstreetcn.com/lives/global/{item.get('id', '')}" if item.get("id") else "",
                "date": date_str,
                "source": "华尔街见闻",
            })

        logger.info("[华尔街见闻] %d 条快讯", len(results))
        return results
    except Exception as e:
        logger.warning("[华尔街见闻] 获取失败: %s", str(e)[:100])
        return []


# ═══════════════════════════════════════════════════════════════
# 3. Tavily（有 Key 时启用，AI 增强搜索）
# ═══════════════════════════════════════════════════════════════

def _search_tavily(query: str, max_results: int = 5, days: int = 7) -> list[dict]:
    """Tavily AI 搜索。"""
    try:
        from tavily import TavilyClient
    except ImportError:
        return []

    try:
        client = TavilyClient(api_key=TAVILY_API_KEY)
        resp = client.search(
            query=query,
            search_depth="advanced",
            max_results=max_results,
            include_answer=False,
            include_raw_content=False,
            days=days,
        )
        results = []
        for item in resp.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "snippet": (item.get("content") or "")[:300],
                "url": item.get("url", ""),
                "date": item.get("published_date") or "",
                "source": "tavily",
            })
        logger.info("[Tavily] '%s' → %d 条", query, max(len(results), 0))
        return results
    except Exception as e:
        logger.warning("[Tavily] 失败: %s", str(e)[:120])
        return []


def _clean_html(text: str) -> str:
    """去除 HTML 标签和多余空白。"""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()
# ═══════════════════════════════════════════════════════════════

_KEYWORDS_MAP = {
    "美股资产": ["美股", "纳斯达克", "标普500", "道指", "科技股", "美联储", "华尔街", "CPI", "非农",
                 "标普", "纳指", "美债", "美元", "Apple", "NVIDIA", "英伟达", "微软"],
    "A股资产":  ["A股", "沪深300", "上证指数", "深证", "央行", "降息", "降准", "MLF", "LPR",
                 "人民币", "证监会", "沪深", "A股", "创业板", "科创板"],
    "港股资产": ["港股", "恒生", "南向资金", "中概", "腾讯", "阿里", "美团", "小米", "港交所",
                 "港元", "恒指", "香港"],
    "避险商品": ["黄金", "金价", "大宗商品", "原油", "铜", "银", "贵金属", "资源"],
    "固收资产": ["债券", "债市", "国债", "利率", "央行", "公开市场", "逆回购", "MLF"],
}


def _score_article(article: dict, keywords: set[str]) -> int:
    """算相关度分数——每命中一个关键词 +1 分。"""
    score = 0
    text = f"{article.get('title', '')} {article.get('snippet', '')}".lower()
    for kw in keywords:
        if kw.lower() in text:
            score += 1
    return score


def _filter_by_keywords(articles: list[dict], portfolio: list[dict], top_n: int = 15) -> list[dict]:
    """从快讯中筛选与你持仓相关的，按相关度排序。

    无持仓时返回最近的全部（最多 15 条）。
    """
    if not portfolio or not articles:
        return articles[:top_n]

    # 收集所有相关关键词
    keywords = set()
    for p in portfolio:
        cls = p.get("asset_class", "")
        kws = _KEYWORDS_MAP.get(cls, [])
        keywords.update(kws)
        # 加上标的名字里的关键词（取前 4 个字）
        name = p.get("name", "")
        core = name[:8].replace("ETF", "").replace("联接", "").strip()
        if core:
            keywords.add(core)

    if not keywords:
        return articles[:top_n]

    # 评分 + 排序
    scored = [(a, _score_article(a, keywords)) for a in articles]
    scored.sort(key=lambda x: x[1], reverse=True)

    # 只要得分 > 0 和无得分的最近 5 条
    relevant = [a for a, s in scored if s > 0]
    recent_filler = [a for a, s in scored if s == 0][:5]
    result = (relevant + recent_filler)[:top_n]

    logger.info("关键词过滤: %d → %d 条（命中 %d 条）", len(articles), len(result), len(relevant))
    return result


# ═══════════════════════════════════════════════════════════════
# 5. 主搜索入口
# ═══════════════════════════════════════════════════════════════

# 广告/非新闻关键词（金十、华尔街见闻的推广内容）
_AD_KEYWORDS = [
    "壁纸", "直播", "下载", "活动", "福利", "抽奖", "红包", "签到",
    "推广", "广告", "限时", "优惠", "免费领", "课程", "训练营",
    "日历壁纸", "高清", "粉丝群", "加群", "扫码", "关注有礼",
    "壁纸下载", "复盘直播", "每日打卡",
]

_AD_SOURCES = ["金十数据", "华尔街见闻"]


def _is_ad(article: dict) -> bool:
    """判断是否是非新闻推广内容。"""
    title = article.get("title", "")
    source = article.get("source", "")
    lower = title.lower()

    # 只在已知会推广告的源中检测
    if source not in _AD_SOURCES:
        return False

    for kw in _AD_KEYWORDS:
        if kw in title:
            logger.debug("[%s] 过滤广告: %s", source, title[:60])
            return True
    return False


def fetch_all_news(max_results: int = 40) -> list[dict]:
    """从免费源拉全量市场快讯，合并去重，过滤广告。

    免费源优先（零成本），不调 Tavily。
    """
    all_articles = []
    seen = set()

    for article in _fetch_jin10_news(max_results):
        if _is_ad(article):
            continue
        key = article["title"][:60]
        if key not in seen:
            seen.add(key)
            all_articles.append(article)
    time.sleep(0.3)

    for article in _fetch_wallstreetcn_news(max_results):
        if _is_ad(article):
            continue
        key = article["title"][:60]
        if key not in seen:
            seen.add(key)
            all_articles.append(article)

    logger.info("免费源合计: %d 条不重复快讯", len(all_articles))
    return all_articles


def fetch_portfolio_news(portfolio: list[dict], max_per_query: int = 8, days: int = 7) -> list[dict]:
    """按持仓筛选新闻——免费快讯 + Tavily 深度搜索（可选）。

    默认只用免费源（零成本），Tavily 只在有 Key 且免费源不足 10 条时补一次搜索。
    即使补 Tavily 也是 1 credit/天。
    """
    # 1. 免费快讯（零成本）
    articles = fetch_all_news(max_results=40)

    # 2. 如果免费源太少（比如网络问题），用 Tavily 补一次
    if len(articles) < 10 and TAVILY_API_KEY:
        logger.info("免费源不足（%d 条），Tavily 补一次深度搜索", len(articles))
        query = " ".join(list(set(
            p.get("asset_class", "") for p in portfolio
        ))) + " 行情 新闻"
        tavily_results = _search_tavily(query, max_results=8, days=days)
        for a in tavily_results:
            key = a["title"][:60]
            articles.append(a)

    # 3. 相关度筛选
    return _filter_by_keywords(articles, portfolio, top_n=15)


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _safe_json_parse(text: str):
    """安全解析 JSON，回退到 ast.literal_eval。"""
    try:
        import json as _json
        return _json.loads(text)
    except Exception:
        try:
            import ast
            return ast.literal_eval(text)
        except Exception:
            return None


def build_queries(portfolio: list[dict]) -> list[str]:
    """保留旧接口兼容性——不再分裂搜索词，合并为一条。"""
    class_terms = list(set(p.get("asset_class", "") for p in portfolio if p.get("asset_class")))
    highlight_terms = []
    for p in portfolio:
        if "长期底仓" in p.get("tags", []):
            name = p.get("name", "")
            highlight_terms.append(name[:12] if len(name) > 12 else name)
    merged = " ".join(class_terms + highlight_terms) + " 行情 新闻 最新动态"
    return [merged]
