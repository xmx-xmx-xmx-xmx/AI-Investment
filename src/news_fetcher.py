"""
资讯抓取引擎 —— 免费优先，AI 增强备选。

数据源优先级：
  1. 金十数据（免费快讯，无需 Key）
  2. 华尔街见闻（免费快讯，无需 Key）
  3. Tavily（AI 增强搜索，每月 1000 次，有 Key 时启用）
  4. SearXNG 公共实例（兜底，无需 Key）

用法：
    from src.news_fetcher import fetch_all_news
    articles = fetch_all_news()
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
                "title": display[:200],
                "snippet": content[:300],
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
                "title": text[:200],
                "snippet": text[:300],
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
                 "标普", "纳指", "美债", "美元", "Apple", "NVIDIA", "英伟达", "微软",
                 "半导体", "芯片", "费城半导体", "SOX", "存储", "AI", "人工智能",
                 "美光", "Micron", "MU", "台积电", "TSMC", "ASML", "AMD", "Intel",
                 "HBM", "DRAM", "NAND", "晶圆", "代工", "封装"],
    "A股资产":  ["A股", "沪深300", "上证指数", "深证", "央行", "降息", "降准", "MLF", "LPR",
                 "人民币", "证监会", "沪深", "A股", "创业板", "科创板",
                 "MLCC", "电容", "氟化工", "新能源", "光伏", "风电", "锂电", "电池",
                 "稀土", "永磁", "碳纤维", "新材料"],
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


# 注：fetch_portfolio_news() / build_queries() 已于 2026-07-07 删除（死代码，全项目无调用者）。
# news_fetcher.py 仅保留 fetch_all_news() + _filter_by_keywords() + 各源抓取函数，
# 供 briefing.py 和 global_news.py 使用。

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


# ═══════════════════════════════════════════════════════════════
# 展示层策展：跨源同一事件去重 + 纯行情行剥离（2026-09-23）
#
# 背景：要闻是多源抓取（金十数据 + 华尔街见闻），而 fetch_all_news 的去重只是
#   `title[:60]` 的**字面精确匹配** → 同一事件被不同措辞的各家快讯各留一条。
#   实测（9/21–9/23 的 11 张真实卡片）：每张要闻块 1/4–1/3 的条目是同一事件的
#   第二个来源，单项最多重复 4 次（美联储古尔斯比同一场讲话）。
#   同时「纳指期货: 31,073.05 🔺+0.14% 14:33:55」这类纯报价行也混进了要闻。
#
# ⚠️ 分层原则（与板块/宏观/思维链三处改造一致）：
#   本函数只用于**展示层**。喂给 LLM 的 titles_only 仍用全量，不在这里做去重，
#   避免模型因"看不见重复"而误判信息强度。
# ═══════════════════════════════════════════════════════════════

# 源站套话（各家快讯都会带，会把不相关标题算成"相似"）
_SOURCE_BOILER_RE = re.compile(
    r"[（(]?\s*(?:金十数据|华尔街见闻|财联社|新浪财经|界面新闻|第一财经|证券时报|"
    r"每日经济新闻|澎湃新闻|中国证券报|上海证券报|路透社|彭博社|格隆汇|智通财经)"
    r"\s*\d{0,4}\s*[月年]?\s*\d{0,2}\s*日?\s*[讯电]?\s*[)）]?"
)

# 纯报价行：`名称: 数字 … 时刻`（时刻是判定关键——真新闻不会以 HH:MM 结尾）
_QUOTE_LINE_RE = re.compile(
    r"^[\u4e00-\u9fa5A-Za-z]{2,12}(?:期货|指数|现货)?\s*[:：]\s*"
    r"[\d,]{1,12}\.?\d*\s*[🔺🔻➖]?\s*[+\-]?\d*\.?\d*\s*%?"
    r"[^\u4e00-\u9fa5]{0,12}\d{1,2}:\d{2}(?::\d{2})?\s*$"
)

# 两个条目"算同一事件"的门槛：最长公共连续片段 ≥ 6 字（不含套话）
_DEDUP_MIN_COMMON = 6
_DEDUP_MIN_LEN = 12


def normalize_for_dedup(title: str) -> str:
    """归一化：去翻译标记 / 括号 / 源站套话 / 空白，便于比较。"""
    t = re.sub(r"^\s*\[译\]\s*", "", title or "")
    t = re.sub(r"[\s\u3000]+", "", t)
    t = re.sub(r"[【】\[\]（）()《》<>「」『』]", "", t)
    t = _SOURCE_BOILER_RE.sub("", t)
    return t.strip()


def is_quote_line(title: str) -> bool:
    """是否纯报价行（如 `纳指期货: 31,073.05　🔺+0.14% 14:33:55`）。

    这类内容属于行情，读者已在「📊 全球市场」看过，不该占要闻的位置。
    """
    return bool(_QUOTE_LINE_RE.match(normalize_for_dedup(title)))


def _cmp_text(title: str) -> str:
    """判重专用文本：在 normalize 基础上再去掉标点与**数字**。

    ⚠️ 必须去数字：实测「上证指数早盘收报3938.08点，跌0.36%。 深证成指…」
    与「A股午评…沪指收跌0.36%，深证成指收跌0.58%…」会把
    `跌0.36%` + `深证成指` 连成 `跌036深证成指`（8 字）→ 撞过 6 字门槛。
    那是纯粹的**数字对齐巧合**，不是"同一事件"的证据，会误合不相关的条目。
    去掉数字后该对降到 2 字（"早盘"）→ 正确地不合并。
    """
    return re.sub(r"[^\u4e00-\u9fa5A-Za-z]", "", normalize_for_dedup(title))


def _longest_common_run(a: str, b: str) -> int:
    """两串的最长公共连续片段长度（difflib 是 C 实现，够快）。"""
    if not a or not b:
        return 0
    import difflib
    m = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return m.find_longest_match(0, len(a), 0, len(b)).size


def same_event(a: str, b: str) -> bool:
    """两条标题是否在讲同一件事。

    判据 = 归一化（去源站套话 / 标点 / 数字）后存在 ≥ _DEDUP_MIN_COMMON 字的公共连续片段。
    - 命中正例：`美联储古尔斯比：…` ×3 / `微软…Copilot…超级应用` ×3 /
      `伊朗议会副议长` ×3 / `荣耀方飞…下一代AI手机` ×2 / `马克龙…霍尔木兹海峡` ×2
    - 不误伤：`银河证券：产业韧性…` vs `摩根大通：韩国利率…`
      （公共片段仅"金十数据"套话，已剥离；去数字后不再撞车）
    - 已知漏网：`上证指数早盘收报…` vs `【A股午评：…】`（同一件事，但一个裸报数据、
      一个带分析，措辞几乎无重叠）—— **宁可漏合，不可错合**
    """
    na, nb = _cmp_text(a), _cmp_text(b)
    if len(na) < _DEDUP_MIN_LEN or len(nb) < _DEDUP_MIN_LEN:
        return False
    return _longest_common_run(na, nb) >= _DEDUP_MIN_COMMON


def curate_for_display(news_list: list[dict], max_items: int = 8) -> list[dict]:
    """展示层策展：剥掉纯报价行 → 合并同一事件的多源条目（保留信息最全的那条）。

    注意：**不补位**。去重后剩下几条就是几条 —— 用户要的是"不再同一件事说三遍"，
    而不是"用新条目把省下的位置填满"。
    """
    items = [a for a in news_list[:max_items] if not is_quote_line(a.get("title", ""))]
    if len(items) <= 1:
        return items

    kept: list[dict] = []
    for a in items:
        title = a.get("title", "")
        merged_into = None
        for idx, k in enumerate(kept):
            if same_event(title, k.get("title", "")):
                merged_into = idx
                break
        if merged_into is None:
            kept.append(a)
        else:
            # 同一事件：保留字面更长的那条（信息通常更全）
            if len(title) > len(kept[merged_into].get("title", "")):
                kept[merged_into] = a
    dropped = len(items) - len(kept)
    if dropped:
        logger.info("[展示层策展] 要闻 %d → %d 条（去重 %d 条）", len(items), len(kept), dropped)
    return kept
