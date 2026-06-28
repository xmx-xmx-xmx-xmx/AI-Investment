# TODO —— AI 量化投资系统开发路线图

> 最后更新：2026-06-25
> 当前阶段：核心系统全面上线，进入交互体验 + 前瞻增强阶段

---

## ✅ 已归档（全部完成）

| 模块 | 文件 | 概要 |
|------|------|------|
| 行情抓取 | `market_data.py` | akshare/yfinance 双源，覆盖 A 股/港股/美股/VIX/美股三大指数/美债收益率 |
| 飞书 SDK | `feishu_client.py` | bitable 读写封装（含 `create_record`） |
| 策略中枢 | `strategy.py` | 仓位健康报告 / 长底仓锁定 / 防飞刀拦截 / 冷却期 |
| 现价更新 | `price_updater.py` | 智能路由(基金/ETF/港股/美股) + 趋势检测 + 日涨跌幅写入 |
| Pending 确认 | `pending_resolver.py` | T 日日历 + 净值抓取 + 新品自动建仓 + QDII 懒加载 |
| 节假日熔断 | `holiday_gate.py` | XSHG(中国) + XNYS(美国) 双日历 |
| 资讯引擎 | `news_fetcher.py` | 金十数据 + 华尔街见闻(免费) + Tavily(1 credit) + 关键词筛选 |
| 国际资讯 | `global_news.py` | 3 条 RSS → LLM 匹配翻译去重 → 简报「🌐 国际快讯」 |
| 财报日历 | `earnings_calendar.py` | 雷达表/底仓表美股个股 → yfinance 财报日期 → 早间+周报注入 |
| 消息推送 | `notify.py` | 飞书群双卡片(数据卡 + AI 分析卡) |
| 多时段简报 | `briefing.py` | 七时段 + 五区块周报 + AI 解读 + 持仓一览 + 宏观日历 + 雷达 + 市场基准 + 国际快讯 |
| 宏观日历 | `macro_calendar.py` | ForexFactory 免费 JSON → 国家/影响级别/关键词三层筛选 → 7 组敏感度映射 |
| 雷达观测 | `radar.py` | 底仓+雷达全量扫描 → 抄底/追涨双信号 → LLM 三行解读 → 简报注入 |
| LLM 客户端 | `llm.py` | 共享 LLM 客户端工厂（SiliconFlow） |
| OCR 票据 | `auto_bill_parser.py` | XML Prompt v2.0 + Few-Shot 防越狱 |
| iPhone 记账 | 快捷指令 | 拍照 → iOS OCR → LLM → 飞书交易流水表 |
| 安全 | 全局 | 硬编码 Key 全清 + .gitignore + .env |
| 飞书表结构 | 底仓/交易流水/雷达观测 | 4 表：份额/成本/现价/日涨跌幅 + 交易流水(含代码) + 雷达(双信号) |
| 投资纪律 | `strategy.py` / `advisor.py` | **固收 50% / 美股 25% / A 股 10% / 港股 5% / 避险 10%** |
| 外部触发 | `daily-run.yml` | 飞书 / workflow_dispatch 手动按需触发 |

---

## 📋 剩余待办 —— 按重要性排列

### 🟡 第二优先：前瞻增强

- [ ] **D5. 宏观日历增强** — 按持仓自定义影响级别（非通用 ForexFactory 分级）；事件→持仓映射改为可配置文件（`config/sensitivity.yaml`）
  - **依赖**：D2 宏观日历已就绪

- [ ] **D9. Prompt 工程重构** — 统一 LLM 提示词体系，提升分析深度
  - [ ] **投资宪法集中化**：新建 `src/prompt_templates.py`，所有 LLM 调用共享同一份投资纪律（目标权重 50/25/10/5/10、长底仓不卖、增量定投优先、无杠杆无短期压力）
  - [ ] **输入格式统一**：所有 prompt 统一为 `[系统角色] [投资铁律] [当前持仓] [宏观日历] [市场数据] [待分析资讯]` 六段式 XML 结构
  - [ ] **思维链 (CoT)**：在 `_ai_insight` 和 `_radar_insight` 的 prompt 尾部追加推理步骤指令，要求模型先推演再给结论（第一步宏观定调 → 第二步偏离度校验 → 第三步趋势确认 → 第四步结论），减少「和稀泥」废话
  - **依赖**：现有 LLM 调用点全部改造（`briefing.py._ai_insight`、`radar.py._radar_insight`、`global_news.py.match_and_translate`）
  - **不做的**：Few-Shot 示例（每次多烧数千 token，投入产出比低，30B 模型用 CoT 就够了）
  - **为什么优先**：不改任何数据管道，纯 prompt 层升级。当前所有 AI 解读用的是同一套松散 prompt，改造后回答质量和一致性会有明显提升

### 🟢 第三优先：交互与体验

- [ ] **飞书仪表盘** — 大类权重饼图、市值趋势（在飞书AI帮助下部分完成）

- [ ] **简报 AI 解读效果抽查** — 回看近期飞书群推送，调整 Prompt 参数
  - **类型**：纯调参 + review，低投入


