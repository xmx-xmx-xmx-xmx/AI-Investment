# TODO —— AI 量化投资系统开发路线图

> 最后更新：2026-06-30
> 当前阶段：核心系统+飞书机器人双向通道完成，进入机器人扩展+微调阶段

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
| 飞书机器人 | `api/index.py` | Render FastAPI：巡航 + LLM 问答 |

---

## 📋 剩余待办 —— 按重要性排列

### 问题
-  [ ] **1** 现在A股场外基金被我手动归为A股资产，但代码里似乎有往飞书内填数据创建的“基金”分组。思考这部分分组怎么解决，因为除了场外，未来也可能买入A股的场内ETFs。
- [ ] **IBKR** — 我在IBKR试着买入了一些港股ETF，代码里/数据库是否有需要修改的地方，比如港币的汇率处理？

### 🟡 第二优先：机器人扩展

- [ ] **按需快报与自选股管理 (On-Demand Commands)**

  - 映射指令：@机器人 雷达 / @机器人 早报 / @机器人 观察 [代码] / @机器人 取消观察 [代码]。

  - 操作：直接复用现有的 radar.py 和 feishu_client.create_record，在 index.py 中添加路由。

- [ ] **快速记账**（`@机器人 买入 [名称] [金额]`）
  - 写入交易流水表（pending 状态），替代 iPhone 快捷指令的备选入口
  - **依赖**：`feishu_client.create_record` 已就绪。难点在非结构化输入解析

### 🟢 第三优先：功能增强 + prompt增强

- [ ] **D1. 宏观日历增强** — 事件→持仓映射改为可配置文件 `config/sensitivity.yaml`
  - **依赖**：`macro_calendar.py` 已就绪，纯重构

- [ ] **D2. prompt修改** — 回看飞书推送，调整 prompt 参数（max_tokens / temperature）
  - **类型**：纯调参，低投入，可融入先进的投资经验

- [ ] **D3. 飞书仪表盘** — 大类权重饼图、市值趋势（飞书 AI 辅助）

### ⚪ 第四优先：远期择机

- [ ] **D4 雷达深度分析、行业基本面研报** — 对信号标的用 Tavily 搜索相关新闻，LLM 输出展开分析；结合用户持仓，为用户提供某个关注的行业的深度研报
  - **依赖**：D6 雷达先跑一段时间

- [ ] **D5. 全天候哨兵（自动断路器）** ⏸️ 暂缓
  - 融合 D2+D3+D4 → LLM 语义提取 → Logic_Broken 标记 → 告警
  - **依赖**：需积累 RSS+财报数据

- [ ] **D6. Scriptable iOS 桌面小组件** ⏸️ 远期
  - CI 产出 `widget-data.json` → GitHub Pages → Scriptable 3 尺寸小组件
  - 不需要服务器，不需要新域名

- [ ] **D7. 策略回测** — 需先积累底仓快照数据
- [ ] **D8. 模拟盘** — `ENV=paper`

---

## 🏗️ 架构笔记

- **行情**：`market_data.py`
- **策略**：`strategy.py`（唯一真源）
- **简报**：`briefing.py`（7 时段 + 周报）
- **雷达**：`radar.py`（全量扫描 → 双信号 + LLM）
- **国际**：`global_news.py`（4 RSS → LLM 匹配翻译）
- **财报**：`earnings_calendar.py`（yfinance → 早间+周报）
- **宏观**：`macro_calendar.py`（ForexFactory → 敏感度映射）
- **机器人**：`api/index.py`（Vercel Render → 巡航 + LLM 问答 + 指令路由）
- **LLM**：`llm.py` + `prompt_templates.py`
- **哨兵/小组件/回测**：远期

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
uvicorn api.index:app --reload

# 飞书群机器人（@AI投顾）
#   巡航 / 状态 / 仓位     → 实时仓位健康报告
#   任意投资问题            → LLM 结合持仓+雷达+新闻 智能问答
```
