# AI 量化投资系统

> 不预测市场，只执行纪律。飞书多维表格 = 唯一数据库 + 展示看板。GitHub Actions（飞书 Bot 触发 + cron 兜底）零成本运行。规则引擎 + 资讯映射 + 多时段简报。

> 📌 **待办唯一真源：`TODO.md`** · **决策规则唯一来源：`docs/ACTION_RULES.md`** · 本 README 只讲"系统是什么、怎么跑"。

## 核心原则

1. **极简前端**：100% 依赖飞书多维表格作为数据库和看板，零 UI
2. **纪律驱动**：Python 死算偏离度，LLM 只做翻译和安抚，绝不反过来
3. **规则先行**：所有提醒/建议先定规则（`docs/ACTION_RULES.md`）再写代码；规则引擎纯本地、fail-silent，绝不拖垮简报
4. **零成本运行**：GitHub Actions（workflow_dispatch + 6 条 cron 兜底）+ Render 免费实例
5. **账本可审计**：结算回执 + 结算前快照 + 增量对账告警，算错必被告警看见

## 当前状态（2026-11-07）

| 维度 | 值 |
|---|---|
| 代码规模 | `src/` 39 模块（含 `briefing_modules/` 12 子模块），**35 个测试文件 / 738 用例全绿** |
| 目标权重 | **固收45 / 美股30 / A股5 / 港股10 / 避险10**（`src/constants.py` 唯一真源） |
| 分仓 | 长期底仓16 / 普通持有10 / 观察仓3（底仓表「标签」列） |
| 简报模式 | 默认 `compact`（金字塔架构：顶层 3 秒指令+定心丸+底线阈值；中层平淡日闲暇建议；底层要闻合并+紧凑折叠），支持 `--full` / GitHub 变量 `BRIEFING_STYLE` 随时切回完整版 |
| 规则引擎 | 规则1 同主题业绩差（周六） / 2 同类分位（周六） / 3 回本提醒 / 5 单只保险丝+止盈 / 6 资讯×持仓映射 |
| 决策审计 | 飞书「决策日志表」（`tblzyv0Y9NJxhYHS`）每时段全自动静默落盘系统结论、触发规则、总市值（资金盘子）与各大类偏离度 |
| 推送与日历可靠性 | `cron_guard` 双窗口（`DISPATCH_WINDOWS` + `FALLBACK_WINDOWS`）根治排队延迟跨界抢跑双推；A 股 2027+ 交易日历越界自动从新浪接口动态自愈；美股 fast_info 日期穿透守卫隔夜 K 线延迟 |
| Agent Skills | 仓库内置 `.agents/skills/`（8 个标准技能包）+ `docs/PROJECT_MEMORY.md` 长期工程记忆 |

## 项目结构

```
.
├── TODO.md                       # 待办唯一真源
├── AGENTS.md                     # 本地开发硬隔离规范与技能索引（CLAUDE.md 软链至此）
├── .agents/skills/               # 8 个内置 Agent Skills（跨 AI 助手开箱即用）
├── requirements.txt / pyproject.toml
├── src/                          # 核心业务代码（39 模块）
│   ├── briefing.py               # 简报统一调用门面（280 行，向后兼容 CLI 与猴子补丁）
│   ├── briefing_modules/         # 简报拆包架构（12 个单一职责子模块：headers/receipt/news/market/sector/valuation/signals/snapshots/narrator/slots/decision_log/pyramid）
│   ├── rules_engine.py           # 规则 1/3/5：同主题业绩差 / 回本提醒 / 单只保险丝与止盈
│   ├── peer_rank.py              # 规则 2：主动型近1年同类分位（akshare，周六周更）
│   ├── news_mapper.py            # 规则 6：资讯→主题→持仓映射（8 主题关键词表）
│   ├── reconcile.py              # 底仓增量对账告警（基准=结算前快照，只告警不改数）
│   ├── pending_resolver.py       # Pending 结算：T日净值 + convert 双腿 + 失败单排除
│   ├── strategy.py               # 策略中枢：仓位健康 + 长底仓锁定 + 防飞刀 + 冷却期与小额豁免
│   ├── radar.py                  # 雷达扫描 + 超配闸门（偏离≥+5pp 抑制买入信号）
│   ├── holiday_gate.py           # 节假日熔断：A股/港股/美股三市场 + 2027+ 动态日历自愈
│   ├── cron_guard.py             # cron 兜底：DISPATCH/FALLBACK 双时间窗 + 快照判重
│   ├── market_data.py            # 行情抓取（tenacity 轻重试 + fast_info 日期穿透守卫）
│   ├── net_guard.py              # akshare/yfinance 全量硬超时代理
│   ├── news_fetcher.py           # 资讯引擎：金十 + 华尔街见闻 + Tavily
│   ├── global_news.py / macro_calendar.py / earnings_calendar.py
│   ├── llm.py / notify.py / feishu_client.py / constants.py / classification.py
│   └── prompt_templates.py       # 投资宪法 + 六段式模板
├── bot_server.py                 # Render FastAPI → 飞书机器人（webhook + 加固）
├── tests/                        # 738 用例 / 35 个测试文件全绿
├── docs/                         # 活文档（见下）
└── docs/archive/                 # 已归档分析/设计文档
```

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env   # 填入真实 Key

