# REFACTOR.md —— AI 量化投资系统重构蓝图

> 最后更新：2026-07-07（动作一~三已完成）

---

## ✅ 已完成

| 动作 | 内容 | 状态 |
|------|------|------|
| 动作一 | 提取 `references/legacy_gems/` 4 个参考文件 + 打包删除 `_legacy_backup/` | ✅ |
| 动作二 | 创建 `CLAUDE.md` + `src/env.py` | ✅ |
| 动作三 | 删除 `src/market_brief.py` + 标注活跃文件死代码区域 | ✅ |
| 动作四（半步）| 创建 `config/strategy.yaml` + `src/config_loader.py` | ✅ 文件已建 |

---

## 📋 剩余待办

### 🟡 任务二（下半场）：核心代码依赖替换

> ⚠️ **当前状态**：`config/strategy.yaml` 和 `src/config_loader.py` 已物理存在，但 `src/constants.py`、`src/strategy.py`、`src/market_data.py` 中的硬编码尚未迁移。

| 配置项 | 当前位置 | 需改为 |
|--------|----------|--------|
| `TARGET_WEIGHTS` | `src/constants.py` L13-19 | `get_target_weights()` |
| `THRESHOLD_*`（4个阈值） | `src/strategy.py` L72-76 | `get_thresholds()` |
| `COOLDOWN_DAYS` | `src/strategy.py` L78 | `get_cooldown_days()` |
| `_SIGNAL_META` | `src/strategy.py` L85-94 | `get_signals()` |
| `CN_ETF_MAP` / `US_ETF_MAP` / `HK_STOCK_MAP` | `src/market_data.py` | `get_etf_maps()` |
| `_FUTURES_NAME_MAP` | `src/market_data.py` L410 | `get_etf_maps()` |
| `_RADAR_CLASS_MAP` | `src/strategy.py` L32-38 | `get_radar_class_map()` |

迁移后：删除 `src/constants.py`，`strategy.py` 减少约 40 行，`market_data.py` 减少约 60 行。

---

### 🧩 任务四：千行大文件 "解耦搬家" 蓝图

#### 当前 `briefing.py` 解构（1431 行 → 28 个函数）

```
分类统计：
├── 7 个时段编排函数 (_build_{morning,midday,closing,evening,sat_morning,sun_evening})
│   + _build_asia_pacific_market + _build_global_market_snapshot + _build_weekly_return
├── 5 个纯展示 block 函数 (_build_{vix,us_futures,sector_rotation,portfolio_summary,trade_summary})
├── 3 个 LLM/AI 函数 (_ai_insight, _translate_english_titles, _needs_chinese_translation)
├── 5 个纯工具函数 (_push, _fmt_news, _sent_truncate, _should_skip, _skip_msg)
├── 4 个行情估算函数 (_estimate_fund_realtime_pct, _is_fund_pos, _trading_label, _exchange_rate_footnote)
├── 1 个持仓展示 (_portfolio_value_summary)
└── 1 个主入口 (main)
```

#### 拆分后目录树

