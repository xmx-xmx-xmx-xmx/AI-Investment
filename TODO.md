# TODO —— AI 量化投资系统开发路线图

> 最后更新：2026-06-18
> 当前阶段：核心系统全面上线，Cron 已移交飞书外部触发

---

## ✅ 已归档（全部完成）

| 模块 | 文件 | 概要 |
|------|------|------|
| 行情抓取 | `market_data.py` | akshare/yfinance/Yahoo 直连，覆盖 A 股/港股/美股/VIX |
| 飞书 SDK | `feishu_client.py` | bitable 读写封装 |
| 策略中枢 | `strategy.py` | 阶梯阈值 / 增量优先 / 长底仓锁定 / 冷却期 / 防飞刀拦截 |
| AI 报告 | `advisor.py` | XML 结构化 Prompt v2.0 + 思想钢印 |
| 现价更新 | `price_updater.py` | 智能路由(基金/ETF/港股/美股) + 自动趋势检测(5 日净值) |
| Pending 确认 | `pending_resolver.py` | T 日日历 + 净值抓取 + 加权平均成本 + QDII 懒加载 |
| 节假日熔断 | `holiday_gate.py` | XSHG(中国) + XNYS(美国) 双日历 |
| 资讯引擎 | `news_fetcher.py` | 金十数据 + 华尔街见闻(免费) + Tavily(1 credit) + 关键词筛选 |
| 消息推送 | `notify.py` | 飞书群双卡片(数据卡 + AI 分析卡) |
| 多时段简报 | `briefing.py` | 六时段 + 节假日熔断 + AI 解读 + 持仓一览 |
| OCR 票据 | `auto_bill_parser.py` | XML Prompt v2.0 + Few-Shot 防越狱 |
| iPhone 记账 | 快捷指令 | 拍照 → iOS OCR → LLM → 飞书交易流水表 |
| 安全 | 全局 | 硬编码 Key 全清 + .gitignore + .env |
| 飞书表结构 | 底仓/交易流水 | 份额/成本均价/现价/(市值公式)/(收益率公式) + 趋势 + 标签 |
| 投资纪律 | `strategy.py` / `advisor.py` | **固收 50% / 美股 25% / A 股 10% / 港股 5% / 避险 10%** |
| 外部触发 | `daily-run.yml` | Cron 已移除，飞书 / workflow_dispatch 手动按需触发 |

---

## 📋 剩余待办 —— 按重要性排列

### 🔴 第一优先：高价值数据源矩阵

核心逻辑：GitHub Actions 海外网络 → yfinance 直连美股/VIX/美债，国内用华尔街见闻 API 补充宏观日历，RSS 源做英文长文翻译。

- [ ] **D1. 全球行情直连**
  - [ ] yfinance 全量激活：美股三大指数/VIX/美债收益率，替代国内易超时备用源
  - [ ] 底仓路由实盘验证：在飞书加入 A 股 ETF、港股、美股 ETF 各一只，验证 price_updater 智能路由
  - [ ] 🛡️ `pending_resolver.py` 已有 FUND_NAME_MAPPING 映射字典（已完成）

- [ ] **D2. 宏观事件日历**
  - [ ] 接入华尔街见闻 API `api-one.wallstcn.com/apiv1/finance/calendar`
  - [ ] 三星级以上事件过滤（CPI、美联储议息、央行降准/LPR）
  - [ ] 注入早间简报「📅 今日宏观日历」，Prompt 强制 AI 结合重磅数据给出波动预警

- [ ] **D3. 国际 RSS 信息流**
  - [ ] 新建 `src/global_news.py`，`feedparser` 接入 Yahoo Finance / Reuters RSS
  - [ ] LLM 云端翻译 + 摘要（英文长文 → 中文核心）
  - [ ] 抓取条数上限 + Token 熔断，防上下文窗口溢出

### 🟡 第二优先：洞察与报告增强

- [ ] **简报 AI 解读效果抽查** — 回看近期飞书群推送，调整 Prompt 参数
- [ ] **周报** — 周日 `sun_evening` 升级为完整周报（本周收益 vs 基准、偏离度趋势）
- [ ] **底仓路由全品种验证** — A 股 ETF/港股/美股三个路由在 price_updater 中实盘跑通

### 🟢 第三优先：交互与体验

- [ ] **飞书对话机器人** — 群内 @机器人「现在偏离度多少？」
- [ ] **飞书仪表盘** — 大类权重饼图、市值趋势

### ⚪ 第四优先：远期（择机）

- [ ] **策略回测** — 需先积累底仓快照数据
- [ ] **行业基本面研报**
- [ ] **模拟盘** — `ENV=paper`
- [ ] **IBKR** — 券商字段 + 汇率

---

## 🏗️ 架构笔记

- **行情入口**：`src/market_data.py`
- **策略入口**：`src/strategy.py`（唯一真源，LLM 只能引用不能推翻）
- **趋势检测**：`price_updater.py` → 飞书「趋势」字段 → strategy 自动读
- **成本/份额**：`pending_resolver.py` → 加权平均法 → 飞书「成本均价」「持仓份额」
- **收益率/市值**：飞书公式自动计算
- **节假日熔断**：`src/holiday_gate.py`
- **环境**：`ENV=dev/paper/prod`（未来）

## 当前可用命令

```bash
python -m src.price_updater --dry-run
python -m src.pending_resolver --dry-run
python -m src.advisor              # 终端完整 AI 报告
python -m src.notify --dry-run     # 预览不发送
python -m src.notify --data-only   # 仅数据卡
python -m src.briefing morning     # 任意时段手动跑
python -m src.briefing closing     # 收盘前指令
```