# 常用命令（本地务必带 --dry-run，绝不触飞书 API/Webhook —— 见 CLAUDE.md 硬隔离）
python -m src.pending_resolver --dry-run
python -m src.briefing morning --dry-run          # 早间简报（默认 compact 精简版）
python -m src.briefing closing --dry-run --full   # 盘中指令（切换 full 完整版）
python -m src.briefing evening --dry-run          # 夜盘前瞻
python -m src.radar --dry-run                     # 雷达预览
.venv/bin/python -m pytest tests/ -q              # 全量测试
```

## 推送时段与卡片特性

飞书 Bot 每天固定时刻触发 `workflow_dispatch`（工作日 08:30 / 12:00 / 14:30 / 21:00，周六 09:00，周日 19:00）；
`schedule` cron 统一在 Bot 后约 45 分钟兜底（工作日 09:15 / 12:45 / 15:15 / 21:45，周六 09:45 / 周日 19:45），通过紧凑 `FALLBACK_WINDOWS` 与飞书快照判重确保**绝不跨时段抢跑或重复推送**。
A 股休市时段自动熔断，节假日早间卡自动带休市提示。

卡片核心降噪与决策增强块：

- **🔔 锁屏动态摘要标题**：直接把隔夜纳指/VIX、14:30 操作结论写进通知标题，不点开卡片即可扫完核心结论
- **🚦 14:30 红绿灯表头**：`🟢 绿色`=有加仓机会 / `🟡 黄色`=维持定投不动 / `🔴 红色`=触发风控拦截或单只保险丝，顶部 3 行动作卡直给结论
- **🎯 决策参考**（标题正下方）：规则 3/5（及周六规则 1）命中时点名提醒
- **📡 资讯·持仓关联**（要闻块下方）：重大新闻波及持仓时点名（如"AI×11 条 → 波及 5.1% 持仓"）
- **📡 同类对比（近1年）**（仅周六）：主动型基金跑输同类 70%+ 时提醒

## 文档索引

| 文档 | 用途 |
|---|---|
| `TODO.md` | **待办唯一真源**（含状态快照与观察期项） |
| `AGENTS.md` | **本地开发硬隔离规范** + `.agents/skills/` 8 个内置技能索引（`CLAUDE.md` 软链至此） |
| `docs/ACTION_RULES.md` | **决策规则唯一来源**（权重/三桶/六条规则/止盈/忙人降噪双档规则） |
| `docs/PROJECT_MEMORY.md` | **项目长期工程记忆**（历史踩坑红线、防双推、字段形态陷阱、测试打桩要诀） |
| `docs/SYSTEM_AUDIT_20261005.md` | 全模块深度审计报告（代码质量/完成度/用户价值三维评级） |
| `docs/MANUAL_OPS.md` | 需本人点界面的操作（Render 部署 / 快捷指令换模型） |
| `docs/FAILED_TRADE_SOP.md` | 交易失败单处理 SOP |
| `docs/CONVERT_DESIGN.md` | 基金转换链路设计 + 快捷指令改造步骤 |
| `docs/CODE_AUDIT_20261001.md` | 代码审计（C/D 级遗留项清单） |
| `docs/RULE2_SPIKE.md` | 规则 2 数据源验证笔记（同类分位口径依据） |
| `docs/archive/` | 已归档：重构蓝图 / 33 问卷 / 分仓清单 / 两份项目评估 |
