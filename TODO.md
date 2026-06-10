# TODO —— AI 量化投资系统开发路线图

> 最后更新：2026-06-11 凌晨
> 当前阶段：P0 策略大脑已完成，P1 多时段简报待启动

---

## Route A ✅ 单机版（已完成）

- [x] `src/market_data.py` — 行情抓取层（akshare + yfinance + Yahoo 直连，三源 fallback）
- [x] `src/feishu_client.py` — 飞书 SDK 读写封装
- [x] `src/advisor.py` — 从飞书底仓表读持仓 → VIX → 搜新闻 → 偏离度计算 → AI 报告
- [x] `test_mvp.py` — 冒烟测试通过
- [x] 飞书多维表格「AI投资数据库」两张表（交易流水表 + 底仓表）
- [x] 底仓表「市值」公式字段、「价格更新日期」字段、「趋势」字段
- [x] 资产大类五类（美股/A股/港股/避险商品/固收资产）

---

## Route B 🟡 剩安全 & 部署 & 清理 & 资讯多元化

### B1. 安全问题（上 Git 前必须做）

- [ ] **清掉所有硬编码 API Key** — 涉及 6 个文件
- [ ] **加 `.gitignore`** — `.env`、`data/`、`logs/`、`__pycache__/`、`_legacy_backup/`
- [ ] **`.env.example` 清掉真实值**

### B2. 现价自动更新 ✅

- [x] `src/price_updater.py` — 场外基金/ETF/港股/美股智能路由
- [x] VIX 三源 fallback + 港股行情 +「价格更新日期」字段
- [x] 🆕 **自动趋势检测** — 5 日净值趋势（左侧下跌/右侧企稳/横盘震荡），price_updater 自动写入「趋势」字段

### B3. 消息推送 ✅

- [x] `src/notify.py` — 飞书群双卡片（数据卡 + AI 分析卡）

### B4. GitHub 部署

- [ ] 创建仓库 → 配置 Secrets → 更新 `daily-run.yml` → 首次 workflow_dispatch

### B5. 代码清理

- [ ] `src/market_brief.py` 重构 — 复用 `market_data.py` + SiliconFlow
- [ ] `src/auto_bill_parser.py` status 逻辑 — 净值/份额不为空时写 `completed`

### B6. 资讯抓取引擎

- [x] `src/news_fetcher.py` — Tavily（主）+ SearXNG（兜底）+ 合并搜索（1 credit/次）
- [ ] **新闻源多元化** — 加免费定向源彻底省 credits 

### B7. 持仓+资讯→智能建议 ✅

---

## Route C 🟡 进行中

### ✅ P0: 策略大脑 —— Python 硬编码核心护栏（已完成）

> 核心理念：Python 死算数字、LLM 只负责翻译和安抚——绝不反过来。

- [x] **P0-1 `src/strategy.py`** — 策略中枢（260 行）
  - [x] 资产大类目标权重 + 阶梯阈值（<-10%/-5%/+5%/+10%）
  - [x] `judge(portfolio) -> dict` 显式信号输出
  - [x] 四条纪律全部代码锁死：
    - [x] ① 阶梯阈值：`TRIGGER_STRONG_BUY` / `TRIGGER_BUY` / `HOLD_AND_WAIT` / `TRIGGER_SELL`
    - [x] ② 增量资金优先：`priority_target` 指向负偏离最大类
    - [x] ③ 长底仓永不卖出 + 自然稀释策略
    - [x] ④ 冷却期检查：读飞书交易流水表，3 天内同大类不重复建议加仓
  - [x] 🆕 **防飞刀自动拦截**：读取 price_updater 写入的「趋势」字段
    - 「左侧下跌」→ 拦截 BUY，等待右侧企稳
    - 「右侧企稳」→ 放行 BUY + 提示入场信号
    - 「横盘震荡」→ 不拦，按原信号执行
  - [x] 7 只真实持仓全部验证通过（当前全线左侧下跌，信号正确降级为 HOLD）

- [x] **P0-2 思想钢印注入** — `advisor.py` Prompt 改造
  - [x] Prompt 头部强制包裹 `strategy.judge()` 的死结论
  - [x] LLM 角色从「分析者」变为「翻译者」——只能解读 Python 结论，不能反驳
  - [x] 心理防御数据注入（闲钱、无杠杆、回归周期 2-3 月）
  - [x] 全链路跑通验证：飞书→VIX→新闻→偏离度→策略中枢→AI 报告

---