- [ ] **D10. 飞书群聊智能投顾** — `bot_server.py` 从 MVP 复读机升级为多功能军师
  - **基础设施已完成**：Render FastAPI 已上线、飞书 Webhook 已拔通、LLM 客户端已就绪
  - [ ] **① 大盘实时巡航**（`@机器人 巡航` / `@机器人 状态`）：触发时调用 `strategy.judge().health_report` 实时计算偏离度，群内返回格式化仓位健康报告
  - [ ] **② 按需快报**（`@机器人 早报` / `@机器人 收盘` / `@机器人 美股`）：不等待定时推送，即时跑一次对应简报。`@机器人 雷达` 返回当前雷达信号列表
  - [ ] **③ 智能投顾问答**（非固定指令的任意提问，如「美光财报下周开了我该怎么办」）：自动路由到 `src/llm.py`，组装上下文（偏离度+宏观日历+雷达信号+持仓概况）→ LLM 思维链输出 → 回复。走 D9 统一 prompt 模板
  - [ ] **④ 雷达自选股动态管理**（`@机器人 观察 [代码/名称]` / `@机器人 取消观察 [代码/名称]`）：通过飞书 OpenAPI 直接对「雷达观测表」增删记录
  - [ ] **⑤ 快速记账**（`@机器人 买入/卖出 [名称] [金额]`）：写入交易流水表（pending 状态），替代 iPhone 快捷指令作为备选入口
  - **依赖**：`bot_server.py`（已部署）、`strategy.py` / `radar.py` / `briefing.py`（全部就绪）、`feishu_client.py`（已含 create_record / delete_record）
  - **为什么优先**：MVP 通道已验证通过，核心模块都在，本质是把已有的 `python -m src.xxx` 能力翻译成群聊指令


### ⚪ 第四优先：远期（择机）

- [ ] **D6.5 雷达深度分析** — 对有信号的标的用 Tavily/华尔街见闻搜索相关新闻，LLM 输出 2-3 句展开分析
  - **依赖**：D6 雷达先跑一段时间

- [ ] **D7. 全天候哨兵（自动断路器）** ⏸️ 暂缓
  - [ ] 深度融合 D2 宏观日历 + D3 RSS + 华尔街见闻 API
  - [ ] LLM 语义解析：提取「产能过剩」「资本开支骤降」等断路器关键词
  - [ ] 飞书底仓表 + 雷达表新增 `Logic_Broken` 布尔字段
  - [ ] 命中时自动标记 True + 飞书告警卡片
  - **依赖**：需积累 RSS+财报数据

- [ ] **D8. Scriptable iOS 桌面小组件** ⏸️ 远期
  - [ ] 用 GitHub Actions 每次简报运行后生成一个轻量 JSON 数据文件，部署到 GitHub Pages 作为静态 API
  - [ ] 在 Scriptable App 写 3 个 JS 小组件，从 GitHub Pages 拉 JSON 渲染
  - **小组件内容设计**：
    | 尺寸 | 展示内容 |
    |------|---------|
    | 小 (2×2) | 总市值 + 今日涨跌 + VIX 水平 |
    | 中 (4×2) | 前 5 持仓（名称/市值/日涨跌）+ 沪深300/标普/纳指基准 |
    | 大 (4×4) | 全资产大类仓位健康条 + 最新雷达信号 + 今日宏观事件 |
  - **技术路线**：CI 产出一个 `widget-data.json` → GitHub Pages 托管 → Scriptable `new Request()` 拉取 → 渲染 SwiftUI 风格小组件。不需要服务器，不需要新域名
  - **为什么放远期**：核心数据管道已完成，这是纯展示层。不影响投资决策质量，但能大幅减少打开飞书的频率

- [ ] **策略回测** — 需先积累底仓快照数据
- [ ] **行业基本面研报**
- [ ] **模拟盘** — `ENV=paper`
- [ ] **IBKR** — 券商字段 + 汇率

---

## 🏗️ 架构笔记1

- **行情入口**：`src/market_data.py`（A/港/美股 ETF + 三大指数 + VIX + 美债收益率，yfinance 优先/akshare 兜底）
- **策略入口**：`src/strategy.py`（唯一真源，LLM 只能引用不能推翻）
- **趋势检测**：`price_updater.py` → 飞书「趋势」字段 → strategy 自动读
- **成本/份额**：`pending_resolver.py` → 加权平均法 → 飞书「成本均价」「持仓份额」
- **收益率/市值**：飞书公式自动计算
- **节假日熔断**：`src/holiday_gate.py`
- **宏观日历**：`src/macro_calendar.py`（ForexFactory → 筛选 → 敏感度映射 → 简报注入）
- **雷达观测**：`src/radar.py` → 底仓+雷达全量扫描 → 双信号 + LLM 解读 → 简报注入
- **国际资讯**：`src/global_news.py` → 3 RSS → LLM 匹配翻译去重 → 简报注入
- **财报日历**：`src/earnings_calendar.py` → yfinance 财报日期 → 早间+周报注入
- **哨兵**：`src/sentinel.py`（暂缓）
- **智能投顾**：`bot_server.py`（Render FastAPI）→ 飞书群聊 @机器人 交互（巡航/问答/雷达管理/记账）
- **小组件**：Scriptable iOS → GitHub Pages JSON → 桌面总市值/雷达/宏观一览（远期）
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
python -m src.briefing sun_evening # 周报测试
python -m src.radar --dry-run      # 只算不写雷达表
python -m src.radar                # 完整雷达扫描 + 写回飞书
python -m src.global_news           # 国际 RSS 流水线
python -m src.global_news --dry-run # 只抓不译
uvicorn bot_server:app --reload     # 本地启动飞书 Bot 后端
```
