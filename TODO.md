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

### 🔴 第一优先：数据源扩展

> 当前系统行情和新闻数据都够了，但缺少「今日宏观事件日历」（CPI 公布日、美联储议息日、央行降准等）。
> 另外目前底仓表的现价更新只支持场外基金，场内 ETF/港股/美股路由有代码但没实盘验证过。

- [ ] **宏观事件日历源** — 加一个免费的财经日历 API
  - [ ] 来源：华尔街见闻财经日历（`api-one.wallstcn.com/apiv1/finance/calendar`）或 akshare `news_economic_baidu`
  - [ ] 新建 `src/economic_calendar.py`：获取当日/次日的重要宏观事件（CPI、非农、央行决议等）
  - [ ] 注入到早间简报和夜盘前瞻——让 AI 解读时知道"今天有 CPI，注意波动"
  - [ ] 早间简报增加段落「📅 今日财经日历」

- [ ] **底仓表现价更新全覆盖**（当前只有场外基金实测通过）
  - [ ] 加一只场内 A 股 ETF 到飞书，验证 `price_updater` 的 `fetch_etf_price` 路由
  - [ ] 加一只港股到飞书，验证 `fetch_hk_price` 路由
  - [ ] 加一只美股 ETF 到飞书，验证 `fetch_us_price` 路由
  - [ ] 修复底仓表代码中的汇添富消费行业混合无标的代码问题（让它能正常识别或者跳过）

### 🔴 核心补充：国际一手资讯与行情直连（依托 GitHub Actions 原生海外环境）

> 核心理念：利用 GitHub 服务器在美国的物理优势，零成本直连全球顶级金融与新闻接口，在云端让 LLM 完成翻译和提纯后，只把轻量级的中文结论通过飞书 API 传回国内。

- [ ] **海外行情极速抓取（无墙无阻力）**
  - [ ] 彻底激活 `yfinance` 的全部潜力，替代之前为了防网络超时而设置的国内备用源。
  - [ ] 直连获取美股、港股、VIX 恐慌指数以及美债收益率等核心宏观指标，确保现价和趋势更新零延迟。

- [ ] **国际权威新闻 RSS 与 API 矩阵** — 新建/扩展 `src/global_news.py`
  - [ ] 接入 Bloomberg、路透 (Reuters)、华尔街日报 (WSJ) 的纯净 RSS XML 源（使用 Python `feedparser` 库）。
  - [ ] 注册并接入 Finnhub.io 或 Alpha Vantage 的免费 `General News` API，获取华尔街机构级别的一手简报。
  - [ ] 彻底摆脱国内二手财经媒体的“信息时差”和“情绪添油加醋”。

- [ ] **LLM 预处理中枢（云端翻译官）**
  - [ ] 在 GitHub Actions 运行环境内，直接调用大模型（如 DeepSeek V3）读取这批全英文的原始长篇研报和新闻。
  - [ ] 让大模型完成【提纯去噪】+【专业中文化翻译】。
  - [ ] 将翻译后的核心逻辑写入你的四时段简报路由中，随其他数据一起推送到飞书。

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
- [ ] **P2-1 iOS Bark 联动** — 14:30 收盘前指令同步推送到 iPhone 锁屏
- [ ] **P2-3 阿里云函数入口** — `src/cloud_handler.py`（如果后续国内直连 GitHub 不稳）
- [ ] **D1 数据初始化** — 底仓表手动录入全部真实持仓（你已经填了一部分，有空陆续补全）

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