### 🟡 P1: 时间流水线 —— 24 小时多时段简报系统

> 理念：不同时间点，不同简报内容。中午看异动，下午看指令，晚上看美股。

- [ ] **P1-1 多时段简报路由** — 新建 `src/briefing.py`

  - [ ] ☀️ **11:30 午间热身**
    - 内容：上午 A 股/港股异动 + 美股期货亚洲盘走势
    - 实现：`market_data.py` + `news_fetcher.py`
  - [ ] ⚡ **14:30 收盘前终极指令**
    - 内容：全链路打通（price_updater → strategy.judge() → 硬核信号）
    - 这是全天最重要的简报，Python 裁判结论必须零 AI 干扰
  - [ ] 🌆 **14:45 美股夜盘前瞻**
    - 内容：美股期货涨跌 + CPI/非农/美联储动向
    - 来源：news_fetcher + Yahoo 美股期货数据
  - [ ] 🛏️ **次日 08:00 美股收盘复盘**
    - 内容：前夜美股大涨/大跌原因提纯 + 美股仓位市值更新
    - 来源：price_updater + news_fetcher

- [ ] **P1-2 `daily-run.yml` 改造** — 添加 4 个 cron，每个传入不同 `--mode` 参数

---

### 🟢 P2: 交互与运行平台

- [ ] **P2-1 iOS Bark 联动（可选）** — 14:30 终极指令同步推送到 iPhone 锁屏
- [ ] **P2-2 iPhone Action Button** — 快捷指令「记一笔 / 看偏离度」
- [ ] **P2-3 阿里云函数入口** — `src/cloud_handler.py`，解决国内直连 GitHub 偶尔被墙
- [ ] **P2-4 手机 OCR 录入** — 截图 → OCR → 飞书交易流水表

---

### ⚪ P3: 开源代码库抄作业（择机）

- [ ] **P3-1 策略参数回测** — ±3% vs ±5% 阈值对比、再平衡频率优化
- [ ] **P3-2 行业基本面研报** — 从开源项目提取个股/行业分析逻辑

---

## 已有 Route C 模块（与 P0-P3 协同）

| 模块 | 内容 | 状态 |
|------|------|------|
| C3 仪表盘 | 飞书图表：大类权重饼图、市值趋势、盈亏排行 | 待启动 |
| C5 模拟盘模式 | `ENV=paper`，独立模拟表 | 待启动 |
| C6 IBKR 海外持仓 | 券商字段 + 汇率换算 | 待启动 |
| C7 多币种 & 汇率 | 汇率自动获取 | 待启动 |

---

## 🏗️ 架构笔记

- **行情入口**：`src/market_data.py`
- **策略入口**：`src/strategy.py`（单一真源，LLM 只能引用不能推翻）
- **趋势检测**：`price_updater.py` → 飞书「趋势」字段 → strategy 自动读取，全自动免手动
- **飞书入口**：`src/feishu_client.py`（SDK）+ `src/notify.py`（Webhook 推送）
- **环境区分**：未来 `ENV=dev/paper/prod`

---

## 📊 项目进度总览

| 阶段 | 已完成 | 待做 |
|------|--------|------|
| Route A | 7/7 ✅ | 0 |
| Route B | 3/7 | B1(安全) B4(部署) B5(清理) |
| P0 策略大脑 | 2/2 ✅ | 0 |
| P1 多时段简报 | 0/2 | P1-1 P1-2 |
| P2 交互平台 | 0/4 | 全部 |
| P3 标本库清算 | 0/2 | 全部 |

## 📋 下次开发建议

1. 🟡 **P1 多时段简报** — 做大价值：不同时间看不同简报（午间看异动、收盘看指令、睡前看美股）
2. 🟡 **【实用】新闻源多元化** — 省 Tavily credits
3. python -m src.notify 命令得到的操作要点还没有按照第二条消息根据strategy得到的内容做归纳？【这里要梳理一下逻辑，或者第一条消息的操作要点直接去掉】

---

## 当前可用命令

```bash
python -m src.price_updater        # 更新现价 + 自动检测趋势 → 飞书
python -m src.advisor              # 飞书→VIX→新闻→偏离度→策略中枢→AI报告（终端）
python -m src.notify               # 完整日报推送到飞书群（数据卡 + AI 分析卡）
python -m src.notify --data-only   # 仅数据卡，不调 LLM
python -m src.notify --dry-run     # 预览不发送
python test_mvp.py                 # 冒烟测试
```
