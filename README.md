# AI 量化投资系统

> 不预测市场，只执行纪律。飞书多维表格 = 唯一数据库 + 展示看板。GitHub Actions 白嫖运行（飞书 Bot 触发）。纳指期货 + 板块轮动温差 + 全球市场快照。

## 核心原则

1. **极简前端**：100% 依赖飞书多维表格作为数据库和看板，零 UI
2. **纪律驱动**：Python 死算偏离度，LLM 只做翻译和安抚，绝不反过来
3. **全球视野**：纳指期货盘前风向 + 12 核心行业板块轮动温差 + 8 源资讯引擎
4. **零成本运行**：完全依靠 GitHub Actions + 飞书 workflow_dispatch 触发
5. **配置即代码**：板块轮动配置表可直接在飞书手机端编辑，即时生效，零部署

## 项目结构

```
.
├── requirements.txt              # 依赖清单
├── pyproject.toml                # uv 项目配置
├── .env.example
├── src/                          # 核心业务代码（18 模块）
│   ├── market_data.py            # 行情抓取：A/港/美股 ETF + 三大指数 + VIX + 美债 + 纳指期货 + 板块温差
│   ├── price_updater.py          # 现价更新：智能路由 + 趋势检测 + 日涨跌幅写入
│   ├── pending_resolver.py       # Pending 确认：T日净值 + 新品自动建仓 + QDII懒加载
│   ├── strategy.py               # 策略中枢：仓位健康报告 + 长底仓锁定 + 防飞刀拦截
│   ├── advisor.py                # AI 报告：XML 结构化 Prompt v2.0 + 思想钢印
│   ├── briefing.py               # 多时段简报：7 时段 + 纳指期货 + 板块轮动 + 日盈亏 + 英文翻译
│   ├── radar.py                  # 雷达扫描：底仓+自选全量 → 抄底/追涨 → LLM 解读（最多5信号）
│   ├── global_news.py            # 国际 RSS：4源 → LLM 匹配翻译去重
│   ├── earnings_calendar.py      # 财报日历：yfinance 财报日期 + EPS 对比
│   ├── macro_calendar.py         # 宏观日历：ForexFactory → 筛选 → 持仓敏感度映射
│   ├── news_fetcher.py           # 资讯引擎：金十 + 华尔街见闻 + Tavily
│   ├── feishu_client.py          # 飞书 SDK 封装（含 create_record + batch_update）
│   ├── classification.py         # 资产分类：投资载体推断 + 资产大类映射
│   ├── holiday_gate.py           # 节假日熔断：XSHG(中国) + XNYS(美国)
│   ├── llm.py                    # 共享 LLM 客户端（SiliconFlow DeepSeek-V4-Flash）
│   ├── notify.py                 # 飞书群推送（数据卡 + AI 分析卡）
│   ├── auto_bill_parser.py       # OCR 票据解析
│   ├── constants.py              # 共享常量（目标权重唯一真源）
│   └── prompt_templates.py       # 投资宪法 + 六段式模板 + 思维链（含板块温差步骤）
├── tests/                        # 159 个单元测试
├── .github/workflows/
│   └── daily-run.yml             # 飞书 / workflow_dispatch 手动触发
├── bot_server.py                 # Render FastAPI → 飞书机器人（巡航 + LLM 问答 + 去重）
└── _legacy_backup/               # 旧代码备份
```

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt
# 或
uv sync

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入真实 Key

# 常用命令
python -m src.price_updater --dry-run    # 现价预览
python -m src.pending_resolver --dry-run # 交易确认预览
python -m src.briefing morning           # 早间简报
python -m src.briefing midday            # 午间快讯
python -m src.briefing closing           # 收盘前报告
python -m src.briefing evening           # 夜盘前瞻
python -m src.briefing sun_evening       # 周报
python -m src.radar --dry-run            # 雷达预览

