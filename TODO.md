# TODO —— AI 量化投资系统开发路线图 整合版

> 最后更新：2026-07-07
> 当前阶段：核心系统技术债清偿 + YAML 配置化 + 千行文件微创拆分

---

## ✅ 已归档（全部完成）

| 模块 | 文件 | 概要 |
|------|------|------|
| 行情抓取 | `market_data.py` | akshare/yfinance 双源，A 股/港股/美股/VIX/三大指数/美债 |
| 飞书 SDK | `feishu_client.py` | bitable 读写封装（含 create/delete） |
| 策略中枢 | `strategy.py` | 仓位健康报告 / 长底仓锁定 / 防飞刀 / 冷却期 |
| 现价更新 | `price_updater.py` | 智能路由 + 趋势检测 + 日涨跌幅写入 |
| Pending 确认 | `pending_resolver.py` | 新品自动建仓 + 净值抓取 + QDII 懒加载 |
| 节假日熔断 | `holiday_gate.py` | XSHG(中国) + XNYS(美国) |
| 资讯引擎 | `news_fetcher.py` | 金十 + 华尔街见闻 + Tavily + 广告过滤 |
| 国际资讯 | `global_news.py` | 3 RSS → LLM 匹配翻译去重 → 「🌐 国际快讯」 |
| 财报日历 | `earnings_calendar.py` | yfinance 财报日期 → 早间+周报注入 |
| 消息推送 | `notify.py` | 飞书群双卡片 |
| 多时段简报 | `briefing.py` | 七时段 + 五区块周报 + AI 综合解读 + 市场基准 |
| 宏观日历 | `macro_calendar.py` | ForexFactory → 筛选 → 持仓敏感度映射 |
| 雷达观测 | `radar.py` | 底仓+雷达全量扫描 → 双信号 + LLM 解读 |
| Promp工重构 | `prompt_templates.py` | 投资宪法 + 六段式模板 + 思维链 |
| LLM 客户端 | `llm.py` | SiliconFlow DeepSeek-V4-Flash |
| OCR 票据 | `auto_bill_parser.py` | 拍照→OCR→LLM→飞书 |
| iPhone 记账 | 快捷指令 | 拍照 → 交易流水表 |
| 安全 | 全局 | .env + .gitignore |
| 飞书表结构 | 4 张表 | 底仓/交易流水/雷达观测 |
| 投资纪律 | **50/25/10/5/10** | 长底仓不卖，自然稀释，增量定投 |
| 外部触发 | `daily-run.yml` | 飞书 workflow_dispatch |
| 飞书机器人 | `bot_server.py` | Render FastAPI：巡航 + LLM 问答 |
| 场外基金穿透 | `briefing.py._estimate_fund_realtime_pct` | 白天用指数实时涨跌×折扣系数估算场外基金变动，标注 `[穿透估算]`，夜间真值自动覆盖 |
| HKD/CNY 汇率 | `market_data.py` `advisor.py` `strategy.py` | 多源汇率抓取（akshare→yfinance→k780 API），持仓市值自动换算 CNY，简报脚注标注汇率基准日 |
| 资产分类重构 | `classification.py`（新）+ `radar.py` `pending_resolver.py` 等 | 分离「投资载体」(场外基金/场内ETF/个股) 与「资产大类」，简报按载体分组展示，新品自动推断两个字段 |
| 机器人防刷屏 | `bot_server.py` | event_id 去重（防飞书超时重试） + 消息确认异步化 |
| 港股ETF行情修复 | `market_data.py` | Sina 实时行情作为港股 ETF 主要源，解决 03121/03486 涨跌方向错误 |
| 英文新闻翻译 | `briefing.py._translate_english_titles` | 中英混合标题自动检测 + LLM 批量翻译为中文 |
| 新闻截断优化 | `news_fetcher.py` + `briefing.py` | 标题截断 120→200 字，摘要 200→300 字 |
| 纳指期货实时行情 | `market_data.py.fetch_nq_futures` | Sina `hf_NQ`/`hf_ES` 实时期货 → 14:30 盘前风向 + 21:00 夜盘前瞻 |
| 核心行业板块轮动追踪 | `market_data.py.fetch_sector_deltas` + 飞书「板块轮动配置表」 | 12 板块温差计算（行业涨跌幅 vs 大盘基准），飞书表动态配置、即时生效 |
| 持仓日盈亏金额 | `briefing.py._portfolio_value_summary` | 每笔持仓显示当日盈亏金额 + 底部「今日浮动盈亏」汇总 |
| 雷达防截断 | `briefing.py` `radar.py` | 最多展示 5 个高优先级信号 + LLM token 500→1000 |
| 港股数据源修复 | `radar.py._fetch_hk_historical` | `stock_hk_daily`（Sina）替代不存在的 `stock_hk_hist_em` + 放宽最低数据要求 |
| 架构清盘 | `CLAUDE.md` `REFACTOR.md` `src/env.py` | 本地开发隔离最高宪法 + 环境判定 + 重构蓝图 |
| 死代码清除 | `market_brief.py` `notify.py` `advisor.py` `news_fetcher.py` | 删除 233 行孤立文件 + 3 个模块中 6 个死函数 |
| 宝藏提取 | `references/legacy_gems/` | 从旧项目提取 fundamental_adapter / yfinance_fundamental / feishu_stream / retry_pattern |
| 策略配置外置 | `config/strategy.yaml` `src/config_loader.py` | YAML 配置 + 单例加载器（文件已建，依赖替换待做） |
| 旧项目清盘 | `_legacy_backup/` | tar.gz 打包（135MB）后物理删除，394 个文件不再污染

