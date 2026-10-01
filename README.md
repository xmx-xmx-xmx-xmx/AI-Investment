# AI 量化投资系统

> 不预测市场，只执行纪律。飞书多维表格 = 唯一数据库 + 展示看板。GitHub Actions（飞书 Bot 触发 + cron 兜底）零成本运行。规则引擎 + 资讯映射 + 多时段简报。

> 📌 **待办唯一真源：`TODO.md`** · **决策规则唯一来源：`docs/ACTION_RULES.md`** · 本 README 只讲"系统是什么、怎么跑"。

## 核心原则

1. **极简前端**：100% 依赖飞书多维表格作为数据库和看板，零 UI
2. **纪律驱动**：Python 死算偏离度，LLM 只做翻译和安抚，绝不反过来
3. **规则先行**：所有提醒/建议先定规则（`docs/ACTION_RULES.md`）再写代码；规则引擎纯本地、fail-silent，绝不拖垮简报
4. **零成本运行**：GitHub Actions（workflow_dispatch + 6 条 cron 兜底）+ Render 免费实例
5. **账本可审计**：结算回执 + 结算前快照 + 增量对账告警，算错必被告警看见

## 当前状态（2026-10-01）

| 维度 | 值 |
|------|-----|
| 代码规模 | `src/` 27 模块，**589 测试全绿** |
| 目标权重 | **固收45 / 美股30 / A股5 / 港股10 / 避险10**（`src/constants.py` 唯一真源） |
| 分仓 | 长期底仓16 / 普通持有10 / 观察仓3（底仓表「标签」列） |
| 规则引擎 | 规则1 同主题业绩差 / 2 同类分位（周更） / 3 回本提醒 / 5 单只保险丝 / 6 资讯×持仓映射 |
| 推送可靠性 | 双推判重 + cron 延迟容错（09-30 三连修后稳定） |

## 项目结构

```
.
├── TODO.md                       # 待办唯一真源
├── CLAUDE.md                     # 本地开发硬隔离规范（最高优先级）
├── requirements.txt / pyproject.toml
├── src/                          # 核心业务代码（27 模块）
│   ├── briefing.py               # 多时段简报编排（7 时段 + 规则/资讯块注入）
│   ├── rules_engine.py           # 规则 1/3/5：同主题业绩差 / 回本提醒 / 单只保险丝
│   ├── peer_rank.py              # 规则 2：主动型近1年同类分位（akshare，周六周更）
│   ├── news_mapper.py            # 规则 6：资讯→主题→持仓映射（8 主题关键词表）
│   ├── reconcile.py              # 底仓增量对账告警（基准=结算前快照，只告警不改数）
│   ├── pending_resolver.py       # Pending 结算：T日净值 + convert 双腿 + 失败单排除
│   ├── strategy.py               # 策略中枢：仓位健康 + 长底仓锁定 + 防飞刀 + 冷却期
│   ├── radar.py                  # 雷达扫描 + 超配闸门（偏离≥+5pp 抑制买入信号）
│   ├── holiday_gate.py           # 节假日熔断：A股/港股/美股三市场
│   ├── cron_guard.py             # cron 兜底：时段推断 + dispatch 判重
│   ├── market_data.py            # 行情抓取（外层超时壳，刻意不经 net_guard）
│   ├── net_guard.py              # akshare/yfinance 全量硬超时代理
│   ├── news_fetcher.py           # 资讯引擎：金十 + 华尔街见闻 + Tavily
│   ├── global_news.py / macro_calendar.py / earnings_calendar.py
│   ├── llm.py / notify.py / feishu_client.py / constants.py / classification.py
│   └── prompt_templates.py       # 投资宪法 + 六段式模板
├── bot_server.py                 # Render FastAPI → 飞书机器人（webhook + 加固）
├── tests/                        # 589 用例 / 25+ 模块
├── docs/                         # 活文档（见下）
└── docs/archive/                 # 已归档分析/设计文档
```

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env   # 填入真实 Key

# 常用命令（本地 dry-run，绝不触飞书 API —— 见 CLAUDE.md 硬隔离）
python -m src.pending_resolver --dry-run
python -m src.briefing morning          # 早间简报
python -m src.briefing evening          # 夜盘前瞻
python -m src.radar --dry-run           # 雷达预览
.venv/bin/python -m pytest -q           # 全量测试
```

## 推送时段

飞书 Bot 每天固定时刻触发 workflow_dispatch（08:30 / 12:00 / 14:30 / 21:00）；
cron 在 bot 后 45 分钟兜底（09:15 / 12:45 / 15:15 / 21:45，判重跳过已推送时段）；
周末无 bot，cron 是唯一通道（周六 09:10 复盘 / 周日 19:10 前瞻）。
A股休市时段自动熔断，节假日早间卡自动带休市提示。

卡片新增的"会开口"的块：

- **🎯 决策参考**（标题正下方）：规则 1/3/5 命中时出现（如"同主题两只基金收益差 27.8pp"）
- **📡 资讯·持仓关联**（要闻块下方）：重大新闻波及持仓时点名（如"AI×11 条 → 波及 5.1% 持仓"）
- **📡 同类对比（近1年）**（仅周六）：主动型基金跑输同类 70%+ 时提醒

## 文档索引

| 文档 | 用途 |
|------|------|
| `TODO.md` | **待办唯一真源**（含状态快照） |
| `docs/ACTION_RULES.md` | **决策规则唯一来源**（权重/三桶/六条规则/止盈/标的池偏好） |
| `docs/MANUAL_OPS.md` | 需本人点界面的操作（Render 部署 / 快捷指令换模型） |
| `docs/FAILED_TRADE_SOP.md` | 交易失败单处理 SOP |
| `docs/CONVERT_DESIGN.md` | 基金转换链路设计 + 快捷指令改造步骤 |
| `docs/CODE_AUDIT_20261001.md` | 代码审计（C/D 级遗留项清单） |
| `docs/RULE2_SPIKE.md` | 规则 2 数据源验证笔记（同类分位口径依据） |
| `docs/archive/` | 已归档：重构蓝图 / 33 问卷 / 分仓清单 / 两份项目评估 |
