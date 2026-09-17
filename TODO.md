# TODO —— 唯一待办真源

> **最后更新：2026-09-17**
> **本文件取代**：`REFACTOR.md` 待办段 / `docs/PROJECT_ASSESSMENT.md` §3.5 / `docs/PROJECT_VALUE_AND_IMPROVEMENT.md` §四 / `.workbuddy/memory/*.md` 中的零散待办。
> 其余文件只保留"已归档成果 + 分析过程"，不再维护待办列表。
>
> **当前阶段**：E+F 已上线 → **观察期**。下一轮重点是"减负"（让用户真的愿意看），不是"增强"。

---

## 0. 状态快照（2026-09-17）

| 维度 | 值 |
|------|-----|
| 核心入口 | `python -m src.briefing <slot>`；7 时段 |
| src/ 模块数 | 23 |
| `briefing.py` 行数 | **2031**（全项目最大且改动最频繁，⚠️ 仅 diff 块 + trade_summary 有测试） |
| `pending_resolver.py` 行数 | 883（本次 +convert 分支后） |
| 测试 | **202 用例**，覆盖 8 个模块（macro_calendar / market_data / global_news / radar / strategy / briefing-diff / **convert**） |
| 飞书表 | 5 张（底仓 / 交易流水 / 雷达观测 / 板块轮动配置 / 简报快照表 `tblxJqf6BT5GfhGh`） |
| 交易流水表字段 | 12 列（2026-09-17 新增 `转入标的` / `转出份额`，`买卖方向` 加 `convert`） |
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
| **E 跳过逻辑 bug 修复**（占位符签名导致减负从未生效） | 本轮发现 | commit `03238b6` |
| **E 二次校正**（签名从"卡片 hash"改"信息面指纹"：新闻/行情换了就调 LLM，不再天天"维持不动"） | 用户 10 天实测反馈 | commit `03238b6` |
| **国际快讯分层**（展示层 60 字/4 条速读；AI 层 5 条×150 字带英文标题；流水线缓存 30min TTL 避免跑两遍） | 用户反馈"几个字还被截断" | commit `03238b6` |
| **`tests/test_briefing_diff.py`**（diff 双判据 10 用例） | P0 #2 部分完成 | commit `03238b6` |
| **`tests/test_global_news.py` 补 6 用例**（两层分层边界） | 同上 | commit `03238b6` |
| **P0 #0：交易流水表「转换（convert）」支持**（详见下方 §1.1） | 用户 2026-09-17 实操换仓发现 | 2026-09-17 实施 |

### 1.1 P0 #0 转换（convert）改造 —— 已实施明细

**一、飞书表结构（已改，可回滚）**
- `买卖方向` 单选：`buy / sell` → `buy / sell / convert`
- 新增 `转入标的`（文本，类型 1）
- 新增 `转出份额`（数字，类型 2）

**二、代码（6 个文件）**
| 文件 | 改动 |
|------|------|
| `src/pending_resolver.py` | ① `_parse_action` 支持 convert，**未知方向返回 `unknown` 而非 `buy`**；② 新增 `_parse_shares`；③ 新增 `_ensure_holding`（查不到底仓自动建档）；④ 新增 `_resolve_convert`（双标的 · 双腿净值 · 两腿都成功才 `completed`）；⑤ 主流程三分支 + `unknown` 跳过；⑥ **份额优先**（有 `转出份额` 不依赖 `金额/净值`）；⑦ CLI 明细打印容错 |
| `src/briefing.py` | `_build_trade_summary` 增加 `状态 == completed` 过滤；convert 行渲染为 `转换 A → B` |
| `src/strategy.py` | `_check_cooldown`：① 只看 `completed`；② 大类改用 `infer_asset_class(代码, 名称)` 现算（原读全空的 `资产大类` 列 → 该功能**从未生效**）；③ convert 的转入腿计入冷却（D3） |
| `src/classification.py` | `infer_asset_class` 支持**无代码时按名称关键词判大类**（转换的转入标的只有名称） |
| `src/auto_bill_parser.py` | OCR prompt：action 扩展三值 + 新增 `target_product` / `transfer_shares` + **转换单 few-shot**；`FIELD_NAME_MAP` 加两列；None 值不写 |
| `tests/test_convert.py` | **新增 27 用例**（方向解析 / 份额解析 / 两腿正确 / 清仓删除 / 净值缺失熔断 / 标的匹配失败 / 份额驱动 / 用户填优先 / 下游 pending 过滤 / 冷却期） |

**三、顺带修的数据 bug**
- 底仓表 E 行 `标的代码` `017091` → **`019118`**（原与 A 行撞码，会让转入腿按 A 类净值折算）

