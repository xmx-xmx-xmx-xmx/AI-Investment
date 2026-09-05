# TODO —— 唯一待办真源

> **最后更新：2026-09-05**
> **本文件取代**：`REFACTOR.md` 待办段 / `docs/PROJECT_ASSESSMENT.md` §3.5 / `docs/PROJECT_VALUE_AND_IMPROVEMENT.md` §四 / `.workbuddy/memory/*.md` 中的零散待办。
> 其余文件只保留"已归档成果 + 分析过程"，不再维护待办列表。
>
> **当前阶段**：E+F 已上线 → **观察期**。下一轮重点是"减负"（让用户真的愿意看），不是"增强"。

---

## 0. 状态快照（2026-09-05）

| 维度 | 值 |
|------|-----|
| 核心入口 | `python -m src.briefing <slot>`；7 时段 |
| src/ 模块数 | 23 |
| `briefing.py` 行数 | **1968**（全项目最大且改动最频繁，⚠️ 零测试覆盖） |
| 测试 | 159 用例，覆盖 5 个模块（macro_calendar / market_data / global_news / radar / strategy） |
| 飞书表 | 5 张（底仓 / 交易流水 / 雷达观测 / 板块轮动配置 / **简报快照表 `tblxJqf6BT5GfhGh`**） |
| LLM 链 | 主 `DeepSeek-V3.2` → 备 `Qwen3.5-9B` → 纯文本兜底 |
| CI | 仅 `workflow_dispatch`（飞书触发），⚠️ 无 cron 兜底 |
| 本地隔离 | ✅ 已落地（`get_feishu_client_or_none()`） |

---

## 1. ✅ 已完成（近期，不再列入待办）

| 项 | 出处 | 完成于 |
|----|------|--------|
| **P0 11 处 FeishuClient 本地隔离** | ASSESSMENT §3.5 #3 | commit `2f37d3c` |
| **weights 动态化**（宪法从 `TARGET_WEIGHTS` 拼，永不漂移） | ASSESSMENT §3.5 #1 | commit `64e8344` |
| **investment_main.py 死引用修复** | ASSESSMENT §3.5 #2 | commit `0fdbc5f` |
| **LLM 降级链重构**（两层 + 吞字防护） | VALUE §一 | 2026-09-02 |
| **D：hard_signals 注入 LLM**（消除矛盾解读） | VALUE §3.5 | commit `64e8344` |
| **E：变化感知 + 飞书快照表 + diff** | VALUE §3.2 | commit `2f37d3c` + `45ece75` |
| **F：叙事化 AI 解读**（`<diff_context>` 注入） | VALUE §3.3 | commit `2f37d3c` |
| **E 跳过逻辑 bug 修复**（占位符签名导致减负从未生效） | 本轮发现 | 本轮 |

---

## 2. 📋 剩余待办（按 价值 ÷ 工时 排序）

> 排序依据：**用户价值** > **风险/债务** > **纯重构**。
> 工时估算是"一人专注干"的量级。

### 🔴 P0 —— 观察期做（本周，合计 < 1 天）

| # | 待办 | 工时 | 为什么现在做 |
|---|------|------|-------------|
| **1** | **观察 E+F 首个生产周期**（3-5 天，只做记录不改代码） | 0 | 刚上线的 diff / 跳过 / 叙事化**一次都没在真实推送里跑过**。看三件事：① 无变化时那句"按纪律维持不动"是否出现得合理；② 有变化时 AI 是否真的讲了"变了什么"而不是套话；③ 有没有整段空白/重复。观察结果决定 P1 的取舍 |
| **2** | **补 `briefing.py` 核心测试**（diff / hard_signals / snapshot 三块，约 8-10 个用例） | 2-3 h | 本轮刚在 `_diff_against_last` 抓到一个让核心功能完全失效的 bug，而它**零测试**。1968 行、改动最频繁、零覆盖 = 下一次改动必踩雷 |
| **3** | **依赖对齐**：`pyproject.toml` 补 `openpyxl` / `exchange-calendars` / `litellm` / `PyYAML`（requirements.txt 有、pyproject 缺） | 10 min | `uv sync` 会静默缺包，属"改一行省一次排查" |

