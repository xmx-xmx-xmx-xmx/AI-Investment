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
| `pending_resolver.py` 行数 | 944（+convert 分支 + 名称歧义安全网） |
| 测试 | **212 用例**，覆盖 9 个模块（macro_calendar / market_data / global_news / radar / strategy / briefing-diff / **convert** / **holding-match**）；实测 `pytest -q` 全绿 |
| 飞书表 | 5 张（底仓 / 交易流水 / 雷达观测 / 板块轮动配置 / 简报快照表 `tblxJqf6BT5GfhGh`） |
| 交易流水表字段 | 12 列（2026-09-17 新增 `转入标的` / `转出份额`，`买卖方向` 加 `convert`） |
| 转换链路状态 | ✅ 代码已 push（`883d234`）；✅ iOS 快捷指令已手动改完并**真机实测通过**（3 笔转换成功入表，见 §1.2） |
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
| `src/pending_resolver.py`（第二轮加固） | **名称歧义安全网**：`_fuzzy_match_product` 归一化后收集**全部**候选，命中多只不同标的（如缺份额类别字母时同时命中 A/C/E）→ 返回 `None`（旧行为是返回第一个命中 = 静默记错类别）；新增 `_find_holding_conflicts` / `_fmt_conflicts`；买入分支与 `_ensure_holding`（两腿）均**拒绝猜测 + 告警跳过**，不落"新品建底仓"分支 |
| `tests/test_holding_match.py` | **新增 9 用例**：带字母各归各类 / 不同字母不互相命中 / 单候选不误伤 / 全半角等价 / 缺字母端到端跳过（买入 + 两条转换腿，且两腿都不落库） |

**三、顺带修的数据 bug**
- 底仓表 E 行 `标的代码` `017091` → **`019118`**（原与 A 行撞码，会让转入腿按 A 类净值折算）

**四、遗留（已全部闭环，2026-09-17 收尾）**
- ✅ **iOS 快捷指令已改完**（用户手动改；步骤见 `docs/CONVERT_DESIGN.md` §3.5，改后自检清单见 §3.5.3）
- ✅ **代码已 push**：`883d234`(代码+测试) → `bfd7790`(Qwen_Core 结案) → `518b9aa` / `17bb37a` / `4f5c6da` / `e4427af`(文档)，本地与 `origin/main` 已对齐
- ✅ **真机实测通过**：3 笔 C→E 转换成功入表（见 §1.2）

---

### 1.2 P0 #0 真机实测（2026-09-17）

**3 笔转换已入表待确认**（`tblbnD3uaEdohjji`，当前表内唯一 pending）：

| record_id | 买卖方向 | 产品名称 → 转入标的 | 转出份额 | 交易时间 | 状态 |
|---|---|---|---|---|---|
| `recvvswDmM8RAI` | convert | (QDII)C → (QDII)E | 200 | 09-17 12:42:52 | pending |
| `recvvswMoKj1k8` | convert | (QDII)C → (QDII)E | 50 | 09-17 12:43:08 | pending |
| `recvvswSTr6EbT` | convert | (QDII)C → (QDII)E | 30 | 09-17 12:44:26 | pending |

单号 / 时间 / 份额 / 转入标的全部正确，快捷指令改造已端到端验证。

**第 4 笔：46.93 买入（✅ 已修正为 E 类，2026-09-17 15:3x 用户在用户端修正）**

| record_id | 买卖方向 | 产品名称 | 交易金额 | 交易时间 | 状态 |
|---|---|---|---|---|---|
| `recvvsEpJZemVl` | buy | …ETF联接**(QDII)E** | 46.93 | 09-17 12:51:46 | pending |

**修正经过**：首录（`recvvsAN850K7v`）时表内产品名被写成 **C 类**，用户核对后删除该行、**改提示词重新拍了一次** →
新行 `产品名称` 正确为 `…(QDII)E`。本地核对 `_fuzzy_match_product` 精确命中 `019118`（E 类，底仓 0 份）。
根因与通用教训见 `docs/CONVERT_DESIGN.md` **§3.5.5**（few-shot 示例里写了真实基金名 → 模型照抄示例的类别字母）。

`买卖方向=buy` / `交易金额=46.93` / `交易时间=12:51:46` / `状态=pending` 全部正确。按 9/15 净值估算约 **16.53 份**。

**后续无需人工干预**：`daily-run.yml` 的 **Step 0 无条件先跑 `python -m src.pending_resolver`**，所以之后每次简报触发都会先对账。

| 时间 | 动作 | 状态 |
|------|------|------|
| 9/17 起每次触发 | 确认器对账；QDII 净值未发布 → 保持 `pending` + 告警（**设计如此，非故障**） | ⏳ 进行中 |
| 预计 9/18 晚 ~ 9/19 | 9/17 净值入库 → 4 笔 pending 一起成交：转换两腿（C `308.23`→**28.23** 份 / E `0`→**≈277** 份）+ E 类买入 46.93（**≈16.53 份**）+ 流水全部置 `completed` | ⏳ 待触发 |
| 回填完成后 | 顺手核对简报持仓段与冷却期是否正常（冷却期功能本轮才刚修活） | — |

