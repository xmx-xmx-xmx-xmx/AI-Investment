# D3 国际 RSS 信息流 —— 设计文档

> 日期：2026-06-22
> 状态：已确认
> 关联：[[TODO.md]] D3 项

## 概述

新建 `src/global_news.py`，接入 3 条英文 RSS 源，通过 LLM 一次性完成持仓匹配+翻译+语义去重，输出 3-6 条中文摘要注入简报「🌐 国际快讯」区块。

核心原则：不堆源、不造噪音。3 条 RSS 覆盖你真正关心的市场，LLM 只输出和你有关系的内容。

---

## 一、信息源（3 条 RSS）

| 源 | URL | 日量 | 覆盖 |
|---|---|---|---|
| Yahoo Finance RSS | `https://finance.yahoo.com/news/rssindex` | ~49 | 美股个股/ETF/板块动态 |
| Reuters Business | `https://news.google.com/rss/search?q=site:reuters.com+business+markets&hl=en` | ~70 | 全球宏观：利率、地缘、欧/美/亚太股指 |
| Semiconductor | `https://news.google.com/rss/search?q=semiconductor+chip+industry&hl=en&gl=US&ceid=US:en` | ~100 | 芯片全链：TSMC/三星/SK海力士/美光/ASML |

**为什么只有 3 条**：
- Reuters Business 覆盖了日经/欧洲/亚洲宏观（不需要单独配 Nikkei/Asia Markets/BBC）
- Semiconductor 直接覆盖你最关心的韩台美半导体赛道
- Yahoo Finance 覆盖中文源不报的美股个股/ETF

---

## 二、处理流水线

```
3 条 RSS (~200 条原始标题+链接)
    ↓
1. 关键词预筛（纯代码，零 token）
   去掉体育/娱乐/纯政治/本地社会新闻
   过滤器匹配: 含至少一个金融关键词
   产出: ~50-60 条
    ↓
2. LLM 一次性处理（~3k-5k token）
   输入:
     - 50-60 个英文标题
     - 你的底仓标的列表
     - 你的雷达标的列表
     - 当天中文快讯标题列表（用于去重）
   输出 JSON:
     [
       {
         "title": "Samsung reports Q2 profit surge on AI chip demand",
         "match_target": "半导体行业(雷达)",
         "cn_summary": "三星Q2利润因AI芯片需求大增，利好存储芯片产业链",
         "skip": false,
         "skip_reason": ""
       },
       {
         "title": "Fed officials signal rate cut delay...",
         "match_target": "美股资产",
         "cn_summary": "",
         "skip": true,
         "skip_reason": "中文快讯#3已覆盖同一事件"
       }
     ]
    ↓
输出: 3-6 条 skip=false 的中文摘要
```

### 预筛关键词列表

```python
_FINANCE_KEYWORDS = [
    "stock", "market", "fed", "rate", "inflation", "cpi", "gdp", "earnings",
    "revenue", "profit", "chip", "semiconductor", "semicon", "tsmc", "samsung",
    "sk hynix", "micron", "asml", "nvidia", "amd", "intel", "apple", "microsoft",
    "google", "amazon", "meta", "tesla", "oil", "gold", "commodity", "bond",
    "treasury", "yield", "dollar", "yen", "euro", "yuan", "ecb", "boj", "pboc",
    "trade", "tariff", "iran", "opec", "energy", "ai", "data center",
    "hbm", "wafer", "foundry", "dram", "nand", "etf", "index",
    "s&p", "nasdaq", "dow", "nikkei", "kospi", "hang seng", "shanghai",
    "economy", "gdp", "pmi", "jobs", "payroll", "bank", "credit", "debt",
]
```

---

## 三、模块接口（`src/global_news.py`）

### 3.1 核心函数

| 函数 | 输入 | 输出 | 说明 |
|------|------|------|------|
| `fetch_rss_feeds()` | 无 | `list[dict]` ~50 条 | 拉取 3 条 RSS → 预筛去重（纯标题前 80 字去重） |
| `match_and_translate(articles, holdings, radar_items, cn_titles)` | 预筛结果 + 持仓 + 雷达 + 中文标题 | `list[dict]` 3-6 条 | LLM 一次调用：匹配 + 翻译 + 去重 |
| `fetch_global_news()` | 无 | `list[dict]` 3-6 条 | 主入口 = fetch_rss_feeds + match_and_translate |

### 3.2 返回结构

```python
{
    "title": "Samsung reports Q2 profit surge...",       # 原始英文标题
    "cn_summary": "三星Q2利润因AI芯片需求大增...",         # 中文摘要（50字以内）
    "match_target": "半导体行业(雷达)",                     # 关联哪个持仓/雷达标的
    "source": "Reuters",                                  # 来源标识
    "url": "https://...",                                 # 原始链接（可选）
}
```

### 3.3 CLI 入口

```bash
python -m src.global_news             # 抓取→匹配→翻译→输出
python -m src.global_news --dry-run   # 只输出不注入简报
```

---

## 四、简报注入

`briefing.py` 新增函数 `_build_global_news()`，在 morning / closing / evening 三时段调用。

无匹配新闻时返回 `"🌐 国际快讯：今日无相关持仓新闻"`，不走 LLM。

有匹配时插入「🌐 国际快讯」区块，格式：

```
🌐 国际快讯

· 三星Q2利润因AI芯片需求大增，HBM产线满载运转
  → 关联: 存储芯片ETF 03076（雷达）

· 伊朗核谈判取得进展，油价回落至$68/桶
  → 关联: 避险商品

· 日本央行暗示7月加息，日元升值至1美元兑143日元
  日经出口型企业短期承压
```

LLM 每日仅调用 1 次（`match_and_translate`），总 token 预算 ~3k-5k。无匹配时不走 LLM（纯代码预筛就能判断）。

---

## 五、变更文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/global_news.py` | 新建 | RSS 抓取 + LLM 匹配/翻译/去重 |
| `tests/test_global_news.py` | 新建 | 单元测试 |
| `src/briefing.py` | 修改 | `_build_morning` / `_build_closing` / `_build_evening` 调用 `fetch_global_news` |
| `pyproject.toml` | 修改 | 依赖加 `feedparser>=6.0` |
| `TODO.md` | 修改 | D3 标记完成 |

---

## 六、不在范围

- 不做全文翻译（只做标题+1句摘要）
- 不做实时推送（只在简报时段内产出）
- 不做历史存档（RSS 新闻不存飞书表）
- 不做 Tavily 额外搜索（RSS 已覆盖）
- 不做非英文 RSS（日经/韩语源不做，Semiconductor + Reuters 已覆盖日韩台话题）

---

## 七、Token 熔断

- 单次 LLM 调用 `max_tokens=1200`（约 3k-5k 总 token 含 prompt）
- 若 LLM 不可用（API key 缺失或调用失败），优雅降级：仅输出无匹配提示
- 若 RSS 全部拉取失败，不报错阻断，直接返回空列表