### 🟠 P1 —— 下一轮（本月，按此顺序做）

| # | 待办 | 工时 | 价值 |
|---|------|------|------|
| **4** | **平淡日折叠 + 今日变化摘要**（VALUE §3.2 完整版） | 2-3 天 | ⭐ **用户痛点的真正解药**。目前只砍掉了 AI 那一段，简报主体仍全量铺陈 7 个 block（VIX→市场→新闻→财报→宏观→雷达→国际→持仓）。用户的原话是"内容太多成负担"——把平淡日的 block 折叠成"3-5 行变化摘要 + 一句话点评"，才是真的减负 |
| **5** | **参考卡：今日值得多看一眼**（VALUE §3.4） | 1-2 天 | 把 `judge()` 已算好的硬信号 + 语境（趋势 / 冷却期 / 雷达同步）整理成"如果你要定投，美股是当前方向"式的参考。数据**零新数据源**，纯释放已有判断价值。用户明确"不越俎代庖买卖"，参考卡的措辞正好踩在这条线上 |
| **6** | **CI 加 cron 兜底**（`schedule: cron '30 0 * * 1-5'` = 北京时间 08:30） | 15 min | 当前只有飞书 Bot 触发 workflow_dispatch，Bot 一宕机整条调度链就断，且**静默失败**（不会有人发现） |
| **7** | **bot_server 加固**：`_processed_events` 改 TTL dict（30 min）+ Verification Token 强制校验 + 清 L100-102 死代码 | 1-2 h | 安全项：防伪造事件 + 防内存无限增长 |
| **8** | **D3 VIX 动态赔率**：`strategy.py` 接 `fetch_vix()`，宪法"每次 100-200 元"改为"金额由恐慌指数动态计算" | 1-2 天 | 最直观的"智能感"提升——同样的偏离度，恐慌区和平静区的建议金额不一样，用户能立刻感知到系统"会看环境" |
| **9** | **D4 技术面风控闸门**：radar 已算的 MA20 偏离递给 strategy（>5% 一票否决买入）+ 新增 `fetch_etf_premium`（溢价 >2% 拦截追高） | 1-2 天 | 当前风控是**单维度**（只看偏离度）。补上趋势 + 溢价后，参考卡每条都能讲"偏离 / 趋势 / 恐慌"三维，减少单维度误判 |
| **10** | **YAML 配置化**（`config_loader` 8 个 getter 零调用 → 替换 constants/strategy/market_data 硬编码，完成后删 constants.py） | 1-2 周 | ⚠️ **从原 P0 降级**。weights 已动态化、不会再漂移，实际一年也改不了几次纪律。纯工程收益，排在用户价值之后 |

### 🟡 P2 —— 本季度后 / 择机

| # | 待办 | 工时 | 备注 |
|---|------|------|------|
| **11** | **D1/D2 机器人命令**（`@机器人 雷达 / 早报 / 收盘` + `@机器人 买入 [名称] [金额]`） | 3-5 天 | 基建已就绪（`notify.send_card` / `FeishuPusher`）。交互价值高，但工程量大，等减负做完再上 |
| **12** | **D5b 基本面估值**（PE/PB/ROE/股息率，用 `legacy_gems/fundamental_adapter.py`） | 2-3 天 | 为红利低波(021551) / 港股消费(017435) 补估值维度 |
| **13** | **D5 tenacity 重试**（给 `market_data` 外部抓取注入 `@retry`，用 `legacy_gems/retry_pattern.py`） | 半天 | 防单次网络抖动断链 |
| **14** | **D6 宏观敏感度改 YAML**（`EVENT_SENSITIVITY` 7 组 → `config/sensitivity.yaml`） | 半天 | 同 #10，配置化一起做 |
| **15** | **briefing.py 拆分**（1968 行 → `src/briefing/` 包子模块，优先级：formatting → blocks → ai → estimation → slots） | 2-3 天 | ⚠️ **仅当继续大改时才拆**。纯重构不产生用户价值，做完 #4 #5 再说 |
| **16** | **补 `advisor` / `feishu_client` / `pending_resolver` / `price_updater` 测试** | 2-3 天 | 与 #2 分开：#2 保核心改动，这条补全覆盖 |
| **17** | **D6 prompt 微调**（max_tokens / temperature A/B） | 半天 | 等 #1 观察有结论再做，否则是瞎调 |
| **18** | **D7 飞书仪表盘**（大类权重饼图 / 市值趋势） | 2-3 天 | 锦上添花 |

