# TODO —— AI 量化投资系统开发路线图

> 最后更新：2026-06-12
> 当前阶段：P0 策略大脑已完成，准备启动周末端侧记账与国际资讯源接入

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

- [x] **清掉所有硬编码 API Key** — 涉及 6 个文件
- [x] **加 `.gitignore`** — `.env`、`data/`、`logs/`、`__pycache__/`、`_legacy_backup/`
- [x] **`.env.example` 清掉真实值**

### B2. 现价自动更新 ✅

- [x] `src/price_updater.py` — 场外基金/ETF/港股/美股智能路由
- [x] VIX 三源 fallback + 港股行情 +「价格更新日期」字段
- [x] 🆕 **自动趋势检测** — 5 日净值趋势（左侧下跌/右侧企稳/横盘震荡），price_updater 自动写入「趋势」字段

### B3. 消息推送 ✅

- [x] `src/notify.py` — 飞书群双卡片（数据卡 + AI 分析卡）

### B4. GitHub 部署

- [x] 创建仓库 → 配置 Secrets → 更新 `daily-run.yml` → 首次 workflow_dispatch

### B5. 代码清理

- [ ] `src/market_brief.py` 重构 — 复用 `market_data.py` + SiliconFlow
- [ ] `src/auto_bill_parser.py` status 逻辑 — 净值/份额不为空时写 `completed`

### B6. 资讯抓取引擎

- [x] `src/news_fetcher.py` — Tavily（主）+ SearXNG（兜底）+ 合并搜索（1 credit/次）
- [ ] **新闻源多元化** — 见周末进阶任务 D3，引入免翻墙国际源

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

### 🟡 P1: 时间流水线 —— 24 小时多时段简报系统 【部分完成，推送时间bug未修】

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
- [ ] **P2-3 阿里云函数入口** — `src/cloud_handler.py`，端侧记账流 + 国内直连
- [ ] **P2-4 手机 OCR 录入** — 截图 → OCR → 飞书交易流水表（见 D2 重构方案）

### 🆕 D1: 数据初始化 —— 一键快照录入（抛弃历史包袱）

> 不为历史 100/200 元的细碎交易浪费时间。系统只面向当下与未来。

- [ ] **飞书「底仓表」一次性手动录入**
  - [ ] 录入当前所有持仓的：名称、代码、资产大类、当前总持有份额、成本均价、现价
  - [ ] 市值由公式字段自动计算
  - [ ] 结算货币、标签（长期底仓/观察仓/网格标的/左侧观望）手动选择
- [ ] **飞书「交易流水表」保持留空**
  - [ ] 从本周末的下一笔新交易开始记录
  - [ ] 不对 iOS 快捷指令上线前的历史碎单做任何追溯

### 🆕 D2: 周末攻坚战 —— iPhone 快捷指令 → 纯文本 OCR → 云函数记账流

> 核心思路：放弃上传截图给云端 VL 模型（慢且贵），改为 iOS 本地 OCR + 云端纯文本 LLM。

- [ ] **D2-1 iPhone 快捷指令（需手动在 iPhone 完成）**
  - [ ] 长按 Action Button → 弹出菜单「记一笔」
  - [ ] 选「记一笔」→ 系统截图
  - [ ] 快捷指令内置「提取文本」功能获取图片内粗糙文本（本地、免费、秒出）
  - [ ] 将纯文本 POST 给云端函数
  - [ ] 云端返回 JSON 解析成功后 → 快捷指令弹窗提示「已记录：XX基金 买入 ¥100」

- [ ] **D2-2 云端纯文本 LLM 入口（`src/cloud_handler.py`）**
  - [ ] 接收 POST 的纯文本（非图片）
  - [ ] 调用纯文本 LLM（SiliconFlow `Qwen3-30B`，使用新版 Prompt 3）提取 JSON
  - [ ] 写入飞书「交易流水表」
  - [ ] 返回确认 JSON 给快捷指令

- [ ] **D2-3 架构决策：iOS 本地 OCR vs 云端 VL 模型**

  | 方案 | 延迟 | 成本 | 适用 |
  |------|------|------|------|
  | iOS 本地 `提取文本`（选） | <1 秒 | 免费 | 基金截图字体清晰 |
  | 云端 VL 模型 | 3-8 秒 | ¥0.01/次 | 手写/模糊票据兜底 |
  
  优先使用 iOS 本地 OCR。失败时 fallback 到云端 VL（Prompt 3 已同时适配两种模式）。

### 🆕 D3: 周末进阶拓展 —— 国际化资讯直连（零成本免翻墙）

> 核心思路：利用 GitHub Actions 原生的海外 IP 环境，绕开国内“二手翻译”自媒体，直接白嫖国际顶级一手财经数据，并通过飞书回传国内。

- [ ] **D3-1 引入国际纯净 RSS 信息流**
  - [ ] 使用 Python `feedparser` 库抓取 CNBC、路透社 (Reuters)、华尔街日报 (WSJ) 的官方 RSS XML 接口。
  - [ ] 获取全英文一手宏观新闻摘要，直接喂给 LLM 翻译并进行持仓映射。
  - [ ] 降低对 Tavily 的搜索依赖（节省 credits），大幅提升宏观数据的客观性和纯净度。
- [ ] **D3-2 价格与基本面数据源升级**
  - [ ] 完善 `yfinance` 获取美股、港股 (如 1810.HK) 实时报价的逻辑。
  - [ ] 注册 Finnhub.io 获取免费 API Key，接入其 `General News` 接口作为备用高质量英文资讯库。
- [ ] **D3-3 固化“数据走私”架构**
  - [ ] 确保网络运行链路：`海外 API -> GitHub Actions (获取与计算) -> 飞书 API -> 国内手机接收`。
  - [ ] 在 `market_data.py` 和 `news_fetcher.py` 中做好本地开发时的网络 `try-except` 异常捕获，防止在国内本地测试时因直连海外源超时而阻断程序运行。
---

### ⚪ P3: 开源代码库抄作业（择机）

- [ ] **P3-1 策略参数回测** — ±3% vs ±5% 阈值对比、再平衡频率优化
- [ ] **P3-2 行业基本面研报** — 从开源项目提取个股/行业分析逻辑

---


## 🏗️ 架构笔记

- **行情入口**：`src/market_data.py`（加入国际源 RSS 与 Finnhub）
- **策略入口**：`src/strategy.py`（单一真源，LLM 只能引用不能推翻）
- **趋势检测**：`price_updater.py` → 飞书「趋势」字段 → strategy 自动读取，全自动免手动
- **飞书入口**：`src/feishu_client.py`（SDK）+ `src/notify.py`（Webhook 推送）
- **端侧记账**：iPhone 快捷指令 → iOS 本地 OCR → 纯文本 → 云函数 `src/cloud_handler.py` → 飞书
- **环境区分**：未来 `ENV=dev/paper/prod`

---

## 📊 项目进度总览

| 阶段 | 已完成 | 待做 |
|------|--------|------|
| Route A | 7/7 ✅ | 0 |
| Route B | 6/7 | B5(清理) |
| P0 策略大脑 | 2/2 ✅ | 0 |
| P1 多时段简报 | 0/2 | 全部 |
| P2 交互平台 | 0/4 | 全部 |
| D1 数据初始化 | 0/2 | 本周末手动录入快照 |
| D2 iPhone 记账流 | 0/2 | 本周末攻坚战 |
| D3 国际信息源直连 | 0/3 | 本周末进阶拓展 |
| P3 标本库清算 | 0/2 | 全部 |

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
