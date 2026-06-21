# TODO —— AI 量化投资系统开发路线图

> 最后更新：2026-06-21
> 当前阶段：核心系统全面上线，D1 全球行情直连 + D2 宏观日历已完成

---

## ✅ 已归档（全部完成）

| 模块 | 文件 | 概要 |
|------|------|------|
| 行情抓取 | `market_data.py` | akshare/yfinance 双源，覆盖 A 股/港股/美股/VIX/美股三大指数/美债收益率 |
| 飞书 SDK | `feishu_client.py` | bitable 读写封装 |
| 策略中枢 | `strategy.py` | 阶梯阈值 / 增量优先 / 长底仓锁定 / 冷却期 / 防飞刀拦截 |
| AI 报告 | `advisor.py` | XML 结构化 Prompt v2.0 + 思想钢印 |
| 现价更新 | `price_updater.py` | 智能路由(基金/ETF/港股/美股) + 自动趋势检测(5 日净值) |
| Pending 确认 | `pending_resolver.py` | T 日日历 + 净值抓取 + 加权平均成本 + QDII 懒加载 |
| 节假日熔断 | `holiday_gate.py` | XSHG(中国) + XNYS(美国) 双日历 |
| 资讯引擎 | `news_fetcher.py` | 金十数据 + 华尔街见闻(免费) + Tavily(1 credit) + 关键词筛选 |
| 消息推送 | `notify.py` | 飞书群双卡片(数据卡 + AI 分析卡) |
| 多时段简报 | `briefing.py` | 六时段 + 节假日熔断 + AI 解读 + 持仓一览 + 宏观日历注入 |
| 宏观日历 | `macro_calendar.py` | ForexFactory 免费 JSON 接口 → 国家/影响级别/关键词三层筛选 → 7 组敏感度细粒度持仓映射 → 早间简报 + 周日简报注入 |
| LLM 客户端 | `llm.py` | 共享 LLM 客户端工厂（单一配置点，所有模块统一调 GPT/SiliconFlow） |
| 常量 | `constants.py` | 共享常量（资产大类枚举、信号类型等） |
| OCR 票据 | `auto_bill_parser.py` | XML Prompt v2.0 + Few-Shot 防越狱 |
| iPhone 记账 | 快捷指令 | 拍照 → iOS OCR → LLM → 飞书交易流水表 |
| 安全 | 全局 | 硬编码 Key 全清 + .gitignore + .env |
| 飞书表结构 | 底仓/交易流水 | 份额/成本均价/现价/(市值公式)/(收益率公式) + 趋势 + 标签 |
| 投资纪律 | `strategy.py` / `advisor.py` | **固收 50% / 美股 25% / A 股 10% / 港股 5% / 避险 10%** |
| 外部触发 | `daily-run.yml` | Cron 已移除，飞书 / workflow_dispatch 手动按需触发 |

---

## 📋 剩余待办 —— 按优先级排列

### 🔴 第一优先：高价值数据源矩阵 + 策略闸门

> 当前核心任务：铺完国际信息管道 + 建立「不亮绿灯就不动手」的入场闸门。

- [x] **D1. 全球行情直连** ✅
  - [x] yfinance 全量激活：美股三大指数(`^DJI`/`^GSPC`/`^IXIC`) + VIX + 美债收益率(2Y/10Y/利差)，yfinance 优先/akshare 兜底
  - [x] 🛡️ `pending_resolver.py` 已有 FUND_NAME_MAPPING 映射字典（已完成）

- [ ] **D3. 国际 RSS 信息流**
  - [ ] 新建 `src/global_news.py`，`feedparser` 接入 Yahoo Finance / Reuters RSS
  - [ ] LLM 云端翻译 + 摘要（英文长文 → 中文核心）
  - [ ] 抓取条数上限 + Token 熔断，防上下文窗口溢出
  - **依赖**：无（`llm.py`、`news_fetcher.py` 已就绪）
  - **为什么在这一层**：国际一手信息是所有英文语义分析的基础，D7 哨兵依赖它。

- [ ] **D6. 雷达观测表（隔离区状态机）**
  - [ ] 飞书新建「雷达观测表」：标的代码、名称、资产大类、当前价、偏离度%、趋势、观测状态（观察中/击球区/已入场/已放弃）、入库日期
  - [ ] 新建 `src/radar.py`：`scan_radar()` — 逐只抓现价 → 复用 strategy 偏离度 + 趋势算式 → 判定是否进入"击球区"
  - [ ] 简报注入：早间/收盘简报追加「🔓 雷达解锁」区块，列出当日击球区标的
  - [ ] 状态流转：观察中 → 击球区（系统亮绿灯）→ 手动确认后移入底仓表 / 放弃
  - **依赖**：无（`market_data.py`、`strategy.py`、`feishu_client.py`、`briefing.py` 已就绪）
  - **解决的痛点**：剥夺主观操作权。高波动卫星仓位（MLCC、氟化工、存储芯片 ETF 等）只有系统判定真正进入击球区时才解锁，做到「系统不亮绿灯，手拿现金死等」。

### 🟡 第二优先：洞察与报告增强

> 让简报更聪明、让周报有闭环。

- [ ] **D4. 财报日历**
  - [ ] 接入个股财报发布日历（Yahoo Finance `earnings_dates` / akshare A 股财报预约披露）
  - [ ] 早间简报标注当日/本周持仓标的财报日期
  - [ ] 可选：D3 完成后注入财报相关 RSS 新闻摘要
  - **依赖**：无（`market_data.py` 已就绪）

- [ ] **周报** — 周日 `sun_evening` 升级为完整周报
  - [ ] 本周收益 vs 基准（SPY/沪深300）对比
  - [ ] 偏离度趋势图（文字描述 or 飞书图表）
  - [ ] 本周关键事件回顾（宏观日历已发生事件 + 对持仓的影响摘要）
  - **依赖**：`briefing.py`、`market_data.py`（D1 三大指数/美债已就绪）、`macro_calendar.py`

- [ ] **简报 AI 解读效果抽查** — 回看近期飞书群推送，调整 Prompt 参数
  - **依赖**：无（纯调参 + review）

- [ ] **底仓路由实盘验证** — A 股 ETF/港股/美股各一只，验证 `price_updater` 智能路由全品种跑通
  - **类型**：操作验证（非代码开发）

### 🟢 第三优先：交互与体验

- [ ] **飞书对话机器人** — 群内 @机器人「现在偏离度多少？」「XX 标的现价？」
- [ ] **飞书仪表盘** — 大类权重饼图、市值趋势

### ⚪ 第四优先：远期（择机）

- [ ] **D7. 全天候哨兵（自动断路器）** ⏸️ 暂缓
  - [ ] 深度融合 D2 宏观日历 + D3 RSS 纯净流 + 华尔街见闻 API
  - [ ] LLM 语义解析：提取「产能过剩」「资本开支骤降」「需求疲软」等断路器关键词
  - [ ] 飞书底仓表 + 雷达表新增 `Logic_Broken` 布尔字段
  - [ ] 命中时自动标记 True + 飞书告警卡片
  - **依赖**：硬依赖 D3（RSS）+ D4（财报日历），需要积累一段时间的 RSS 数据才有语义分析价值

- [ ] **D5. 宏观日历增强** — 按持仓自定义影响级别（非通用 ForexFactory 分级）；事件→持仓映射改为可配置文件（`config/sensitivity.yaml`）

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
- **雷达观测**：`src/radar.py`（规划中 → 飞书「雷达观测表」）
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
```
