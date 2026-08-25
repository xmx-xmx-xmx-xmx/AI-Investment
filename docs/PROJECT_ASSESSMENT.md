# AI 量化投资项目全面评估报告

> 评估时间：2026-08-25
> 评估范围：src/ 23 个核心模块（约 8994 行）、tests/ 5 个测试文件（2152 行）、bot_server.py、CI 配置、配置文件、文档
> 评估方法：架构梳理 + 逐文件代码质量审计 + 文档与代码交叉验证

---

## 一、项目现状分析

### 1.1 整体架构

**架构定位**：飞书多维表格驱动的纪律型量化投资助手。
- 数据层：飞书多维表格 = 唯一数据库（底仓 / 交易流水 / 雷达观测 / 板块轮动配置 / 观测记录 5 张表）
- 计算层：Python 死算偏离度与阈值，LLM 仅做翻译与安抚
- 触发层：GitHub Actions `workflow_dispatch`（飞书 Bot 触发）+ Render FastAPI Webhook
- 展示层：飞书群双卡片推送 + 多时段简报（7 时段 + 周报）
- 零成本运行：完全依赖免费层（GitHub Actions / SiliconFlow 免费档 / akshare / yfinance）

**核心模块清单（实测行数）**：

| 模块 | 行数 | 职责 | 质量评级 |
|------|------|------|---------|
| briefing.py | **1622** | 7 时段简报编排 + LLM 解读 + 三层降级 | ⚠️ 巨型单文件 |
| market_data.py | 979 | 行情抓取多源 fallback | 🟡 良好但硬编码多 |
| radar.py | 651 | 雷达扫描 + 双信号 + LLM | 🟡 良好但有死代码 |
| pending_resolver.py | 622 | 净值抓取 + T 日确认 + QDII 懒加载 | 🟡 良好 |
| advisor.py | 440 | 持仓读取 + 偏离度 + prompt 组装 | 🔴 违反隔离规范 |
| strategy.py | 514 | 仓位健康 / 长底仓 / 防飞刀 / 冷却 | 🟡 逻辑清晰但硬编码 |
| macro_calendar.py | 562 | ForexFactory + 敏感度映射 | 🟡 良好但映射硬编码 |
| global_news.py | 587 | RSS → 评分 → LLM 匹配翻译 | 🟡 良好 |
| feishu_client.py | 375 | bitable 读写封装 | 🟢 质量较高 |
| price_updater.py | 385 | 现价智能路由 + 趋势检测 | 🟢 缓存兜底已实现 |
| auto_bill_parser.py | 321 | OCR → LLM → 飞书 | 🟡 独立链路 |
| news_fetcher.py | 332 | 金十 / 华尔街见闻 / Tavily | 🟡 Tavily 分支疑似死代码 |
| classification.py | 227 | 资产载体 + 大类推断 | 🟢 质量较高 |
| earnings_calendar.py | 210 | yfinance 财报日历 | 🟡 含新违规点 |
| prompt_templates.py | 132 | 投资宪法 + 六段式 + 思维链 | 🔴 权重与代码不一致 |
| notify.py | 132 | 飞书群推送 + HMAC | 🟢 良好 |
| holiday_gate.py | 120 | XSHG / XNYS 节假日熔断 | 🟢 质量最高之一 |
| llm.py | 112 | LLM 客户端工厂 + 三层降级 | 🟢 良好 |
| config_loader.py | 103 | YAML 单例加载器 | 🟢 设计干净但零调用 |
| timeout_guard.py | 84 | threading 硬超时装饰器 | 🟢 质量最高 |
| constants.py | 19 | TARGET_WEIGHTS 唯一真源 | 🔴 与宪法不一致 |
| env.py | 25 | is_production / is_dev 环境判定 | 🔴 全项目零引用 |
| bot_server.py | 466 | Render FastAPI 飞书机器人 | 🟡 含死代码与内存隐患 |

**技术栈**：Python 3.11+ / akshare + yfinance（行情）/ openai SDK 兼容 SiliconFlow（LLM）/ lark-oapi（飞书）/ FastAPI + uvicorn（Webhook）/ pandas + numpy / tenacity（重试）/ feedparser（RSS）/ exchange-calendars（节假日）。

