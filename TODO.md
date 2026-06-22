# TODO —— AI 量化投资系统开发路线图

> 最后更新：2026-06-22
> 当前阶段：D1-D6 全部完成，进入洞察增强 + 交互体验阶段

---

## ✅ 已归档（全部完成）

| 模块 | 文件 | 概要 |
|------|------|------|
| 行情抓取 | `market_data.py` | akshare/yfinance 双源，覆盖 A 股/港股/美股/VIX/美股三大指数/美债收益率 |
| 飞书 SDK | `feishu_client.py` | bitable 读写封装 |
| 策略中枢 | `strategy.py` | 阶梯阈值 / 增量优先 / 长底仓锁定 / 冷却期 / 防飞刀拦截 |
| AI 报告 | `advisor.py` | XML 结构化 Prompt v2.0 + 思想钢印 |
| 现价更新 | `price_updater.py` | 智能路由(基金/ETF/港股/美股) + 自动趋势检测(5 日净值) + 日涨跌幅写入 |
| Pending 确认 | `pending_resolver.py` | T 日日历 + 净值抓取 + 加权平均成本 + QDII 懒加载 |
| 节假日熔断 | `holiday_gate.py` | XSHG(中国) + XNYS(美国) 双日历 |
| 资讯引擎 | `news_fetcher.py` | 金十数据 + 华尔街见闻(免费) + Tavily(1 credit) + 关键词筛选 |
| 国际资讯 | `global_news.py` | 3 条 RSS（Yahoo/Reuters/Semiconductor）→ LLM 匹配翻译去重 → 简报「🌐 国际快讯」|
| 消息推送 | `notify.py` | 飞书群双卡片(数据卡 + AI 分析卡) |
| 多时段简报 | `briefing.py` | 六时段 + 节假日熔断 + AI 解读 + 持仓一览 + 宏观日历 + 雷达扫描 + 市场基准 + 国际快讯 |
| 宏观日历 | `macro_calendar.py` | ForexFactory 免费 JSON 接口 → 国家/影响级别/关键词三层筛选 → 7 组敏感度细粒度持仓映射 |
| 雷达观测 | `radar.py` | 飞书「雷达观测表」→ 抄底/追涨双信号 → LLM 三行解读 → 早间/收盘前简报注入 |
| LLM 客户端 | `llm.py` | 共享 LLM 客户端工厂（单一配置点，所有模块统一调 SiliconFlow） |
| 常量 | `constants.py` | 共享常量（资产大类枚举、信号类型等） |
| OCR 票据 | `auto_bill_parser.py` | XML Prompt v2.0 + Few-Shot 防越狱 |
| iPhone 记账 | 快捷指令 | 拍照 → iOS OCR → LLM → 飞书交易流水表 |
| 安全 | 全局 | 硬编码 Key 全清 + .gitignore + .env |
| 飞书表结构 | 底仓/交易流水/雷达观测 | 份额/成本均价/现价/(市值公式)/(收益率公式) + 趋势 + 标签 |
| 投资纪律 | `strategy.py` / `advisor.py` | **固收 50% / 美股 25% / A 股 10% / 港股 5% / 避险 10%** |
| 外部触发 | `daily-run.yml` | Cron 已移除，飞书 / workflow_dispatch 手动按需触发 |

---

## 📋 剩余待办 —— 按重要性排列

### 🟡 第二优先：洞察与报告增强

> D1-D6 数据管道已铺完。下一步让简报更聪明、让周报有闭环。

- [ ] **周报** — 周日 `sun_evening` 升级为完整周报
  - [ ] 本周收益 vs 基准（SPY/沪深300）对比
  - [ ] 偏离度趋势（文字描述）
  - [ ] 本周关键事件回顾（宏观日历已发生事件 + 对持仓的影响摘要）
  - [ ] 本周国际快讯回顾（RSS 匹配到的本周重要新闻汇总）
  - **依赖**：`briefing.py` + `market_data.py`（D1） + `macro_calendar.py`（D2） + `global_news.py`（D3）
  - **为什么优先**：所有数据源已就绪，纯简报层升级，产出即时可用

