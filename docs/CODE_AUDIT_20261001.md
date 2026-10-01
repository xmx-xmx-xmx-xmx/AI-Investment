# 代码深度审计报告（2026-10-01 凌晨，只读）

> ✅ **执行记录（10-01 上午）**：A 级全部删除（A1-A6，注：所列 src/prompts.py 实不存在，只有 src/prompts.md）；B1-B5 全部清理（B4 legacy_gems 为早期参考代码，用户确认遗忘用途后删除；git 历史可恢复）。C/D 级按用户指示暂不动。

> 方法：vulture 死代码扫描（60%/80% 双置信度）+ 全模块 import 图 + requirements 对照
> + 逐文件人工核查。**本报告只列证据，未删除任何文件**——每一项等你拍板。
> 结论：项目整体很干净（pyc/data/.env 全正确 ignore、TODO/FIXME=0、litellm 已规范移除），
> 问题集中在"迭代快导致的尸体堆积"，约 **900+ 行死代码 / 死文件**。

## A 级 · 确定可删（证据充分，删前跑全量测试即可）

| # | 目标 | 行数 | 证据 |
|---|---|---|---|
| A1 | `investment_main.py` | 150 | 自称"总入口"，实际入口是 `python -m src.briefing` + `bot_server.py`；全仓零引用（含 tests/.github） |
| A2 | `test_mvp.py`（根目录） | 105 | 零引用；tests/ 目录已有 25 个正式测试文件 |
| A3 | `src/prompts.py` | 31 | 零引用。⚠️ 注意：`src/prompt_templates.py`（195 行）**活着**，勿混淆 |
| A4 | `src/auto_bill_parser.py` | 347 | 零引用。支付宝账单自动解析器，是快捷指令方案的前身，已被替代。自带独立 OpenAI 调用与 logging.basicConfig（若被意外 import 还会污染全局日志） |
| A5 | `src/config_loader.py` + `config/strategy.yaml` | 103+yaml | config_loader 零引用（getter 全部无人调用）；strategy.yaml 只被它读。权重真源是 `constants.py` 的 TARGET_WEIGHTS。⚠️ requirements.txt 里 `PyYAML` 的注释写着"→ src/config_loader.py"，删除后该注释需同步改（PyYAML 本身保留，.github yaml 校验等仍可用） |
| A6 | `src/prompts.md` + `docs/prompts.md` | 31+203 | 两份均零代码引用（prompt 实际都在 prompt_templates.py）。两份内容还不一致（31 行 vs 203 行），留着只会误导 |

小计 ≈ **970 行 + 1 个配置目录**。

## B 级 · 疑似可删（死代码但体量小，可顺手清）

| # | 目标 | 证据 |
|---|---|---|
| B1 | `briefing.py` 的 `_sent_truncate` / `_trading_label` / `_skip_msg` 三个函数 | 定义后零调用（`sector_snippet` 变量 1718 行赋值后未用）。⚠️ 2409 行大文件里动刀，建议下次改 briefing 时顺手删，不单独开 PR |
| B2 | `bot_server.py` 的 `_extract_text` / `_is_command` | 定义后零调用。⚠️ vulture 同报告里的 `root/version/feishu_webhook` 是 FastAPI 路由（装饰器注册），**不是死代码，勿删** |
| B3 | `src/feishu_client.py:29` 未用 import `ListAppTableRecordRequest` | 90% 置信度，删一行 |
| B4 | `references/legacy_gems/` 4 个文件 | 全部零 import（本就不在包路径）。retry_pattern.py 头注自称"供 market_data/radar 参考"——若模式已被吸收，这 4 个可整体移除或迁出仓库；倾向**保留但补一行 README**（成本最低） |
| B5 | `docs/superpowers/`（plans/specs 旧规划） | 历史规划文档，时效性自检后决定归档或删除 |

## C 级 · 代码瑕疵（修复而非删除，各 1-5 分钟）

| # | 位置 | 问题 |
|---|---|---|
| C1 | `src/feishu_client.py:212` | vulture 100% 报 "unreachable code after while"——需人工核查 while/else 逻辑，若确为不可达分支应简化（重试逻辑宜保守，确认后再动） |
| C2 | `src/pending_resolver.py:486` | `_apply_buy` 的 `confirm_nav` 参数未使用（成本计算只用金额）。参数有语义完整性价值，**建议保留并加注释**，或删除参数 |
| C3 | `src/strategy.py:125` | `_apply_long_bottom_override` 的 `deviation_pct` 参数未使用，同上二选一 |
| C4 | `src/market_data.py:446/688` | `encoding` 属性赋值未用（60% 置信，疑似 akshare 兼容残留），低危 |

## D 级 · 看着像问题、实际是刻意设计（**不要动**，防未来误删）

| 项 | 说明 |
|---|---|
| `market_data.py` 不经 net_guard | 两套超时并存是刻意的（外层函数壳 + 内部各自超时） |
| `cron_guard.py` 的 `CRON_UTC` | vulture 报 unused，实际与 daily-run.yml 成对维护 + 测试引用 |
| bot_server FastAPI 路由 | 装饰器注册，vulture 误报 |
| `tests/` 对部分内部函数的直接测试 | 测试引用不算"死" |

## 建议

1. **A 级整批删**（一个 commit）：`git rm` 上表 6 组文件 → 全量测试 → push。10 分钟。
2. **B 级随缘**：B1 留给下次 briefing 改动顺手做；B3 一行随时删；B4 加 README。
3. **C 级节后小专场**：C1 核查优先（涉及重试逻辑正确性）。
4. 审计方法可复用：vulture 扫描命令已验证，建议每季度跑一次挂进 TODO。

> 附：vulture 60% 置信度全量 38 项中，本报告已覆盖全部非误报项；
> 其余为 FastAPI 路由、测试夹具、装饰器注册类误报，已逐一排除。