### 1.2 已实现功能清单

| 功能 | 模块 | 完成度 | 备注 |
|------|------|--------|------|
| 多时段简报（7 时段 + 周报） | briefing.py | ✅ 100% | 1622 行待拆 |
| 行情抓取多源 fallback | market_data.py | ✅ 95% | 硬编码映射未 YAML 化 |
| 现价智能路由 + 缓存兜底 | price_updater.py | ✅ 100% | L271-275 已实现 |
| T 日净值确认 + 新品建仓 | pending_resolver.py | ✅ 95% | 汇率兜底硬编码 |
| 雷达双信号 + LLM 解读 | radar.py | ✅ 90% | `_radar_insight` 死代码 |
| 仓位健康 / 长底仓 / 防飞刀 / 冷却 | strategy.py | ✅ 95% | 阈值硬编码 |
| 投资宪法 + 六段式 prompt | prompt_templates.py | ⚠️ 80% | 权重与代码漂移 |
| 国际 RSS + LLM 翻译去重 | global_news.py | ✅ 95% | 关键词硬编码 |
| 宏观日历 + 敏感度映射 | macro_calendar.py | ✅ 90% | 映射硬编码 |
| 财报日历 | earnings_calendar.py | ✅ 90% | 含新违规点 |
| 节假日熔断 | holiday_gate.py | ✅ 100% | 质量高 |
| 三层 LLM 容灾降级 | llm.py + briefing.py | ✅ 100% | DeepSeek→Qwen27B→Qwen9B→纯文本 |
| 超时保护装饰器 | timeout_guard.py | ✅ 100% | threading 实现 |
| 飞书群双卡片推送 | notify.py | ✅ 100% | HMAC 签名正确 |
| 板块轮动温差（12 板块） | market_data.py + 飞书表 | ✅ 100% | 配置表动态生效 |
| 纳指期货实时行情 | market_data.py | ✅ 100% | Sina hf_NQ / hf_ES |
| 场外基金穿透估算 | briefing.py | ✅ 100% | 白天指数 × 折扣 |
| 港股 ETF 数据源修复 | market_data.py + radar.py | ✅ 100% | Sina 替代 |
| 英文标题批量翻译 | briefing.py | ✅ 100% | LLM 批量 |
| 资产分类推断 | classification.py | ✅ 100% | 多策略 + 缓存 |
| 飞书机器人巡航 + LLM 问答 | bot_server.py | ⚠️ 70% | 含死代码 + 内存隐患 |
| OCR 票据解析 | auto_bill_parser.py | ✅ 90% | 独立链路 |
| GitHub Actions 触发 | daily-run.yml | ⚠️ 75% | 仅 workflow_dispatch，无 cron |

---

## 二、未完成功能 / TODO 项

### 2.1 REFACTOR.md 头号待办（未启动）

| 待办 | 现状 | 影响 |
|------|------|------|
| 🔴 **YAML 配置化** | config_loader.py 8 个 getter 全部零调用者；constants.py / strategy.py / market_data.py 仍全部硬编码 | 改纪律必须改代码，违反"配置即代码"原则 |
| 🔴 **briefing.py 千行拆分** | 实测 1622 行（README/CLAUDE 写 1431 已过时）；slots / blocks / ai / estimation / formatting 5 子包零启动 | 维护成本高，单点故障风险 |

### 2.2 TODO.md 路线图待办

