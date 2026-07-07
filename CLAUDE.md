# CLAUDE.md

本文件是 `AI-Investment` 项目的本地开发行为规范，优先级高于所有其他指令。
生产环境（GitHub Actions）不受此文件约束。

## 1. 🚫 本地开发硬隔离 —— 最高优先级

### 1.1 绝对禁止：本地调用飞书云端 API

本地开发（含 `--dry-run`、`python -m src.xxx` 等所有非 GitHub Actions 环境）中：

- **禁止** `from src.feishu_client import FeishuClient` 后接 `FeishuClient()` 实例化
- **禁止** `client.list_records()` / `client.create_record()` / `client.batch_update()` 等任何飞书 API 写操作
- **禁止** `judge_from_feishu()` 不带 mock client 直接调用

**违规热点**（以下位置必须在本地开发时被拦截）：

| 文件 | 违规行为 | 拦截方案 |
|------|---------|---------|
| `strategy.py` `_fetch_radar_signals()` | 内直接 `FeishuClient()` | 检查 `--dry-run` flag，返回 `{}` |
| `strategy.py` `judge_from_feishu()` | 无 client 时自动建 | 要求显式传入 mock client |
| `briefing.py` `_build_trade_summary()` | 内 `FeishuClient()` | 本地模式返回空字符串 |
| `market_data.py` `fetch_sector_deltas()` | 内 `FeishuClient()` | 本地模式返回 `[]` |
| `radar.py` 模块级 import | 自动实例化 | 本地模式返回空 dict |
| `advisor.py` `load_portfolio()` | 内自动建 client | 本地模式加载 mock JSON |

### 1.2 强制使用本地快照

本地模式下，所有持仓/雷达/配置数据的唯一来源必须是：

```
test/fixtures/
├── portfolio_mock.json       # lark-cli 导出的底仓快照
├── radar_mock.json           # lark-cli 导出的雷达观测快照
├── sector_config_mock.json   # lark-cli 导出的板块轮动配置快照
└── trade_history_mock.json   # lark-cli 导出的交易流水快照
```

快照更新命令（仅在需要同步最新真实数据时手动执行）：
```bash
lark-cli +record-list --table-id tblxxx --json > test/fixtures/portfolio_mock.json
```

### 1.3 环境判定函数

见 `src/env.py`：
```python
from src.env import is_production, is_dev
```

所有涉及 FeishuClient 的代码必须包裹：
```python
if is_production():
    client = FeishuClient()
else:
    client = None  # 或加载 mock 数据
```

### 1.4 命令行接口规范

所有模块的 `main()` 必须支持 `--dry-run`：
```bash
python -m src.briefing morning --dry-run   # 零网络调用
python -m src.strategy --dry-run           # 本地 mock 数据
python -m src.radar --dry-run              # 本地 mock 数据
```

---

## 2. 项目结构速览

```
src/
├── briefing.py          # 7时段简报编排（1431行，待拆分）
├── market_data.py       # 行情抓取（933行）
├── radar.py             # 雷达扫描（651行）
├── strategy.py          # 策略中枢（514行）
├── advisor.py           # AI 顾问上下文构造（572行）
├── pending_resolver.py  # 交易确认（597行）
├── price_updater.py     # 现价更新（359行）
├── feishu_client.py     # 飞书 SDK 封装（375行，仅生产可用）
├── macro_calendar.py    # 宏观日历（562行）
├── news_fetcher.py      # 资讯引擎（364行）
├── global_news.py       # 国际 RSS（423行）
├── earnings_calendar.py # 财报日历（210行）
├── notify.py            # 飞书推送（274行）
├── classification.py    # 资产分类（227行）
├── prompt_templates.py  # Prompt 模板（132行）
├── holiday_gate.py      # 节假日熔断（120行）
├── llm.py               # LLM 客户端（33行）
├── constants.py          # 共享常量（19行，待迁移到 YAML）
├── env.py                # 环境判定
config/
└── strategy.yaml         # 策略配置（待接入）
references/
└── legacy_gems/          # 从旧项目提取的参考代码
```

## 3. 常用命令

```bash
# 简报（本地干跑）
python -m src.briefing morning --dry-run
python -m src.briefing midday --dry-run
python -m src.briefing closing --dry-run
python -m src.briefing evening --dry-run

# 数据维护（本地干跑）
python -m src.price_updater --dry-run
python -m src.pending_resolver --dry-run
python -m src.radar --dry-run

# 策略判定（本地 mock）
python -m src.strategy --dry-run

# 运行测试
.venv/bin/python -m pytest tests/ -q

# 更新本地 mock 快照
lark-cli +record-list --table-id tblxxx > test/fixtures/portfolio_mock.json
```

## 4. 默认工作流

1. 拉取最新代码后，先检查 `test/fixtures/` 快照是否过期
2. 所有代码修改在本地用 `--dry-run` 验证
3. 不执行 `git commit` / `git push` 除非用户明确要求
4. 生产环境部署 = 推送到 GitHub + Actions 自动触发
5. 飞书配置表（板块轮动配置表）的修改：直接在手机飞书端编辑，下一次 Actions 运行自动生效

## 5. 安全红线

- `.env` 不得提交；密钥只存在于 GitHub Secrets
- `feishu_triggers_pat.md` 不得提交（已在 .gitignore）
- 不在代码中硬编码 token/URL/密码
- 本地开发时 Token 消耗=0（不使用 LLM、不使用飞书 API）