**四、遗留（未做）**
- ⚠️ **iOS 快捷指令未改**（用户手动改，见 `docs/CONVERT_DESIGN.md` §3.5）。改造前用快捷指令记转换 → 仍会被 LLM 判成 `buy`。
- ⚠️ **尚未 commit / push**。

---

## 2. 📋 剩余待办（按 价值 ÷ 工时 排序）

> 排序依据：**用户价值** > **风险/债务** > **纯重构**。
> 工时估算是"一人专注干"的量级。

### 🔴 P0 —— 观察期做（本周，合计 < 1 天）

| # | 待办 | 工时 | 为什么现在做 |
|---|------|------|-------------|
| **1** | **观察 E+F 首个生产周期**（3-5 天，只做记录不改代码） | 0 | 刚上线的 diff / 跳过 / 叙事化**一次都没在真实推送里跑过**。看三件事：① 无变化时那句"按纪律维持不动"是否出现得合理；② 有变化时 AI 是否真的讲了"变了什么"而不是套话；③ 有没有整段空白/重复。观察结果决定 P1 的取舍 |
| **2** | **补 `briefing.py` 核心测试**（✅ diff 块已完成 10 用例；**剩 `hard_signals` / snapshot 读写两块**，约 6-8 个用例） | 1-2 h | 本轮刚在 `_diff_against_last` 抓到让核心功能完全失效的 bug，而它**零测试**。diff 已覆盖，但 hard_signals/snapshot 仍是裸奔 |
| **3** | **依赖对齐**：`pyproject.toml` 补 `openpyxl` / `exchange-calendars` / `litellm` / `PyYAML`（requirements.txt 有、pyproject 缺） | 10 min | `uv sync` 会静默缺包，属"改一行省一次排查" |

> ✅ **P0 #0 已实施完成（2026-09-17）**。代码改动明细见 §1.1；设计依据与决策记录见 `docs/CONVERT_DESIGN.md`。
> 表结构已改、代码已改、27 用例已过、真实 3 笔转换端到端仿真通过（C→28.23 / E→277.48）。
> ⚠️ **仍未做**：① iOS 快捷指令未改（用户手动改，步骤见下）；② 代码**未 commit / push**。
> ✅ 异常记录 `rid=recvvrYcrDyn7W` 已删除（用户确认是误录）。

#### ⏱ P0 #0 的后续动作（改造已完成，只剩"录数据"）

**结论：转换功能已就绪，现在可以录了；录入后由确认器在 9/21 自动补全。**

| 时间 | 动作 |
|------|------|
| 9/17（周四，今天） | ✅ convert 代码改造完成 + 表结构就绪。**iOS 快捷指令待用户手动改** |
| 改完快捷指令后（任意时间） | 用 3 个订单号录 3 行：`买卖方向=convert`、`产品名称`=C 类、`转入标的`=E 类、`转出份额`=200/50/30、`状态=pending`、`交易时间`=真实申请时间。**份额驱动，不必等净值** |
| 9/21（周一）净值公布后 | 跑 `python -m src.pending_resolver` → 自动回填确认份额/净值 → `completed`。这 3 笔顺带成为新功能的**端到端验证样本** |

**⚠️ 关键操作细节**：`交易时间` 必须填**真实申请时间** `2026-09-17 12:42:52 / 12:43:08 / 12:44:26`，**绝不能填录入当天**。因为 `pending_resolver._get_t_day` 是按 `交易时间` 反推 T 日的——填 9/19（周六）会被推到 9/21，确认器就会去取 **9/21 的净值**，而实际确认用的是 **9/17 的净值**，份额会算错。

**✅ 已同步修掉的连带问题**：`briefing.py::_build_trade_summary` 与 `strategy.py::_check_cooldown` 两个读取方均已加 `状态 == completed` 过滤 —— 录入的 pending 行不会再污染简报与冷却期。

**⚠️ 快捷指令未改之前**：用它记转换仍会被 LLM 判成 `buy` → 底仓静默失真。**改造完成前，转换请手工在表里录（按上表字段）。**

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
| **11** | **D1/D2 机器人命令**（`@机器人 雷达 / 早报 / 收盘` + `@机器人 买入 [名称] [金额]`） | 3-5 天 | 基建已就绪（`notify.send_card` / `FeishuPusher`）。交互价值高，但工程量大，等减负做完再上。<br>⚠️ **前置修复（2026-09-05 诊断）**：线上机器人已失联——①应用已不在任何群（`GET /im/v1/chats` 返回空）；②事件订阅是 WebSocket 长连接且只订了 `card.action.trigger`，没订 `im.message.receive_v1`，消息永远到不了 Render。修复：拉「Chen Yimin的智能助手」回群 + 回调改 Webhook（`https://ai-investment-server.onrender.com/feishu/webhook`）+ 订阅接收消息事件。用户确认当前以被动接收为主，此项暂缓 |
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