| 优先级 | 待办 | 状态 |
|--------|------|------|
| 🟠 进行中 | YAML 配置化（详见 2.1） | ❌ 未落地 |
| 🔵 待办 | briefing.py 微创拆分（5 个优先级） | ❌ 未启动 |
| 🟡 P1 | D1 按需快报与自选股管理（`@机器人 雷达 / 早报` 等） | ❌ 未实现，基建已就绪（notify.send_card） |
| 🟡 P1 | D2 快速记账 `@机器人 买入 [名称] [金额]` | ❌ 未实现 |
| 🟢 P2 | D3 VIX 动态赔率授权 | ❌ 未实现，`fetch_vix` 已有 |
| 🟢 P2 | D4 技术面风控闸门（MA20 偏离 / ETF 溢价） | ❌ 未实现，radar 已算 MA20 |
| 🔵 P3 | D5 tenacity 重试装饰器注入 market_data | ❌ 未实现，legacy_gems/retry_pattern.py 已提取 |
| 🔵 P3 | D5b 基本面估值（PE / PB / ROE） | ❌ 未实现，legacy_gems/fundamental_adapter.py 已提取 |
| 🔵 P3 | D5c 飞书高级卡片升级 | ❌ 未实现，legacy_gems/feishu_stream.py 已提取 |
| 🔵 P3 | D6 宏观敏感度映射改 config/sensitivity.yaml | ❌ 未实现 |
| 🔵 P3 | D6 prompt 微调（max_tokens / temperature） | ❌ 未实现 |
| 🔵 P3 | D7 飞书仪表盘（饼图 / 市值趋势） | ❌ 未实现 |
| ⚪ 远期 | D8 雷达深度分析 / 行业研报 | ❌ |
| ⚪ 远期 | D9 Scriptable iOS 桌面小组件 | ❌ |
| ⚪ 远期 | D10 策略回测 | ❌ 需先积累数据 |
| ⚪ 远期 | D11 模拟盘（ENV=paper） | ❌ |

### 2.3 文档与代码漂移（隐藏的"未完成"）

| 漂移点 | 证据 | 严重性 |
|--------|------|--------|
| 🔴 **TARGET_WEIGHTS 三处不一致** | constants.py / strategy.py / config/strategy.yaml / README.md 均为 美股20%/港股10%；但 prompt_templates.py L23 宪法为 美股25%/港股5%；TODO.md L31 也写"50/25/10/5/10" | **极高** —— LLM 基于错误权重解读 |
| 🔴 **investment_main.py 死引用** | L98、L119 `from src.market_brief import main as brief_main`，但 market_brief.py 已被删除（REFACTOR.md "死代码切除"） | **高** —— 总入口文件无法运行 |
| 🟡 **CLAUDE.md 本地隔离规范零落地** | env.is_production() / is_dev() 全项目零引用；CLAUDE 1.3 节强制包裹要求未实现 | **高** —— 本地 Token 消耗≠0 |
| 🟡 **README 写"159 个单元测试"** | 实测 tests/ 仅 5 个 .py 文件（test_macro_calendar / test_market_data / test_global_news / test_radar / test_strategy），覆盖 5/23 模块 | **中** —— 文档夸大 |
| 🟡 **requirements.txt 与 pyproject.toml 不一致** | requirements.txt 列 exchange-calendars + litellm + openpyxl + PyYAML；pyproject.toml 未列 | **中** —— uv sync 会缺包 |
| 🟡 **README 项目结构行数过时** | README / CLAUDE 写 briefing.py 1431 行，实测 1622 行 | **低** |

---

## 三、功能评估与优化建议

### 3.1 代码质量问题（按严重性排序）

#### 🔴 P0 严重 —— 必须立即处理

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| 1 | **CLAUDE.md 本地隔离规范全项目未落地** | env.py 25 行只在自己 docstring 出现；11 处 `FeishuClient()` 直接实例化（pending_resolver:397 / advisor:86 / briefing:184 / radar:331 / earnings_calendar:33 / market_data:537 / price_updater:238 / global_news:505 / strategy:49&509 / bot_server:70&97） | 本地 `--dry-run` 仍真实调用飞书 API，违反"本地 Token 消耗=0"红线；CLAUDE 1.1-1.4 节形同虚设 |
| 2 | **TARGET_WEIGHTS 三处数据漂移** | 代码层（constants/strategy/yaml/README）= 美股20%/港股10%；宪法 prompt = 美股25%/港股5% | LLM 解读基于错误权重，输出建议与实际策略背离 |
| 3 | **investment_main.py 总入口死引用** | L98、L119 引用已删除的 `src.market_brief` | 总入口无法运行，用户按 README `python main.py` 会直接 ImportError |
| 4 | **config_loader.py 8 个 getter 全闲置** | YAML 文件已就绪，加载器已写好，但 constants.py / strategy.py / market_data.py 零迁移 | "配置即代码"原则未实现，改纪律仍需改代码 |