```
src/
├── briefing/                    # 📁 新包，替代單一 briefing.py
│   ├── __init__.py              # 导出 7 个时段函数 + main()
│   ├── _orchestrator.py         # 原 _push, _should_skip, _skip_msg, main()
│   │
│   ├── slots/                   # 📁 7 时段编排（每个文件一个时段，~80行）
│   │   ├── morning.py           # _build_morning()
│   │   ├── midday.py            # _build_midday()
│   │   ├── closing.py           # _build_closing()
│   │   ├── evening.py           # _build_evening()
│   │   ├── sat_morning.py       # _build_sat_morning()
│   │   ├── sun_evening.py       # _build_sun_evening()
│   │   └── _helpers.py          # _build_asia_pacific_market, _build_global_market_snapshot,
│   │                            # _build_weekly_return (被多个 slot 共享)
│   │
│   ├── blocks/                  # 📁 纯展示 block 构建器（只拼字符串，不调 API）
│   │   ├── vix.py               # _build_vix_block()
│   │   ├── futures.py           # _build_us_futures_block()
│   │   ├── sector_rotation.py   # _build_sector_rotation_block()
│   │   ├── portfolio.py         # _portfolio_value_summary() + _build_portfolio_summary()
│   │   ├── trade.py             # _build_trade_summary()
│   │   └── market_context.py    # 市场基准数据文本拼接（从各 slot 中提取公共逻辑）
│   │
│   ├── ai/                      # 📁 LLM 相关（纯 prompt 构造 + API 调用）
│   │   ├── insight.py           # _ai_insight() — 各时段 AI 综合解读
│   │   └── translate.py         # _translate_english_titles() + _needs_chinese_translation()
│   │
│   ├── estimation/              # 📁 场外基金实时估算
│   │   ├── fund_realtime.py     # _estimate_fund_realtime_pct() + _is_fund_pos()
│   │   └── exchange.py          # _exchange_rate_footnote()
│   │
│   └── formatting/              # 📁 纯文本格式化工具（零外部依赖）
│       ├── news_format.py       # _fmt_news() + _sent_truncate()
│       └── labels.py            # _trading_label()
│
├── market_data.py               # 保持不变（933行，但后续也应拆）
├── strategy.py                  # 减去配置常量后 ~400 行
├── ...                          # 其他模块不变
```

#### import 引用关系图

```
slots/morning.py ───────────────┐
slots/midday.py ────────────────┤
slots/closing.py ───────────────┤
slots/evening.py ───────────────┤
slots/sat_morning.py ───────────┤
slots/sun_evening.py ───────────┤
                                 │
        ┌────────────────────────┤
        │  (每个 slot 按需 import)
        ▼                        ▼
blocks/vix.py          blocks/futures.py       blocks/sector_rotation.py
       │                      │                        │
       │    都用 market_data   │                        │
       ▼                      ▼                        ▼
  src.market_data      src.market_data          src.market_data
  (fetch_vix)          (fetch_nq_futures)       (fetch_sector_deltas)


blocks/portfolio.py ────► src.advisor (load_portfolio, calculate_rebalance)
blocks/trade.py ────────► src.advisor (load_portfolio)
                           ⚠️ 本地模式下这两个 block 必须用 mock 数据！

ai/insight.py ──────────► src.llm (get_llm_client, get_llm_model)
                          src.prompt_templates (build_analysis_prompt)

slots/_helpers.py ──────► src.market_data (fetch_us_index, fetch_us_etf, fetch_vix)
                          src.holiday_gate

estimation/fund_realtime.py ──► src.market_data
estimation/exchange.py ───────► src.market_data

formatting/news_format.py ──► src.news_fetcher (fetch_all_news 等)
```

#### 拆分优先级

| 优先级 | 拆出内容 | 预估行数 | 风险 |
|--------|---------|---------|------|
| 1 | `formatting/` — 纯工具函数 | ~60 行 | 零风险 |
| 2 | `blocks/` — 5 个展示 block | ~200 行 | 低风险 |
| 3 | `ai/` — LLM 调用 | ~80 行 | 低风险 |
| 4 | `estimation/` — 场外基金估算 | ~100 行 | 中风险（依赖 market_data） |
| 5 | `slots/` — 7 个时段函数 | ~600 行 | 高风险（核心编排逻辑） |

---

## 📊 活跃文件中的死代码标注（待后续清理）

| 文件 | 死函数 | 建议操作 |
|------|--------|---------|
| `src/notify.py` | `_make_data_card()` (L83)、`_make_ai_analysis_card()` (L144)、`run_full_notify()` (L168) | 后续删除 |
| `src/advisor.py` | `main()` (L445)、`_get_fallback_portfolio()` (L547) | 后续删除 |
| `src/news_fetcher.py` | `fetch_portfolio_news()` | 后续删除（仅在 advisor.py 死代码 `main()` 中使用） |