---

## 📋 剩余待办 —— 按重要性排列

### 🟠 进行中：YAML 配置化（详见 REFACTOR.md 任务二下半场）
  - [ ] 将 `constants.py` / `strategy.py` / `market_data.py` 中的硬编码迁移到 `config/strategy.yaml` + `config_loader.py`，完成后删除 `constants.py`

### 🔵 千行文件微创拆分（详见 REFACTOR.md 任务四）
  - [ ] `briefing.py` 1431行 → `src/briefing/` 包（slots / blocks / ai / estimation / formatting）
  - [ ] 分 5 个优先级逐步拆，优先级1-2零风险优先

### 🟡 第二优先：机器人扩展

- [ ] **D1. 按需快报与自选股管理 (On-Demand Commands)**
  - `@机器人 雷达 / 早报 / 收盘 / 午报` → 复用 `briefing.py`/`radar.py`
  - `@机器人 观察 [代码] / 取消观察 [代码]` → 飞书 OpenAPI 增删雷达观测表
  - `@机器人 资讯 [关键词]` → 结合持仓联网搜索去噪声
  - 💡 **已有基建**：`src/notify.py` 的 `__main__` 入口已恢复，当前复用 closing 简报作为轻量推送（`workflow_dispatch: notify`）。后续 `@机器人 收盘` 等命令的飞书卡片推送逻辑可直接在此扩展。`FeishuPusher.send_card()` 已封装好 HMAC 签名 + 飞书卡片 JSON 模板。

- [ ] **D2. 快速记账与自动穿透持仓查询**（`@机器人 买入 [名称] [金额]`）
  - 写入交易流水表（status=pending，等待手动确认），替代 iPhone 快捷指令。
  - 利用大模型提取出 `动作(买/卖)`、`标的代码/名称`、`金额/份额`

### 🟢 第三优先：策略增强（VIX 动态赔率 + 技术面风控）

- [ ] **D3. VIX 动态赔率授权**（方向 2）
  - `strategy.py` 集成已有的 `fetch_vix()`，建立 VIX 分级乘数映射
  - VIX < 20 → 常规小额定投 100-200；VIX > 30 → 授权 2-3 倍资金左侧狙击
  - `prompt_templates.py` 宪法中”每次 100-200 元”改为”金额由系统根据恐慌指数动态计算”

- [ ] **D4. 技术面风控闸门**（方向 3）
  - MA20 偏离度 > 5% → 一票否决买入（`strategy.py._apply_valuation_gate`）
  - 场内 ETF 溢价率 > 2% → 拦截追高（`market_data.py.fetch_etf_premium`）
  - radar.py 已计算 MA20，就差”把数据递给 strategy 做拦截”这一步

### 🔵 后续增强（含 legacy_gems 战利品）