#### 🟠 P1 高 —— 建议本季度处理

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| 5 | **briefing.py 1622 行未拆分** | REFACTOR.md 头号待办未启动 | 维护成本高、单点故障、新人上手难 |
| 6 | **FeishuClient.list_records 打印 token 前缀** | feishu_client.py | 日志泄露风险（生产日志可能暴露 token 片段） |
| 7 | **bot_server `_processed_events` 无 TTL** | bot_server.py L45、L415 | 全局 set 仅在 >10000 时全清，长期运行内存泄漏；应改 TTL dict 或 LRU |
| 8 | **bot_server Verification Token 校验可选** | bot_server.py L404 | 生产应强制校验，防伪造事件 |
| 9 | **bot_server L100-102 死代码** | `rb = load_portfolio.__globals__.get("calculate_rebalance")  # won't work` 然后立刻重新 import | 混乱且无意义 |
| 10 | **测试覆盖仅 5/23 模块** | tests/ 无 briefing / advisor / pending_resolver / price_updater / feishu_client / classification / prompt_templates / timeout_guard / config_loader / notify / holiday_gate / earnings_calendar / auto_bill_parser / news_fetcher / llm / env / constants / bot_server 的测试 | 重构无安全网 |
| 11 | **CI 仅 workflow_dispatch 无 cron 兜底** | daily-run.yml | 飞书 Bot 宕机 → 整个定时调度链断裂；建议加 cron 兜底（如每天 08:30 自动跑 morning） |

#### 🟡 P2 中 —— 路线图推进时一并处理

| # | 问题 | 位置 |
|---|------|------|
| 12 | market_data.py k780 汇率 appkey 硬编码 | L916 |
| 13 | pending_resolver `_get_hkd_cny_rate` 写死 0.92 兜底 | L397 附近 |
| 14 | macro_calendar EVENT_SENSITIVITY 7 组硬编码 | D6 待办 |
| 15 | global_news `_HIGH_PRIORITY` / `_MEDIUM_PRIORITY` 大段关键词硬编码 | L505 附近 |
| 16 | news_fetcher Tavily 分支疑似死代码（未被 `fetch_all_news` 调用） | 全文件 |
| 17 | feishu_client TABLE_MAP 4 个 table_id 硬编码 | 全文件 |
| 18 | auto_bill_parser 模块级 `logging.basicConfig` 副作用 + `FEISHU_TABLE_ID` 默认值硬编码 | 全文件 |
| 19 | advisor.DEVIATION_THRESHOLD=0.03 与 strategy 的 5% 不一致 | advisor.py |
| 20 | requirements.txt 与 pyproject.toml 不一致（exchange-calendars / litellm / openpyxl / PyYAML） | 根目录 |

### 3.2 性能瓶颈

| 瓶颈 | 位置 | 现状 | 建议 |
|------|------|------|------|
| GitHub Actions 15 分钟超时 | daily-run.yml L31 | 已通过三层容灾改造降至 8-10 分钟（REFACTOR.md 已归档） | ✅ 已解决 |
| 单次 LLM 调用阻塞 | briefing.py | 三层降级链已建（DeepSeek 120s → Qwen27B 90s → Qwen9B 30s → 纯文本） | ✅ 已解决 |
| yfinance 跨墙慢 | market_data.py | `with_timeout` 10s 已套；缓存兜底已实现 | ✅ 已解决 |
| bot_server 同步处理 | bot_server.py | 已改为后台线程异步（`_run_command_async`） | ✅ 已解决 |
| briefing.py 单文件 1622 行 | briefing.py | 拆分未启动，IDE 解析慢、单点故障 | 🔴 见 P1 #5 |
| FeishuClient.list_records 每次全表扫 | strategy._check_cooldown / _fetch_radar_signals | 每次策略判定都全表读交易流水 + 雷达表 | 🟡 建议加飞书侧 filter 或本地短时缓存 |

### 3.3 安全隐患

