# TODO —— AI 量化投资系统开发路线图

> 最后更新：2026-06-15
> 当前阶段：核心系统全面上线，四时段简报+双卡日报全自动运行

---

## 📊 核心系统 —— 全部完成 ✅

| 模块 | 说明 | 状态 |
|------|------|------|
| market_data | 行情抓取（akshare+yfinance+Yahoo直连，三源） | ✅ |
| feishu_client | 飞书 SDK 读写封装 | ✅ |
| strategy | Python 策略中枢（4 条纪律代码锁死） | ✅ |
| advisor | AI 完整报告（XML Prompt v2.0 + 思想钢印） | ✅ |
| price_updater | 现价自动更新 + 自动趋势检测 | ✅ |
| pending_resolver | 基金净值确认 + T 日日历计算 + 份额自动写回 | ✅ |
| holiday_gate | 节假日熔断（XSHG/X N Y S 双日历） | ✅ |
| news_fetcher | 金十/华尔街见闻免费源 + Tavily 备用 | ✅ |
| notify | 飞书群双卡推送（数据卡 + AI 分析卡） | ✅ |
| briefing | 六时段简报（含周末 + 节假日熔断 + AI 解读） | ✅ |
| daily-run.yml | GitHub Actions 六时段 cron + 手动触发 | ✅ |
| auto_bill_parser | OCR 票据识别（XML Prompt v2.0 + Few-Shot） | ✅ |
| iPhone 快捷指令记账 | 拍照 OCR → 纯文本 → LLM → 飞书交易流水表 | ✅ |
| security | 清硬编码 Key + .gitignore + .env | ✅ |

---

## 📋 剩余待办 —— 按优先级排列

### 🔴 第一优先：全球高价值数据源矩阵（High-Signal Data Architecture）
  核心理念：彻底摒弃低价值、高噪音的二手资讯。依托 GitHub Actions 的海外原生网络优势，打造“国内华尔街见闻 + 国际顶级财经 API/RSS”的混合双擎。在云端由大模型完成数据的【提纯、去噪、翻译】后，将高密度策略指令传回国内。

  [ ] D1. 全球行情直连通道（无墙无延迟）
    [ ] 激活 yfinance 全量潜力：利用 GitHub 物理环境，直连获取美股、港股、VIX 恐慌指数、美债收益率等核心指标，替代国内易超时的备用源。
    [ ] 底仓路由实盘验证：在飞书加入场内 A 股 ETF、港股（如小米）、美股 ETF 各一只，跑通验证 price_updater 的智能路由逻辑。
    [ ] 🛡️ 容错机制兜底（严禁 AI 盲猜代码）：在 pending_resolver.py 中建立本地别名映射字典（Mapping Dictionary）。如果飞书与字典中均无对应代码，必须触发警告跳过，严禁让 AI 联网搜索基金代码，确保底层账本 100% 准确。

  [ ] D2. 国内高阶宏观日历（精准打击核心事件）
    [ ] 接入华尔街见闻 API：调用 api-one.wallstcn.com/apiv1/finance/calendar，彻底弃用噪音极大的 akshare 百度宏观接口。
    [ ] 事件星级过滤：只抓取“三星级及以上”的核弹级宏观事件（如 CPI 发布、美联储议息、央行降准/LPR 调整）。
    [ ] 简报注入：在早间简报增加「📅 今日宏观日历」模块，并在 Prompt 中强制要求 AI 结合当日重磅数据给出波动预警。

  [ ] D3. 国际权威源与云端预处理（打破信息茧房）
    [ ] 构建 RSS 纯净信息流：新建 src/global_news.py，优先使用 feedparser 库接入对开发者友好的 Yahoo Finance、Reuters 等官方 XML 源（避开 Bloomberg/WSJ 等高强反爬机制）。
    [ ] 华尔街机构级 API 接入：注册并接入 Finnhub.io 或 Alpha Vantage 的免费 General News API，获取英文原生的一手投行简报。
    [ ] LLM 云端预处理中枢（自带 Token 熔断）：在 Actions 流程中直接调用大模型阅读英文长文，完成“核心逻辑提取”与“专业中文化”。必须设置抓取条数上限，防止资讯井喷导致 LLM 上下文窗口溢出。

### 🟡 第二优先：洞察与报告增强

- [ ] **简报 AI 解读效果回看**
  - [ ] 抽查最近 3 天的飞书群推送，看 AI 解读是否真的在"结合持仓分析"
  - [ ] 调整 Prompt 参数（temperature/max_tokens）优化输出质量
  - [ ] 考虑将 AI 解读的模型换成更强的（如换到 DeepSeek V3 或其他）

- [ ] **周报生成** — 每周日 20:00 的周末前瞻升级为完整周报
  - [ ] 内容：本周偏离度变化趋势、收益率对比（你的持仓 vs 基准）、AI 周度总结
  - [ ] 新建 `src/weekly_report.py` 或扩展现有 `briefing.py` 的 `sun_evening` 模式

### 🟢 第三优先：交互与体验

- [ ] **飞书对话机器人** — 群内 @机器人 查询「现在偏离度多少？」「该不该加仓？」
- [*] **D1 数据初始化** — 底仓表手动录入全部真实持仓 【除港股外均已补齐】

### ⚪ 第四优先：策略增强（择机）

- [ ] **P3-1 策略参数回测** — ±3% vs ±5% vs ±10% 偏离度阈值历史表现对比
  - [ ] 依赖：需要积累至少 1 个月的底仓快照数据（目前还没有快照机制）
  - [ ] 建议：先建一个每日自动存档底仓表的功能，积累数据后再做回测

- [ ] **P3-2 行业基本面研报** — 从开源项目提取个股/行业分析逻辑
- [ ] **C3 飞书仪表盘** — 大类权重饼图、市值趋势、盈亏排行
- [ ] **C5 模拟盘模式** — `ENV=paper`，独立模拟表
- [ ] **C6 IBKR 海外持仓** — 券商字段 + 汇率换算

---

## 🏗️ 架构笔记

- **行情入口**：`src/market_data.py`
- **策略入口**：`src/strategy.py`（唯一真源，LLM 只能引用不能推翻）
- **趋势检测**：`price_updater.py` → 飞书「趋势」字段 → strategy 自动读取
- **节假日熔断**：`src/holiday_gate.py`（XSHG 中国 + XNYS 美国）
- **飞书入口**：`src/feishu_client.py`（SDK）+ `src/notify.py`（Webhook）
- **端侧记账**：iPhone 快捷指令 → iOS 本地 OCR → 纯文本 → LLM → 飞书交易流水表
- **环境区分**：`ENV=dev/paper/prod`（未来）

---

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