### 🟢 P3 —— 远期

| # | 待办 | 前置 |
|---|------|------|
| **19** | **D10 策略回测** | 快照机制已建（`简报快照表`），数据开始积累，攒够 3-6 个月再动 |
| **20** | **D5c 飞书高级卡片**（`legacy_gems/feishu_stream.py` 交互卡片模板） | 等 #4 定下简报形态 |
| **21** | **D11 模拟盘**（`ENV=paper`） | — |
| **22** | **D8 雷达深度分析 / 行业研报** | — |
| **23** | **D9 Scriptable iOS 桌面小组件** | — |

---

## 3. 🗑️ 已废弃 / 不再做

| 项 | 原因 |
|----|------|
| "手动同步 TARGET_WEIGHTS 到宪法" | 已改为从 `constants.TARGET_WEIGHTS` 动态拼，**永不漂移** |
| "本地隔离 11 处" | 已完成（`get_feishu_client_or_none()` 工厂） |
| 三层 LLM 降级链（DeepSeek→Qwen27B→Qwen9B→纯文本） | 已改两层；第三层"应急"删除，代金券只覆盖两层 |
| `market_brief.py` 相关 | 文件已删，`investment_main.py` 兼容层已加 |

---

## 4. 📁 待办来源映射（历史归档，勿再往这些文件加待办）

| 原文件 | 原位置 | 状态 |
|--------|--------|------|
| `TODO.md` | 全文 | ← **本文件**（已重写） |
| `REFACTOR.md` | §剩余待办（YAML 配置化 / 千行拆分） | → 并入 #10 #15 |
| `docs/PROJECT_ASSESSMENT.md` | §2.2 路线图 / §2.3 漂移 / §3.1 质量问题 / §3.5 优化建议 | → 已完成项见 §1，剩余并入 §2 |
| `docs/PROJECT_VALUE_AND_IMPROVEMENT.md` | §四 改进优先级总览 | → §3.2→#4、§3.3→已完成(F)、§3.4→#5、§3.5→已完成(D)、§3.6→#8 #9 #12 |
| `.workbuddy/memory/2026-09-05.md` | §剩余待办 | → 已全部完成或并入 §2 |
| `.workbuddy/memory/MEMORY.md` | 待办池 | → 只保留技术约束，不再列待办 |

---

## 5. 🏗️ 架构速查

- **行情** `market_data.py`（A/港/美股 ETF + 三大指数 + VIX + 美债 + 纳指期货 + 板块温差）
- **策略** `strategy.py`（唯一真源：仓位健康 / 长底仓锁定 / 防飞刀 / 冷却期）
- **简报** `briefing.py`（7 时段 + 周报 + hard_signals + diff 变化感知）
- **雷达** `radar.py`（全量扫描 → 双信号 + LLM）
- **飞书** `feishu_client.py`（5 张表，`TABLE_MAP` 单点维护；`get_feishu_client_or_none()` 本地隔离）
- **快照** `read_briefing_snapshot` / `write_briefing_snapshot`（生产走飞书，本地走 `tests/fixtures/`）
- **LLM** `llm.py` + `prompt_templates.py`（投资宪法动态化 + 六段式 + hard_signals + diff_context）

### 常用命令

```bash
python -m src.briefing morning|midday|closing|evening|sat_morning|sun_evening
python -m src.price_updater --dry-run
python -m src.pending_resolver --dry-run
python -m src.radar --dry-run
.venv/bin/python -m pytest tests/ -q

# 推送（国内网络需走 Clash 7890）
git -c http.proxy=http://127.0.0.1:7890 -c https.proxy=http://127.0.0.1:7890 push origin main
```