**⚠️ 四条已固化的约束（勿忘）**
- `交易时间` 必须填**真实申请时间**（本次已填对）。`_get_t_day` 按它反推 T 日，填录入当天会把确认器引到错误的净值日。
- **数字类字典项空值会落成 `0` 而非留空**。所有"用户是否填了 X"的判断必须用 `> 0`，不能用 `is not None`。
- **`买卖方向` 只有快捷指令会写，项目代码从不写它**（`pending_resolver` 只回写 `确认净值/确认份额/状态`）。因此 **2026-09-17 之前的 88 行历史记录该列全空**，且**不是被"完成"清掉的**。影响面见下。
- **提示词的 few-shot 示例里不得写真实业务值**（尤其产品名 / 份额类别字母）。示例会给"形状 + 值"双重信号，弱模型会照抄 → 类别字母抄错是**完全静默**的错账。取值类字段一律用占位符（如 `基金名称`）。见 `docs/CONVERT_DESIGN.md` §3.5.5。

**🔍 历史 88 行 `买卖方向` 为空的影响评估（已核实）**

| 读取方 | 行为 | 影响 |
|---|---|---|
| `briefing._build_trade_summary` | 仅看**近 5 日**，渲染成 `{日} {方向} {产品} ¥{额}` | 空方向 → 少个 "buy" 字样，金额/产品名仍在，AI 仍能读懂。**可忽略** |
| `strategy._check_cooldown` | 要求 `方向 == buy/convert`，其余 `continue` | 历史买入对冷却期**不可见**。但 `COOLDOWN_DAYS = 3`，9/14 及更早的本来就过期；**只有 9/16 那笔（博时红利低波 A，A股资产）落在窗口内却看不见**。影响很小 |
| `pending_resolver` | 只筛 `状态=pending`，历史行都是 `completed` | 无影响 |

→ **结论：不建议回填**。收益接近零（最坏情况漏一次冷却提示），而补录方向需要逐行人工判断（`交易金额` 在卖出时是"到账金额"，无法从行数据反推买卖）。今天起的记录已经能正确写入了。

**🔍 已闭环的小事**
- ✅ 示例单号：`docs/CONVERT_DESIGN.md` §1.5 与提示词 few-shot 示例原写作 `202609170100…`，表内真实值 `202609170010…`（第 10-11 位互换）。**不影响运行**（实测单号均被正确 OCR），已改为真实值。
- ✅ 示例基金名：原示例写了真实基金名（结尾 `C`），导致 E 类买入被记成 C 类。已改为占位符 `基金名称` + 规则 2 显式要求按截图识别类别字母（用户侧已生效），并在代码侧补了名称歧义安全网。

---

## 2. 📋 剩余待办（按 价值 ÷ 工时 排序）

> 排序依据：**用户价值** > **风险/债务** > **纯重构**。
> 工时估算是"一人专注干"的量级。

### 🔴 P0 —— 观察期做（本周，合计 < 1 天）

| # | 待办 | 工时 | 为什么现在做 |
|---|------|------|-------------|
| **1** | **观察 E+F 首个生产周期**（3-5 天，只做记录不改代码） | 0 | 刚上线的 diff / 跳过 / 叙事化**一次都没在真实推送里跑过**。看三件事：① 无变化时那句"按纪律维持不动"是否出现得合理；② 有变化时 AI 是否真的讲了"变了什么"而不是套话；③ 有没有整段空白/重复。观察结果决定 P1 的取舍 |
| **2** | **补 `briefing.py` 核心测试**（✅ diff 块 10 用例已完成；**剩 `hard_signals` / snapshot 读写两块**，约 6-8 个用例）——已核实 `tests/` 里这两块**零命中** | 1-2 h | 本轮刚在 `_diff_against_last` 抓到让核心功能完全失效的 bug，而它**零测试**。diff 已覆盖，但 hard_signals/snapshot 仍是裸奔 |
| **3** | **依赖对齐**：`pyproject.toml` 补 `openpyxl` / `exchange-calendars` / `litellm` / `PyYAML`（requirements.txt 有、pyproject 缺，已核实） | 10 min | `uv sync` 会静默缺包，属"改一行省一次排查" |
| ~~**0b**~~ | ✅ **核对 46.93 那笔的产品类别 —— 已闭环** | — | 用户删除错行后改提示词重录，新行 `recvvsEpJZemVl` 产品名为 **E 类**（正确）。代码侧同时补了**名称歧义安全网**，同类问题今后会显式跳过 + 告警而非静默记错。详见 §1.2 与 `docs/CONVERT_DESIGN.md` §3.5.5 |

> ✅ **P0 #0 已闭环（2026-09-17）**：表结构已改 · 代码已改 + 测试已过 · 代码已 push · 快捷指令已改完 · **真机录 4 笔成功入表**（3 转换 + 1 买入）· 名称歧义安全网已补。
> 全量测试 `212 passed`。完整实测记录、后续自动回填时间表与三条固化约束见 **§1.2**；两次踩坑（POST body 漏键 / few-shot 示例值被照抄）见 `docs/CONVERT_DESIGN.md` §3.5.3 与 §3.5.5。
> ✅ 异常记录 `rid=recvvrYcrDyn7W` 已删除（用户确认是误录）；✅ 首录错类别的 `rid=recvvsAN850K7v` 已由用户删除并重录。

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
