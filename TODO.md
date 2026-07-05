# TODO —— AI 量化投资系统开发路线图 整合版

> 最后更新：2026-07-05
> 当前阶段：核心系统+飞书机器人收&发双向通道、飞书投资数据库基本完成，进入机器人扩展+数据库优化+Prompt 微调阶段

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

---

## 📋 剩余待办 —— 按重要性排列

### 🟡 第二优先：机器人扩展

-  [ ] **D1. 按需快报与自选股管理 (On-Demand Commands)**
  - `@机器人 雷达 / 早报 / 收盘 / 午报` → 复用 `briefing.py`/`radar.py`
  - `@机器人 观察 [代码] / 取消观察 [代码]` → 飞书 OpenAPI 增删雷达观测表
  - `@机器人 资讯 [关键词]` → 结合持仓联网搜索去噪声

-  [ ] **D2. 快速记账与自动穿透持仓查询**（`@机器人 买入 [名称] [金额]`）
  - 写入交易流水表（status=pending，等待手动确认），替代 iPhone 快捷指令。
  - **非结构化 Function Calling 解析**：利用大模型提取出 `动作(买/卖)`、`标的代码/名称`、`金额/份额`。
  - **✨ 自动持仓分类与重仓股穿透探针（新品智能解析）：**
    - **资产大类自动归流**：当通过机器人新增加仓某只不认识的场外基金时，程序自动调用 `AkShare`（`ak.fund_portfolio_hold_em`）或通过 LLM 联网搜索。自动查出该基金的**前十大重仓股及权重**。
    - **自动推断写入**：根据重仓股属性，AI 自动推断该基金的 `投资载体`（场外基金）与 `资产大类`（如：半导体属于科技/股票类），自动回填飞书多维表格对应字段。并自动将其重仓股缓存至系统的配置文件 `config/sensitivity.yaml` 中，方便日间计算高阶的“加权成分股涨跌穿透”。

### 🟢 第三优先：功能增强 + prompt增强

- [ ] **D3. 宏观日历增强** — 事件→持仓映射改为可配置文件 `config/sensitivity.yaml`，如：
  <!-- events:
  - name: "美联储议息"
    keywords: ["FOMC", "Fed", "利率"]
    impact_assets: ["SPY", "QQQ", "TLT", "GLD"]
    direction_hint: "加息利空成长股，降息利好"
  - name: "中国LPR"
    keywords: ["LPR", "MLF", "降准"]
    impact_assets: ["沪深300", "中证500"] -->
  - AI 分析每日新闻时，自动匹配 `sensitivity.yaml` 中的事件关键词，输出"事件→持仓影响链"
  - **依赖**：`macro_calendar.py` 已就绪，纯重构

- [ ] **D4. prompt修改** — 回看飞书推送，调整 prompt 参数（max_tokens / temperature）
  - **类型**：纯调参，低投入，可融入先进的投资经验

- [ ] **D5. 飞书仪表盘** — 大类权重饼图、市值趋势（飞书 AI 辅助）

### ⚪ 第四优先：远期择机

- [ ] **D6 雷达深度分析、行业基本面研报**
  — 对信号标的用 Tavily 搜索相关新闻，LLM 输出展开分析；结合用户持仓，为用户提供某个关注的行业的深度研报

- [ ] **D7. Scriptable iOS 桌面小组件**
  - CI 产出 `widget-data.json` → GitHub Pages → Scriptable 3 尺寸小组件
  - 不需要服务器，不需要新域名

- [ ] **D8. 策略回测** — 需先积累数据
- [ ] **D9. 模拟盘** — `ENV=paper`

---

## 🏗️ 架构笔记

- **行情**：`market_data.py`
- **策略**：`strategy.py`（唯一真源）
- **简报**：`briefing.py`（7 时段 + 周报）
- **雷达**：`radar.py`（全量扫描 → 双信号 + LLM）
- **国际**：`global_news.py`（4 RSS → LLM 匹配翻译）
- **财报**：`earnings_calendar.py`（yfinance → 早间+周报）
- **宏观**：`macro_calendar.py`（ForexFactory → 敏感度映射）
- **机器人**：`bot_server.py`（Render FastAPI → 巡航 + LLM 问答 + 指令路由）
- **穿透**：`briefing.py._estimate_fund_realtime_pct`（场外基金白天实时估算）
- **LLM**：`llm.py` + `prompt_templates.py`
- **小组件/回测**：远期

## 当前可用命令

```bash
# 定时简报
python -m src.briefing morning / closing / sun_evening

# 数据维护
python -m src.price_updater --dry-run
python -m src.pending_resolver --dry-run
python -m src.radar --dry-run

# 测试
python -m src.advisor
python -m src.global_news --dry-run
uvicorn bot_server:app --reload

# 飞书群机器人（@AI投顾）
#   巡航 / 状态 / 仓位     → 实时仓位健康报告
#   任意投资问题            → LLM 结合持仓+雷达+新闻 智能问答
```
