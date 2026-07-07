# REFACTOR.md —— AI 量化投资系统重构蓝图

> 最后更新：2026-07-07（今晚剩余：任务二依赖替换 + 任务四千行拆分）

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

**迁移后效果**：删除 `src/constants.py`，`strategy.py` -40 行，`market_data.py` -60 行。所有策略参数可在 YAML 中直接修改，无需动代码。

---

### 🧩 第二优先：千行大文件 "解耦搬家" 蓝图

#### 当前 `briefing.py` 解构（1431 行 → 28 个函数）

```
├── 7 个时段编排函数 + 3 个共享 helper
├── 5 个纯展示 block 函数
├── 3 个 LLM/AI 函数
├── 5 个纯工具函数
├── 4 个行情估算函数
├── 1 个持仓展示
└── 1 个主入口
```

#### 拆分后目录树

```
src/
├── briefing/                    # 📁 新包
│   ├── __init__.py
│   ├── _orchestrator.py         # _push, _should_skip, _skip_msg, main()
│   ├── slots/                   # 7 时段编排（每文件 ~80行）
│   │   ├── morning.py / midday.py / closing.py / evening.py
│   │   ├── sat_morning.py / sun_evening.py
│   │   └── _helpers.py          # 亚太市场、全球快照、周收益
│   ├── blocks/                  # 纯展示 block（只拼字符串，不调 API）
│   │   ├── vix.py / futures.py / sector_rotation.py
│   │   ├── portfolio.py / trade.py / market_context.py
│   ├── ai/                      # LLM 调用
│   │   ├── insight.py / translate.py
│   ├── estimation/              # 场外基金估算
│   │   ├── fund_realtime.py / exchange.py
│   └── formatting/              # 纯文本工具（零外部依赖）
│       ├── news_format.py / labels.py
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

| 动作 | 内容 | 产物 |
|------|------|------|
| 动作一 | 提取旧项目宝藏 → `references/legacy_gems/`（4 文件）| fundamental_adapter.py、yfinance_fundamental_adapter.py、feishu_stream.py、retry_pattern.py |
| 动作一 | 打包删除 `_legacy_backup/` | `legacy_backup_final.tar.gz` (135MB)，394 个文件已清除 |
| 动作二 | 确立最高宪法 | `CLAUDE.md`（本地开发隔离规范）|
| 动作二 | 环境判定模块 | `src/env.py`（`is_production()` / `is_dev()`）|
| 动作三 | 删除孤立死代码文件 | `src/market_brief.py`（233 行）已物理删除 |
| 动作三 | 切除活跃文件中死函数 | `notify.py` -156 行 (`_make_data_card`、`_make_ai_analysis_card`、`run_full_notify`) |
| 动作三 | 切除活跃文件中死函数 | `advisor.py` -127 行 (`main`、`_get_fallback_portfolio`) + 移除 `news_fetcher`/`strategy` 导入 |
| 动作三 | 切除活跃文件中死函数 | `news_fetcher.py` -55 行 (`fetch_portfolio_news`、`build_queries`) |
| 动作四 | 策略配置 YAML 文件 | `config/strategy.yaml`（完整配置格式）|
| 动作四 | 配置加载器 | `src/config_loader.py`（单例 + 8 个 getter）|
| 验证 | 心跳测试 | `closing --dry-run` exit 0，全链路正常 |
| 推送 | Git | `ec929bf`：15 files, +2225/-607 |