| 隐患 | 位置 | 严重性 | 建议 |
|------|------|--------|------|
| `.env` 提交风险 | 项目根 | 低 | 已在 .gitignore，但建议加 pre-commit 检查 |
| `feishu_triggers_pat.md` | 项目根 | 低 | 已在 .gitignore |
| FeishuClient.list_records 日志打印 token 前缀 | feishu_client.py | 🟠 中 | 改为只打印表名 + 行数 |
| bot_server Verification Token 可选 | bot_server.py L404 | 🟠 中 | 生产强制校验 |
| k780 汇率 appkey 硬编码 | market_data.py L916 | 🟡 低 | 迁移到环境变量 |
| `auto_bill_parser.FEISHU_TABLE_ID` 默认值硬编码 | auto_bill_parser.py | 🟡 低 | 改读环境变量 |
| bot_server `_processed_events` 内存无限增长 | bot_server.py L45 | 🟡 低 | 改 TTL dict（如 30 分钟过期） |

### 3.4 关键环节缺失分析

| 环节 | 现状 | 缺失 / 改进点 |
|------|------|--------------|
| **数据源接入** | akshare + yfinance + Sina + ForexFactory + 4 RSS + 金十 + 华尔街见闻 + Tavily，多源 fallback 完备 | ① 缺基本面数据源（PE/PB/ROE），legacy_gems/fundamental_adapter.py 已提取但未接入（D5b）；② Tavily 分支疑似死代码，需核实；③ ETF 源切换靠代码硬编码，建议改配置驱动 |
| **模型推理** | 三层降级链完备（DeepSeek→Qwen27B→Qwen9B→纯文本），超时保护齐全 | ① 宪法权重与代码漂移（P0 #2），模型基于错误权重推理；② prompt 微调参数（max_tokens / temperature）未做 A/B（D6）；③ 缺推理质量评估指标（如解读命中率回看） |
| **策略回测** | ❌ 完全缺失 | TODO D10 标注"需先积累数据"。当前 `judge()` 是无状态运行，每次从飞书现读，无历史快照。建议：① 飞书「观测记录表」已预留，应开始每日落盘策略输出；② 用 legacy_gems 数据做离线回测框架 |
| **策略执行** | 阶梯阈值 + 长底仓 + 防飞刀 + 冷却期四重护栏逻辑清晰 | ① VIX 动态赔率未接入（D3）；② MA20 偏离 / ETF 溢价技术面风控未接入（D4，radar 已算 MA20，"差递给 strategy 这一步"）；③ 增量资金金额硬编码 100-200 元，未与 VIX 联动 |
| **风控维度** | 纯技术面（MA20 偏离度）单一维度 | 缺基本面估值维度（D5b），红利低波 / 港股消费 等标的缺 PE/PB/ROE 监控 |
| **可观测性** | 飞书群推送 + 日志 | 缺：① 推送命中率统计；② LLM 解读质量回看；③ 策略执行偏差追踪；④ 飞书仪表盘（D7） |
| **测试** | 5 个核心模块有测试，覆盖 159 用例 | 18 个模块零测试，briefing / advisor / pending_resolver / price_updater / feishu_client 等核心均无；重构无安全网 |

### 3.5 优化建议（按优先级排序）

#### 🔴 P0 —— 立即处理（1-2 天）

1. **同步 TARGET_WEIGHTS**：将 prompt_templates.py L23 宪法改为 `固收 50% / 美股 20% / A股 10% / 港股 10% / 避险商品 10%`，与 constants.py / strategy.yaml 对齐；同步修正 TODO.md L31 的"50/25/10/5/10"。
2. **修复 investment_main.py 死引用**：L98、L119 引用已删除的 `src.market_brief`，要么删除该入口文件（README 已用 `python -m src.briefing` 替代），要么改引用 `src.briefing`。
3. **全项目接入 `is_production()` 守卫**：在 11 处 `FeishuClient()` 实例化点包裹 `if is_production()`，本地返回 None 或加载 mock；这是 CLAUDE.md 1.3 节的强制要求。

#### 🟠 P1 —— 本季度处理（1-2 周）