# 数据验证
python -c "from src.market_data import fetch_nq_futures; print(fetch_nq_futures('NQ'))"
python -c "from src.market_data import fetch_sector_deltas; print(fetch_sector_deltas())"
```

## 简报时段

所有时段统一使用 **SiliconFlow `deepseek-ai/DeepSeek-V4-Flash`** 模型，成本约 ¥0.001/1K token。

| 时段 | 时间 | 关键区块 | 新增 |
|------|------|---------|------|
| **morning** | 08:30 | 隔夜美股复盘 + VIX + 要闻 + 财报 + 宏观日历 + 雷达 + 市场基准 + 国际快讯 + AI 解读 | — |
| **midday** | 12:00 | 亚太市场 + **板块轮动（港股+A股实时温差）** + 上午要闻 + 持仓 + 午间快评 | 板块轮动 |
| **closing** | 14:30 | 仓位健康 + 要闻 + 市场基准 + **美股盘前风向（纳指期货）** + **板块轮动（全市场）** + 雷达 + 国际快讯 + 持仓 + AI 解读 | 期货 + 板块轮动 |
| **evening** | 21:00 | VIX + **美股期货实时（NQ/ES）** + **板块轮动（美股开盘前后最活跃）** + 全球市场 + 要闻 + 财报 + 雷达 + 国际快讯 + 持仓 + AI 解读 | 期货 + 板块轮动 |
| **sat_morning** | 周六 08:30 | 标普/纳斯达克/VIX + 本周要闻 + 本周美股回顾 | — |
| **sun_evening** | 周日 20:00 | 本周收益 vs 基准 + 仓位健康 + 宏观回顾 + 下周财报日历 + 宏观日历 | — |

> 持仓展示包含每笔当日的盈亏金额 + 底部「💵 今日浮动盈亏」汇总。

## 雷达信号体系

| 信号 | 条件 | 含义 |
|------|------|------|
| 🟡 关注 | 10日跌超5% + 右侧企稳 | 可能筑底，值得研究 |
| 🔵 底部反转 | 20日跌超8% + 右侧企稳 | 深跌修复信号 |
| 🟢 趋势加速 | 5日连续阳线 + 未溢价 | 趋势偏强但未飞 |

> 最多展示 5 个高优先级信号，其余折叠不显示，防止输出截断。

## 板块轮动温差

12 个核心板块 vs 对应大盘基准，温差 > 2% 标注信号：

| 阵营 | 覆盖板块 | 锚定持仓 |
|------|---------|---------|
| 美股 (4) | SOXX 半导体、KWEB 中概、TLT 美债、IWM 小盘股 | 纳指科技/港股互联/固收/华宝致远 |
| 港股 (4) | 03486 亚洲半导体、HSTECH 恒生科技、03121 韩国科技、03076 台湾半导体 | 全部港股ETF持仓 |
| A股 (2) | 红利低波 vs 沪深300、芯片 vs 沪深300 | 红利低波100/科创板芯片 |
| 跨市场 (2) | GLD vs SPY 避险情绪、电子ETF vs 沪深300 | 上海金/MLCC实质 |

> 配置存储在飞书「板块轮动配置表」，手机端即可编辑，下一次简报自动生效。

## 飞书多维表格配置

5 张表在同一多维表格（`FEISHU_BITABLE_TOKEN`）下：

| 表 | 用途 |
|----|------|
| 底仓表 | 持仓：份额/成本/现价/日涨跌幅/趋势/投资载体/资产大类 |
| 交易流水表 | iPhone 快捷指令写入：交易时间/金额/方向/标的代码 |
| 雷达观测表 | 自选观察：抄底信号/追涨信号/10日20日涨跌/MA20偏离 |
| 板块轮动配置表 | **本系统唯一动态配置**：行业代码/数据源/基准/展示标签/启用 |
| 观测记录表 | 历史记录（预留） |

## GitHub Actions 配置

在 Repo Settings → Secrets 中配置：

| Secret | 说明 |
|--------|------|
| `SILICONFLOW_API_KEY` | 硅基流动 LLM API Key |
| `FEISHU_APP_ID` | 飞书应用 App ID |
| `FEISHU_APP_SECRET` | 飞书应用 Secret |
| `FEISHU_BITABLE_TOKEN` | 多维表格 token |
| `FEISHU_TABLE_ID` | 交易流水表 ID |
| `FEISHU_WEBHOOK_URL` | 飞书群机器人 Webhook |
| `FEISHU_WEBHOOK_SECRET` | 飞书群机器人密钥 |
| `TAVILY_API_KEY` | Tavily 搜索 API Key（可选） |

## 投资纪律

| 资产大类 | 目标权重 |
|----------|----------|
| 固收资产 | 50% |
| 美股资产 | 20% |
| A股资产 | 10% |
| 港股资产 | 10% |
| 避险商品 | 10% |

- 长底仓永不卖出（代码锁死），用自然稀释策略
- 阶梯阈值：±5%加倍定投，±10%止盈/大额买入
- 左侧下跌拦截买入（防飞刀），右侧企稳放行
- 冷却期 3 天（同大类不重复建议操作）
