# CLAUDE.md

本文件是 `AI-Investment` 项目的本地开发行为规范，优先级高于所有其他指令。
生产环境（GitHub Actions）不受此文件约束。

> 📌 **待办唯一真源是 `TODO.md`**；系统是什么/怎么跑见 `README.md`；常用命令与架构速查见 `TODO.md` §5；决策规则见 `docs/ACTION_RULES.md`；工程踩坑长期记忆见 `docs/PROJECT_MEMORY.md`。
> 本文件**只保留硬约束**（工具无关：Claude Code / Antigravity / Cursor / WorkBuddy 等任何 AI 助手改本项目都必须遵守），不再维护模块清单、行数、待办——那些极易漂移。

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
tests/fixtures/
├── portfolio_mock.json       # lark-cli 导出的底仓快照
├── radar_mock.json           # lark-cli 导出的雷达观测快照
├── sector_config_mock.json   # lark-cli 导出的板块轮动配置快照
└── trade_history_mock.json   # lark-cli 导出的交易流水快照
```

快照更新命令（仅在需要同步最新真实数据时手动执行）：
```bash
lark-cli +record-list --table-id tblxxx --json > tests/fixtures/portfolio_mock.json
```

**飞书数据表真源**（单点维护在 `src/feishu_client.py` 的 `TABLE_MAP`）：

| 表名 | table_id | 用途 |
|------|----------|------|
| 底仓表 | `tblpiht8ex94bM6x` | 持仓底仓 |
| 交易流水表 | `tblbnD3uaEdohjji` | 交易记录 |
| 雷达观测表 | `tbloKn9F9TPf4wwO` | 雷达观测标的 |
| 板块轮动配置表 | `tblsR4WDQySkxiYP` | 板块轮动配置 |
| 简报快照表 | `tblxJqf6BT5GfhGh` | E 改造：每时段快照（时段/时间戳/签名/数据载荷） |

> 简报快照表于 2026-09-05 经飞书 API 创建，字段与单选选项已校验。

### 1.3 环境判定函数

见 `src/env.py`：
```python
from src.env import is_production, is_dev
```

生产环境 = **GitHub Actions**（`GITHUB_ACTIONS=true`）**或 Render**（`RENDER=true`，Render 自动注入）。两者都未设置 → 本地开发。

所有涉及 FeishuClient 的代码必须包裹：
```python
if is_production():
    client = FeishuClient()
else:
    client = None  # 或加载 mock 数据
```

> ⚠️ 2026-09-05 教训：曾只判 `GITHUB_ACTIONS`，导致 Render 上的飞书机器人被判成"本地"，
> 巡航指令 raise RuntimeError、问答上下文全空。新增部署平台时必须同步扩 `is_production()` 判据。

### 1.4 命令行接口规范

所有模块的 `main()` 必须支持 `--dry-run`：
```bash
python -m src.briefing morning --dry-run   # 零网络调用
python -m src.strategy --dry-run           # 本地 mock 数据
python -m src.radar --dry-run              # 本地 mock 数据
```

---

## 2. 默认工作流

1. 拉取最新代码后，先检查 `tests/fixtures/` 快照是否过期
2. 所有代码修改在本地用 `--dry-run` 验证
3. 不执行 `git commit` / `git push` 除非用户明确要求
4. 生产环境部署 = 推送到 GitHub + Actions 自动触发
5. 飞书配置表（板块轮动配置表）的修改：直接在手机飞书端编辑，下一次 Actions 运行自动生效

## 3. 安全红线

- `.env` 不得提交；密钥只存在于 GitHub Secrets
- `feishu_triggers_pat.md` 不得提交（已在 .gitignore）
- 不在代码中硬编码 token/URL/密码
- 本地开发时 Token 消耗=0（不使用 LLM、不使用飞书 API）

## 4. 长期记忆与 Agent Skills 索引

- **工程红线与历史踩坑真源**：[`docs/PROJECT_MEMORY.md`](docs/PROJECT_MEMORY.md)（浓缩飞书数组字段陷阱、结算缓存同步、转换 D5 闸门、`cron_guard` 双窗口防双推、超时装饰器陷阱等不可违背的工程不变量）。
- **仓库内置技能库（`.agents/skills/`，兼容 Agent Skills 开放标准）**：

| 技能名 | 路径 | 适用场景 |
|--------|------|----------|
| `ai-investment-verify-release` | `.agents/skills/ai-investment-verify-release/SKILL.md` | 本地跑测试、干跑简报（`--compact`/`--full`）、代理/直连 push 发布 |
| `feishu-bitable-lark-cli` | `.agents/skills/feishu-bitable-lark-cli/SKILL.md` | 用 `lark-cli` 读写飞书多维表格、群消息导出与离线复算 |
| `feishu-trade-failure-rollback` | `.agents/skills/feishu-trade-failure-rollback/SKILL.md` | 支付宝/银行交易失败单的底仓份额与成本均价精确回滚 SOP |
| `github-actions-run-diagnosis` | `.agents/skills/github-actions-run-diagnosis/SKILL.md` | 无 `gh` CLI 时用 REST API 诊断 Actions 超时/排队/失败及耗时分布 |
| `icloud-shortcut-inspector` | `.agents/skills/icloud-shortcut-inspector/SKILL.md` | iCloud 共享快捷指令逆向解析、提示词提取与新旧版本 set-diff 复核 |
| `portfolio-attribution-review` | `.agents/skills/portfolio-attribution-review/SKILL.md` | 全组合收益归因体检（行情/配置/节奏三段拆解与六维记分卡） |
| `qdii-share-class-advisor` | `.agents/skills/qdii-share-class-advisor/SKILL.md` | 场外 QDII 基金 A/C/E 份额费率平衡点、FIFO 赎回费与限额窗口决策 |
| `feishu-ledger-spending-analysis` | `.agents/skills/feishu-ledger-spending-analysis/SKILL.md` | 飞书个人记账本消费体检、旅行/预付费摊销与四口径储蓄率分析 |