4. **启动 YAML 配置化迁移**：config_loader.py 8 个 getter 已就绪，按 REFACTOR.md 表格逐项替换：constants.TARGET_WEIGHTS → get_target_weights()；strategy.THRESHOLD_* / COOLDOWN_DAYS / _SIGNAL_META / _RADAR_CLASS_MAP → get_thresholds() / get_cooldown_days() / get_signals() / get_radar_class_map()；market_data.CN_ETF_MAP / US_ETF_MAP / HK_STOCK_MAP / _FUTURES_NAME_MAP → get_etf_maps()。完成后删除 constants.py。
5. **briefing.py 拆分优先级 1-2**：先拆 `formatting/`（纯工具函数，零风险）和 `blocks/`（5 个展示 block，低风险），后续 slots / ai / estimation 分批跟进。
6. **补测试**：优先补 briefing / advisor / pending_resolver / price_updater / feishu_client 5 个核心模块的单元测试，让后续拆分有安全网。
7. **bot_server 加固**：`_processed_events` 改 TTL dict（30 分钟过期）；Verification Token 强制校验；清理 L100-102 死代码。
8. **CI 加 cron 兜底**：daily-run.yml 在 workflow_dispatch 之外加 `schedule: - cron: '30 0 * * 1-5'`（北京时间 08:30），防飞书 Bot 宕机导致调度链断裂。
9. **依赖对齐**：把 requirements.txt 的 exchange-calendars / litellm / openpyxl / PyYAML 同步到 pyproject.toml，统一用 `uv sync`。

#### 🟡 P2 —— 路线图推进时一并处理

10. **D3 VIX 动态赔率**：strategy.py 集成 `fetch_vix()`，VIX<20 常规 100-200；VIX>30 授权 2-3 倍左侧狙击；宪法"每次 100-200 元"改为"金额由系统根据恐慌指数动态计算"。
11. **D4 技术面风控闸门**：把 radar 已算的 MA20 偏离度递给 strategy 做拦截（>5% 一票否决买入）；新增 `market_data.fetch_etf_premium`（>2% 拦截追高）。
12. **D5b 基本面估值**：解析 legacy_gems/fundamental_adapter.py + yfinance_fundamental_adapter.py，为红利低波 / 港股消费 补 PE/PB/ROE/股息率监控。
13. **D5 tenacity 重试**：用 legacy_gems/retry_pattern.py 给 market_data 所有外部抓取接口注入 `@retry`，防单次网络抖动断链。
14. **D6 宏观敏感度改 YAML**：macro_calendar.EVENT_SENSITIVITY 7 组映射迁移到 `config/sensitivity.yaml`。
15. **D1/D2 机器人扩展**：基建已就绪（notify.send_card / FeishuPusher），实现 `@机器人 雷达 / 早报 / 收盘 / 买入 [名称] [金额]`。

#### 🟢 P3 —— 远期

16. **D7 飞书仪表盘**：大类权重饼图、市值趋势。
17. **D10 策略回测**：先在「观测记录表」每日落盘策略输出，积累数据后做离线回测框架。
18. **D11 模拟盘**：`ENV=paper` 环境隔离。
19. **D5c 飞书高级卡片**：研究 legacy_gems/feishu_stream.py 交互卡片模板，升级纯文本简报。

---

## 四、整体结论

**项目成熟度**：核心功能完备（22/23 模块已实现），三层 LLM 容灾降级、超时保护、缓存兜底等基础设施质量高，README/CLAUDE/REFACTOR/TODO 文档体系完整。

**最大风险**：① CLAUDE.md 本地隔离规范零落地（11 处 FeishuClient 直接实例化）；② TARGET_WEIGHTS 三处数据漂移（宪法与代码不一致）；③ investment_main.py 总入口死引用；④ YAML 配置化与 briefing.py 拆分两个头号待办未启动。

**最大优势**：纪律驱动设计清晰（Python 死算 + LLM 翻译）、容灾降级链完备、零成本运行架构稳定、legacy_gems 战利品已提取待用。

**建议路径**：先修 P0 三处（1-2 天）→ 启动 YAML 迁移 + briefing 拆分优先级 1-2（1-2 周）→ 补测试安全网 → 推进 D3/D4 策略增强 → 远期 D10 回测 / D7 仪表盘。