- [ ] **D5. 稳定性基建** — 利用 `references/legacy_gems/retry_pattern.py` 的指数退避重试模式，为 `market_data.py` 所有外部行情抓取接口注入 `@retry` 装饰器（tenacity），防止单次网络抖动导致整条简报链断裂。

- [ ] **D5b. 多维风控升级（基本面估值）** — 解析 `references/legacy_gems/fundamental_adapter.py` 和 `yfinance_fundamental_adapter.py`，引入真实的 PE/PB/ROE/股息率数据。为红利低波(021551)和港股消费(017435)提供基本面估值监控，补充当前纯技术面（MA20偏离度）的单一风控维度。
  - AkshareFundamentalAdapter.get_fundamental_bundle() → PE/PB/ROE/分红/十大股东
  - yfinance TTM 股息率计算公式（`ttm_dividend_yield_pct`）可直接复用

- [ ] **D5c. 简报 UI 进化（飞书高级卡片）** — 研究 `references/legacy_gems/feishu_stream.py` 的 `_send_interactive_card()` 交互卡片 JSON 模板（header + elements + 色块），在未来将纯文本简报升级为包含涨跌红绿色块与交互按钮的飞书高级卡片。

- [ ] **D6. 宏观日历增强** — 事件→持仓映射改为可配置文件 `config/sensitivity.yaml`

- [ ] **D6. prompt 微调** — 回看飞书推送，调整 prompt 参数（max_tokens / temperature）

- [ ] **D7. 飞书仪表盘** — 大类权重饼图、市值趋势（飞书 AI 辅助）

### ⚪ 远期择机

- [ ] **D8. 雷达深度分析、行业基本面研报**
- [ ] **D9. Scriptable iOS 桌面小组件**
- [ ] **D10. 策略回测** — 需先积累数据
- [ ] **D11. 模拟盘** — `ENV=paper`

---

## 🏗️ 架构笔记

- **行情**：`market_data.py`（A/港/美股 ETF + 三大指数 + VIX + 美债 + **纳指期货** + **板块温差**）
- **策略**：`strategy.py`（唯一真源——仓位健康报告 / 长底仓锁定 / 防飞刀 / 冷却期）
- **简报**：`briefing.py`（7 时段 + 周报 + **纳指期货区块** + **板块轮动区块** + **日盈亏金额**）
- **雷达**：`radar.py`（全量扫描 → 双信号 + LLM + 最多 5 信号防截断）
- **国际**：`global_news.py`（4 RSS → LLM 匹配翻译）
- **财报**：`earnings_calendar.py`（yfinance → 早间+周报）
- **宏观**：`macro_calendar.py`（ForexFactory → 敏感度映射）
- **飞书**：`feishu_client.py`（bitable 读写）+ **「板块轮动配置表」（动态配置，手机改即时生效）**
- **机器人**：`bot_server.py`（Render FastAPI → 巡航 + LLM 问答 + event_id 去重）
- **穿透**：`briefing.py._estimate_fund_realtime_pct`（场外基金白天实时估算）
- **翻译**：`briefing.py._translate_english_titles`（中英混合标题自动检测 + LLM 批量翻译）
- **LLM**：`llm.py` + `prompt_templates.py`（投资宪法 + 六段式模板 + 思维链含板块温差步骤）
- **小组件/回测**：远期

## 当前可用命令

```bash
# 定时简报
python -m src.briefing morning / midday / closing / evening / sat_morning / sun_evening

# 数据维护
python -m src.price_updater --dry-run
python -m src.pending_resolver --dry-run
python -m src.radar --dry-run

# 数据验证
python -c "from src.market_data import fetch_nq_futures; print(fetch_nq_futures('NQ'))"
python -c "from src.market_data import fetch_sector_deltas; [print(f'{d[\"sector\"]}: {d[\"delta\"]:+.1f}%') for d in fetch_sector_deltas()]"

# 测试
python -m src.advisor
python -m src.global_news --dry-run
uvicorn bot_server:app --reload

# 飞书群机器人（@AI投顾）
#   巡航 / 状态 / 仓位     → 实时仓位健康报告
#   任意投资问题            → LLM 结合持仓+雷达+新闻 智能问答

# 板块轮动配置（更新后即时生效，零代码）
#   打开飞书 → 「板块轮动配置表」 → 编辑行/加行/改代码/勾选启用
```
