# AI 量化投资系统

> 不预测市场，只执行纪律。飞书多维表格 = 唯一数据库 + 展示看板。GitHub Actions 白嫖运行（飞书 Bot 触发）。

## 核心原则

1. **极简前端**：100% 依赖飞书多维表格作为数据库和看板，零 UI
2. **纪律驱动**：Python 死算偏离度，LLM 只做翻译和安抚，绝不反过来
3. **双向信号**：雷达扫描全量标的（底仓+自选），抄底+追涨双信号，LLM 三行解读
4. **零成本运行**：完全依靠 GitHub Actions + 飞书 workflow_dispatch 触发
5. **国际视野**：3 条 RSS + 宏观日历 + 财报日历，数据全面但只推和持仓相关的内容

## 项目结构

```
.
├── requirements.txt              # 依赖清单
├── pyproject.toml                # uv 项目配置
├── .env.example
├── src/                          # 核心业务代码（18 模块）
│   ├── market_data.py            # 行情抓取：A/港/美股 ETF + 三大指数 + VIX + 美债
│   ├── price_updater.py          # 现价更新：智能路由 + 趋势检测 + 日涨跌幅写入
│   ├── pending_resolver.py       # Pending 确认：T日净值 + 新品自动建仓 + QDII懒加载
│   ├── strategy.py               # 策略中枢：仓位健康报告 + 长底仓锁定 + 防飞刀拦截
│   ├── advisor.py                # AI 报告：XML 结构化 Prompt v2.0 + 思想钢印
│   ├── briefing.py               # 多时段简报：7 时段 + 5区块周报
│   ├── radar.py                  # 雷达扫描：底仓+自选全量 → 抄底/追涨 → LLM 解读
│   ├── global_news.py            # 国际 RSS：3源 → LLM 匹配翻译去重
│   ├── earnings_calendar.py      # 财报日历：yfinance 财报日期 + EPS 对比
│   ├── macro_calendar.py         # 宏观日历：ForexFactory → 筛选 → 持仓敏感度映射
│   ├── news_fetcher.py           # 资讯引擎：金十 + 华尔街见闻 + Tavily
│   ├── feishu_client.py          # 飞书 SDK 封装（含 create_record）
│   ├── holiday_gate.py           # 节假日熔断：XSHG(中国) + XNYS(美国)
│   ├── llm.py                    # 共享 LLM 客户端（SiliconFlow）
│   ├── notify.py                 # 飞书群推送（数据卡 + AI 分析卡）
│   ├── auto_bill_parser.py       # OCR 票据解析
│   ├── constants.py              # 共享常量
│   └── prompts.md                # Prompt 参考
├── tests/                        # 158 个单元测试
├── docs/superpowers/             # 设计文档 + 实现计划
├── .github/workflows/
│   └── daily-run.yml             # 飞书 / workflow_dispatch 手动触发
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
python -m src.briefing morning           # 早间简报测试
python -m src.briefing closing           # 收盘前报告测试
python -m src.briefing sun_evening       # 周报测试
python -m src.radar --dry-run            # 雷达预览
python -m src.global_news --dry-run      # RSS 预览
```

## 简报时段

所有时段统一使用 **SiliconFlow `Qwen/Qwen3-30B-A3B-Instruct-2507`** 模型（见 `src/llm.py`），成本约 ¥0.001/1K token。

| 时段 | 时间 | 具体区块 | LLM 调用 |
|------|------|---------|----------|
| **morning** | 08:30 | ①道琼斯/标普/纳斯达克/VIX 四指数 ②隔夜要闻（金十+华尔街见闻） ③昨日财报（EPS实际vs预期） ④宏观日历 ⑤雷达扫描（全量+LLM解读） ⑥市场基准（沪深300+恒生+标普+纳指） ⑦国际快讯（RSS+LLM总结） ⑧AI解读+今日重点关注 | 4 次（AI解读/今日关注/雷达LLM/快讯总结） |
| **midday** | 11:30 | ①上午要闻 ②午间快评（LLM） ③持仓市值 ④下午关注 | 1 次 |
| **closing** | 14:30 | ①仓位健康报告（偏离度） ②持仓市值+日涨跌 ③雷达扫描（全量+LLM解读） ④市场基准 ⑤午间解读（LLM） ⑥午间要闻 ⑦国际快讯（RSS+LLM总结） | 3 次 |
| **evening** | 20:30 | ①VIX盘前快照 ②持仓市值 ③市场基准 ④今晚财报（如有） ⑤今日要闻 ⑥雷达扫描（全量+LLM解读） ⑦国际快讯（RSS+LLM总结） ⑧AI解读+今晚关注 | 4 次 |
| **sat_morning** | 周六 08:30 | ①标普/纳斯达克/VIX ②本周要闻 ③本周美股回顾（LLM） | 1 次 |
| **sun_evening** 周报 | 周日 20:00 | ①本周收益（vs SPY/沪深300） ②仓位健康 ③宏观回顾+国际要闻+下周关注（1次LLM） ④下周财报日历 ⑤下周宏观日历 | 1 次（综合） |

> **关于市场基准和全球指数**：morning / closing / evening 均包含 `_build_market_context()`，展示沪深300/恒生/标普/纳指当日涨跌。晨报的美股板块已含道琼斯+标普+纳斯达克+VIX 四指数。 |

## 雷达信号体系

| 信号 | 条件 | 含义（不等于买入指令） |
|------|------|----------------------|
| 🟡 关注 | 10日跌超5% + 右侧企稳 | 可能筑底，值得研究 |
| 🔵 底部反转 | 20日跌超8% + 右侧企稳 | 深跌修复信号 |
| 🟢 趋势加速 | 5日连续阳线 + 未溢价 | 趋势偏强但未飞 |

## 飞书多维表格配置

4 张表在同一多维表格（`FEISHU_BITABLE_TOKEN`）下：

| 表 | 用途 |
|----|------|
| 底仓表 | 持仓：份额/成本/现价/日涨跌幅/趋势 |
| 交易流水表 | iPhone 快捷指令写入：交易时间/金额/方向/标的代码 |
| 雷达观测表 | 自选观察：抄底信号/追涨信号/10日20日涨跌 |
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
| 美股资产 | 25% |
| A股资产 | 10% |
| 港股资产 | 5% |
| 避险商品 | 10% |

- 长底仓永不卖出（代码锁死），用自然稀释策略
- 阶梯阈值：±5%加倍定投，±10%止盈/大额买入
- 左侧下跌拦截买入（防飞刀），右侧企稳放行
- 冷却期 3 天（同大类不重复建议操作）