- [ ] **D4. 财报日历**
  - [ ] 接入个股财报发布日历（Yahoo Finance `earnings_dates` / akshare A 股财报预约披露）
  - [ ] 早间简报标注当日/本周持仓标的财报日期
  - [ ] 可选：注入财报相关 RSS 新闻摘要
  - **依赖**：无（`market_data.py` 已就绪）
  - **为什么优先**：独立模块、为 D7 哨兵提供底层数据

### 🟢 第三优先：交互与体验

- [ ] **飞书对话机器人** — 群内 @机器人「现在偏离度多少？」「XX 标的现价？」
- [ ] **飞书仪表盘** — 大类权重饼图、市值趋势

- [ ] **简报 AI 解读效果抽查** — 回看近期飞书群推送，调整 Prompt 参数
  - **类型**：纯调参 + review，低投入

- [ ] **底仓路由实盘验证** — A 股 ETF/港股/美股各一只，验证 `price_updater` 智能路由全品种跑通
  - **类型**：操作验证（非代码开发）

### ⚪ 第四优先：远期（择机）

- [ ] **D6.5 雷达深度分析** — 对有信号的标的用 Tavily/华尔街见闻搜索相关新闻，LLM 输出 2-3 句展开分析
  - **依赖**：D6 雷达观测表先跑一段时间，积累实际体验

- [ ] **D5. 宏观日历增强** — 按持仓自定义影响级别（非通用 ForexFactory 分级）；事件→持仓映射改为可配置文件（`config/sensitivity.yaml`）

- [ ] **D7. 全天候哨兵（自动断路器）** ⏸️ 暂缓
  - [ ] 深度融合 D2 宏观日历 + D3 RSS 纯净流 + 华尔街见闻 API
  - [ ] LLM 语义解析：提取「产能过剩」「资本开支骤降」「需求疲软」等断路器关键词
  - [ ] 飞书底仓表 + 雷达表新增 `Logic_Broken` 布尔字段
  - [ ] 命中时自动标记 True + 飞书告警卡片
  - **依赖**：D3（RSS）✅ + D4（财报日历），需积累一段时间的 RSS+财报数据

- [ ] **策略回测** — 需先积累底仓快照数据
- [ ] **行业基本面研报**
- [ ] **模拟盘** — `ENV=paper`
- [ ] **IBKR** — 券商字段 + 汇率

---

## 🏗️ 架构笔记

- **行情入口**：`src/market_data.py`（A/港/美股 ETF + 三大指数 + VIX + 美债收益率，yfinance 优先/akshare 兜底）
- **策略入口**：`src/strategy.py`（唯一真源，LLM 只能引用不能推翻）
- **趋势检测**：`price_updater.py` → 飞书「趋势」字段 → strategy 自动读
- **成本/份额**：`pending_resolver.py` → 加权平均法 → 飞书「成本均价」「持仓份额」
- **收益率/市值**：飞书公式自动计算
- **节假日熔断**：`src/holiday_gate.py`
- **宏观日历**：`src/macro_calendar.py`（ForexFactory → 筛选 → 敏感度映射 → 简报注入）
- **雷达观测**：`src/radar.py` → 飞书「雷达观测表」→ 双信号 + LLM 解读 → 简报注入
- **国际资讯**：`src/global_news.py` → 3 RSS → LLM 匹配翻译去重 → 简报注入
- **哨兵**：`src/sentinel.py`（暂缓 → 飞书 `Logic_Broken` 字段）
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
python -m src.radar --dry-run      # 只算不写雷达表
python -m src.radar                # 完整雷达扫描 + 写回飞书
python -m src.global_news           # 国际 RSS 流水线
python -m src.global_news --dry-run # 只抓不译
```
