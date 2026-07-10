# REFACTOR.md —— AI 量化投资系统重构蓝图

> 最后更新：2026-07-07（今晚成果：三层容灾改造 + 剩余 YAML 配置化/千行拆分）

---

## 📋 剩余待办（按优先级排列）

### 🟡 第一优先：YAML 配置化 —— 核心代码依赖替换

> **当前状态**：`config/strategy.yaml` 和 `src/config_loader.py` 已物理存在，但 `src/constants.py`、`src/strategy.py`、`src/market_data.py` 中的硬编码尚未迁移。

| 配置项 | 当前位置 | 需改为 |
|--------|----------|--------|
| `TARGET_WEIGHTS` | `src/constants.py` L13-19 | `get_target_weights()` |
| `THRESHOLD_*`（4个阈值） | `src/strategy.py` L72-76 | `get_thresholds()` |
| `COOLDOWN_DAYS` | `src/strategy.py` L78 | `get_cooldown_days()` |
| `_SIGNAL_META` | `src/strategy.py` L85-94 | `get_signals()` |
| `_RADAR_CLASS_MAP` | `src/strategy.py` L32-38 | `get_radar_class_map()` |
| `CN_ETF_MAP` / `US_ETF_MAP` / `HK_STOCK_MAP` | `src/market_data.py` | `get_etf_maps()` |
| `_FUTURES_NAME_MAP` | `src/market_data.py` L410 | `get_etf_maps()` |

**迁移后效果**：删除 `src/constants.py`，`strategy.py` -40 行，`market_data.py` -60 行。

---

### 🧩 第二优先：千行大文件 "解耦搬家"

#### 拆分后目录树

```
src/
├── briefing/                    # 📁 新包
│   ├── __init__.py
│   ├── _orchestrator.py
│   ├── slots/                   # 7 时段编排（每文件 ~80行）
│   ├── blocks/                  # 纯展示 block（只拼字符串，不调 API）
│   ├── ai/                      # LLM 调用
│   ├── estimation/              # 场外基金估算
│   └── formatting/              # 纯文本工具
```

#### 拆分优先级

| 优先级 | 拆出内容 | 预估行数 | 风险 |
|--------|---------|---------|------|
| 1 | `formatting/` — 纯工具函数 | ~60 行 | 零风险 |
| 2 | `blocks/` — 5 个展示 block | ~200 行 | 低风险 |
| 3 | `ai/` — LLM 调用 | ~80 行 | 低风险 |
| 4 | `estimation/` — 场外基金估算 | ~100 行 | 中风险 |
| 5 | `slots/` — 7 个时段函数 | ~600 行 | 高风险 |

---

## ✅ 已归档（2026-07-07 完成）

### 🔥 紧急容灾改造（GitHub Actions 15 分钟超时修复）

| 层 | 文件 | 改造内容 |
|----|------|---------|
| **基础设施** | `src/timeout_guard.py`（新建）| `with_timeout()` 通用超时装饰器，threading 实现，跨平台零依赖 |
| **基础设施** | `src/llm.py` L28 | OpenAI 客户端增加 `timeout=180, max_retries=1`，单次 LLM 调用不超过 3 分钟 |
| **行情层** | `src/market_data.py` 末尾 | 9 个公开抓取函数统一套超时壳：yfinance 10s / akshare 跨墙 15s / 板块温差 120s |
| **缓存层** | `src/price_updater.py` L271-275 | 抓取超时 → 自动复用飞书底仓「现价」字段作为缓存兜底，确保持仓展示不中断 |
| **源头层** | `src/global_news.py` | RSS 引入 `_HIGH_PRIORITY` / `_MEDIUM_PRIORITY` 关键词评分，100+ 条 → 截断到 30 条；`feedparser` 加 socket 15s 超时；`match_and_translate` 最大文章数 60→30 |
| **LLM 层** | `src/briefing.py` `_build_fallback_insight()`（新建）| LLM 超时 → 自动降级为纯文本脱水摘要（VIX + 板块温差 + 新闻标题），确保飞书通道必达 |

**预期效果**：单次 GitHub Actions 运行从 >15 分钟 → 8-10 分钟。

### 代码清盘

| 动作 | 内容 | 产物 |
|------|------|------|
| 宝藏提取 | `references/legacy_gems/`（4 文件）| fundamental_adapter / yfinance_fundamental / feishu_stream / retry_pattern |
| 旧项目清盘 | 打包删除 `_legacy_backup/` | `legacy_backup_final.tar.gz` (135MB)，394 个文件已清除 |
| 最高宪法 | `CLAUDE.md` + `src/env.py` | 本地开发隔离规范 + 环境判定 |
| 死代码切除 | `src/market_brief.py` 删除 + 3 模块中 6 死函数 | 清理 338 行死代码 |
| 配置文件 | `config/strategy.yaml` + `src/config_loader.py` | YAML 配置 + 单例加载器（文件已建，依赖替换待做）|
