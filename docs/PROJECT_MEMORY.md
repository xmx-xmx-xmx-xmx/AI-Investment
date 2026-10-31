# AI-Investment 项目长期记忆（工程红线与踩坑真源）

> 只留"不查文档就会做错"的红线，每条一句话。**待办唯一真源在 `TODO.md`；规则细则在 `docs/ACTION_RULES.md`；系统审计在 `docs/SYSTEM_AUDIT_20261005.md`。**
> 本文件与 `.agents/skills/` 下的 8 个标准技能一起入库版本控制，供 Claude Code / Antigravity / Cursor / WorkBuddy 等任何 AI 助手跨环境无缝复用。

## 1. 定位与协作
飞书多维表格基座 + Python 确定性计算偏离度与规则 + LLM 叙事翻译，GitHub Actions 零成本运行。核心入口 `python -m src.briefing <slot> [--compact|--full]`（6 常态时段 + `asia_open`）+ Render 的 `bot_server.py`。
- 排序原则：**用户价值 > 风险/债务 > 纯重构**。
- 📌 用户确认的三条定位（09-23）：记账**降级**为静默底座（与支付宝重复但**不能砍**）；**资讯推送是高价值模块**（须指向持仓映射）；核心诉求是**存量管理**（摊薄/止盈/集中度）与**忙人降噪**（锁屏一眼看懂、盘中红绿灯）。
- ⚠️ **工作模式：先定规则、再写代码**。规则未定，任何引擎都只输出"维持节奏"。
- ✅ **#33 规则引擎已全部落地**（`docs/ACTION_RULES.md`）：
  - 规则 1：同主题业绩差（普通持有桶，差 >20pp；**`compact` 默认模式下仅周六 `sat_morning` 注入**，`full` 模式下每日注入）
  - 规则 2：主动型同类分位 ≤30%（**周更·仅周六 `sat_morning` 卡**）
  - 规则 3：回本提醒（历史曾深亏 ≤ -10%，回到成本 ±3% 区间）
  - 规则 5 + Q5-C：单只 >15% 保险丝 + 分类别止盈（宽基含长期底仓从历史最高点回撤 ≥8pp 且仍在水上提示；主动/主题型到绝对线 +15% 提示）
  - 规则 6：资讯×持仓映射（`news_mapper.py`）
  - 实现 = `rules_engine.py` + `news_mapper.py` + `peer_rank.py`，卡片注入点 = 标题正下方（🎯 决策参考 / 📡 资讯·持仓关联 / 📡 同类对比）。
  - ⚠️ **跨运行状态必须存飞书**（CI 的 `data/` 每 run 重建）：底仓表 `历史最低收益率`/`回本提醒已发`/`历史最高收益率`/`止盈提醒已发`——首轮自动全量回填。
- ✅ **N9 忙人降噪补丁 + `compact`/`full` 双档开关**（10-31 落地）：
  - 默认 `DEFAULT_BRIEFING_STYLE = "compact"`（可通过 CLI `--full` / `--compact`、环境变量或 GitHub Actions Repository Variable `BRIEFING_STYLE`、或手动触发 `workflow_dispatch` 下拉框切换）。
  - `compact` 特征：① 锁屏动态摘要标题（`_build_dynamic_header`）；② 14:30 `closing` 卡片颜色充当红绿灯（`green` 有加仓机会 / `red` 风控拦截或保险丝 / `yellow` 维持定投不动）+ 3 行动作卡表头；③ 国内外要闻去重打分为「📰 核心要闻（3-4 条一句话）」（`_build_digest_news_block`），同时喂给 AI 完整上下文；④ 12:00 `midday` 极简百字午休卡（零 LLM 调用）。

## 2. 硬约束（违反会静默出错）
- **本地隔离**：本地禁止跑项目代码调飞书 API；`FeishuClient()` 必经 `is_production()`，统一用 `get_feishu_client_or_none()`。⚠️ `is_production()` **必须双平台**：`GITHUB_ACTIONS=="true"` **或** `RENDER=="true"`（Render 不设前者，曾漏判致机器人全坏）。
- **纪律唯一真源** `TARGET_WEIGHTS`（`src/constants.py`：**固收45 / 美股30 / A股5 / 港股10 / 避险10**）；宪法里权重**动态拼**，别手改字符串。⚠️ `strategy._determine_signal(deviation)` 的 deviation 是**百分点**（`actual - target`）。
- ⚠️ **快捷指令数字类型空值落表是 `0`**（`WFItemType=3`）→ 判断"填了没"**必须 `> 0`**，不能用 `is not None`。
- ⚠️ **快捷指令 few-shot 禁写真实业务值**（弱模型会照抄字母，E 类记成 C 类且**完全静默**）：取值类字段用占位符 + "以输入实际值为准"，形状类字段给完整合法 JSON。见 `docs/CONVERT_DESIGN.md` §3.5.5。
- ⚠️ **名称歧义安全网**：`_fuzzy_match_product` 命中**多只不同标的**（缺类别字母时同时命中 A/C/E）→ 返回 `None`；买入与 `_ensure_holding` **拒绝猜测 + ERROR 跳过**。
- ⚠️ **读流水必须按 `状态=="completed"` 过滤**（`briefing._build_trade_summary`、`strategy._check_cooldown`）。后者原读 `资产大类`（该列**从未被写入**）→ 冷却期从来没生效，现改 `infer_asset_class()` 现算。
- ⚠️ **`买卖方向` 列只由快捷指令写，代码从不写它**：`pending_resolver` 回写只写 `确认净值/确认份额/状态`（+港股 `产品名称`、+两个快照列），**绝不会清空方向**；09-17 前 88 行空是老版本没写这个键，**不回填**。
- **convert 铁律**（`docs/CONVERT_DESIGN.md`）：一次转换 = 流水**一行**。① `_parse_action` 未知方向必须 `"unknown"`，**绝不兜底 `"buy"`**；② 份额驱动；③ **D5 闸门**：`确认份额` 与 `确认净值` 同时填才算用户覆盖；④ 两腿都成功才 `completed`。⚠️ **口径 =「净值发布即记账」，比支付宝「份额到账」早 1-2 天**（属正常时间差）。

## 3. 飞书数据表
真源 = `src/feishu_client.py` 的 `TABLE_MAP`（**表 ID 从这里读，别硬记**）。5 张：底仓表（唯一真源，`市值` 是**公式**，改份额自动重算）· 交易流水表（14 列；含 `状态=failed` 选项 + `结算前份额`/`结算前成本`）· 雷达观测表 · 板块轮动配置表（12 条启用，09-20 起不独立成块）· 简报快照表（每时段一行覆盖写，签名 = **信息面指纹**）。
⚠️ **飞书字段返回形态不统一**：`资产大类`/`投资载体`/`标签` 是**数组**（`["固收资产"]`），`标的代码`/数字是标量。只做 `str()` 会得到 `"['固收资产']"` 这种脏值 → **静默匹配不上任何大类**。统一用 `radar._field_text()` 归一。
⚠️ 底仓表 `资产大类` **手填且存在跨类重叠**（亚洲半导体 `03486` + 韩国 KOSPI200 `03121` 被标「港股资产」）。

## 4. 展示层 vs AI 层 + E 变化感知
**展示要短、喂 LLM 要全，必须分开构造**（别共用变量再截断）。已改五刀：板块轮动 / 宏观日历 / LLM 输出卫生 / 要闻去重 / N9 核心要闻一句话合并。
⚠️ 三个坑：① 去重**必须先剥数字**（`跌0.36%`+`深证成指` 归一化成公共串 → 误合）；② `**思考过程：**` 闭合星号在冒号**之后**（正则尾 `[\s:：]*(?:\*\*|__)?[\s:：]*`），且兜底只救「结论在前、推理在后」；③ `_CENTRAL_BANK_DECISION_KEYWORDS` 按 ForexFactory 真实标题拟，别凭印象改。
⚠️ E 的签名**不能是卡片文本**（含 `HH:MM` → hash 恒变 → 判据恒 True），必须是信息面指纹 + 持仓阈值（¥50 / 0.3%）。⚠️ 交易日行情必然变 → `has_change` 几乎恒 True；`midday` 是唯一既不写快照也不比 diff 的时段。

## 5. 底仓一致性 & 「不会自愈」（09-19 事故）
⚠️ `name_to_rec` 运行开始只读一次 → **同一运行里第 2 笔起覆盖前一笔**（曾致 4 笔同批结算静默漏算份额，零报错）。修法：写回成功后 `_cache_apply()` 同步缓存；**写失败不得同步**。
`pending_resolver` 是**一次性结算机**（只筛 `status=="pending"`），置 `completed` 后**永不回看**；全仓**无「从流水重算底仓」逻辑** ⇒ **算错一次 = 底仓永久停在错值**。⚠️ **不能全量重算**（底仓有**表外历史**，会清零）→ 只能**增量对账**（`src/reconcile.py`）。
✅ `_get_t_day()`：15:00 前 → 当天，否则顺延下一交易日 → 周末录入**不会**永久 pending。

## 6. 抓取超时、CI 稳定性与防双推（N5 / N8）
✅ `src/net_guard.py`：`import_ak()`/`import_yf()` 替代裸 import（26 处 / 6 文件）。⚠️ `market_data.py` 走外层函数壳、**不经** net_guard —— **两套超时并存是刻意的**。
⚠️⚠️ **`with_timeout(s, fallback)` 返回的是「装饰器」不是已包装函数**。写成 `with_timeout(15,None)(fn, *args)` 会把业务参数喂给装饰器 → `TypeError` → **静默降级到兜底源（真打网络）**。必须 `guarded = with_timeout(...)(fn)` 再 `guarded(*args)`。
⚠️ **别用 `ThreadPoolExecutor` 做超时**（worker 非 daemon + `atexit` join → 挂死线程把**解释器退出**一起卡死），必须 daemon 线程 + `join(timeout)`。
🔍 **"漏推"≠"没触发"**：先查 Actions 运行记录。上限 **25 分钟**。诊断用技能 `github-actions-run-diagnosis`。
🔍 `daily-run.yml`：`workflow_dispatch`（飞书 bot 主触发）+ 6 条 `schedule` cron（兜底）→ **Step 0 = `cron_guard`** → **Step 1 = `pending_resolver`**（结果落盘 `data/pending_resolve_result.json` 供下一步读）→ **Step 2 = `briefing <MODE>`**。
✅ **N5 + N8 防双推铁律（`src/cron_guard.py`）**：
- GitHub Actions 免费 runner 在高峰期 `schedule` 事件排队延迟可达 **4–5 小时**（例如 09:15 cron 延迟到 14:08 才启动、15:15 cron 延迟到 20:45 才启动）。
- 因此 `cron_guard.py` **严格拆分两套时间窗**：
  1. `DISPATCH_WINDOWS`（宽窗口，供 `workflow_dispatch` 认领与快照判重）
  2. `FALLBACK_WINDOWS`（紧凑兜底窗口，仅供 `schedule` 事件认领，**起点恒晚于飞书 Bot 触发时刻约 15 分钟，且超期直接弃跑 exit 0**，严禁延迟数小时的早间/收盘 cron 跨界认领到午盘/收盘/夜盘时段抢跑双推）。
- 周末飞书 Bot 同样在周六 09:00 / 周日 19:00 准时触发，因此周末 cron 兜底设在 Bot 后 45 分钟（周六 `45 1 * * 6` = 北京 09:45；周日 `45 11 * * 0` = 北京 19:45）。

## 7. 节假日前置熔断与 2027+ 日历自愈（`holiday_gate.py`｜N6）
三市场对称 `{cn:XSHG, hk:XHKG, us:XNYS}`；`holiday_notice()` ⚠️ **只在部分市场休市时返回内容**（全开/全休都返 `""`）。
✅ **N6 A 股 2027+ 交易日历动态自愈已落地**：当 `exchange-calendars` 的 `XSHG` 日历越界（如 2027-01-01 起）抛 `DateOutOfBounds` 时，自动调用新浪/akshare 接口（`tool_trade_date_hist_sina`）拉取最新官方交易日历并持久化至 `data/cn_trading_days.json`，彻底告别每年年底人工维护。
⚠️⚠️ **不要给 `us_index`/`us_etf` 加开市守卫**：早间 08:30 要用的是几小时前刚收盘那场，此时 `is_us_market_open(今天)` 恒 False → 加守卫会**误杀最有用的时点**。⚠️ 港股休市时四个数据点统一**不给误导性陈旧值**。

## 8. Render 部署
⚠️ **`Port scan timeout` 偶发**（平台侧抖动）：**失败时保留上一成功版本** → 先 `curl .../health`，**200 就没坏**。诱因是短时间连推多 commit → **小改攒一次推**。⚠️ **判断简报改动何时生效看 GitHub Actions，不是 Render**。仓库**无 `render.yaml`**（手动步骤见 `docs/MANUAL_OPS.md` #25）。

## 9. 依赖与测试
⚠️ **CI 走 `requirements.txt`**（不经 `pyproject.toml`）；`uv.lock` 被 gitignore。**`litellm` 是死依赖，别补回去**。
**测试 667 例 / 28 文件**（2026-10-31）。⚠️ **测 `briefing._build_*`：先数清它有几个外部数据源并全部 mock**（同时 mock `net_guard.import_ak/yf` **和** `market_data.fetch_us_*`）。
✅ **`tests/test_builder_smoke.py`**（20 例）：6 个 slot × `compact`/`full` 双档逐个构建卡片 + 离线耗时上限。**打桩要诀**：`import_ak/import_yf` 直接返 `None` 即可，行情一律返 `None`（**别用 MagicMock**：`f"{x['close']:.2f}"` 会让 MagicMock 的 `__format__` TypeError），`judge_from_feishu` 桩要带 `total_value`（closing 硬取）。
⚠️⚠️ **接线新函数后必跑 `tests/test_undefined_names.py`**：用 `symtable` 全量扫「引用未定义名」，毫秒级、零依赖，专防局部导入漏写导致的 `NameError`。
⚠️ **本地跑 `python -m src.briefing <slot>`（不带 `--dry-run`）会真调飞书 webhook 推送**（`FeishuPusher` 是 webhook，**不受** `is_production()` 约束）→ 本地必加 `--dry-run` 或只调 builder 函数。⚠️ `_build_asia_pacific_market` 会**就地 pop 代理变量** → fixture 还原。⚠️ 写快照的测试要把 `_SNAPSHOT_FIXTURE_PATH` 重定向到 `tmp_path`。
⚠️ **用项目 `.venv/bin/python`**。

## 10. 交易失败单处理（全文 `docs/FAILED_TRADE_SOP.md`｜技能 `feishu-trade-failure-rollback`）
**症状**：支付宝提示失败，但流水那笔已 `completed`（系统按成功记账并加进底仓）。**根因**：`pending_resolver` 唯一判据是「T 日净值发布没发布」，**从不校验交易是否成立** ⇒ **失败单不管 = 份额永久虚增**。
✅ **优先读流水快照列** `结算前份额`/`结算前成本`，**不必翻 history**；⚠️ 两列为空 = 历史单或 convert → 才 `+record-history-list` 拿 before 值（🔴 **份额可反推、成本均价不行**，`_apply_buy()` 是 `round(...,2)` 有损）。
🔴 **绝不能改回 `pending`**（下一轮会重新结算一遍，又加一次）。✅ `failed` **零代码改动即生效**（消费点都是 `!= "completed"` 就跳过）。
✅ **L1 回执已落地**：卡片标题下方显示 `📌 本时段自动入账 N 笔` → **定位失败单最快的入口**。⏳ 剩 **L3**（快捷指令扫失败短信自动标记 + 回滚）。

## 11. #32 雷达超配闸门
**`radar.OVERWEIGHT_BLOCK_PP = 5.0`**：标的所属大类偏离 ≥ +5pp → **抑制**（非改写）买入类信号，修的是"对已严重超配的债券基金发 🟢 趋势加速"的反向误导。⚠️ 阈值与 `strategy._determine_signal` 的 ±5 pp 口径一致（**别改成 0**：轻微偏离会静默掉全部信号）。
⚠️ **大类取底仓表「资产大类」列，不用 `infer_asset_class()`** —— 后者对场外基金会触发 akshare 查询，扫几十只标的等于给 CI 引入新的挂死点。
⚠️ **不在底仓的标的（雷达观测表的纯观察对象）一律放行**：没有权重可算，**宁可漏拦不可误杀**。底仓表读取失败时闸门静默不生效、扫描照常。

## 12. 排查工具与网络通道
- **git push**：默认 `git -c http.proxy=http://127.0.0.1:7890 -c https.proxy=http://127.0.0.1:7890 push origin main`。
  🔴 **若 7890 端口未监听**（报 `Connection refused`），用**备用直连通道（已验证可用）**：`git -c http.curloptResolve=github.com:443:140.82.112.3 push origin main`。
  机理：`github.com` 的 DNS 可能解析到不可达 IP，而 `140.82.112/113/114.x` 段可达。**查询类操作（Actions 运行记录/commits）可直连 `api.github.com`，不需要代理**。
- ⚠️⚠️ **macOS BSD grep 不支持 BRE 的 `\|` 转义**：`grep "港股\|恒生"` **静默返回空** → **排查性 grep 一律 `grep -E`**。
- 🔍 **查/写飞书、导群消息、字段形态陷阱、离线复算姿势** → 见 `.agents/skills/feishu-bitable-lark-cli/SKILL.md`。
- 🔍 **判断"某时段到底推没推"最硬的证据 = 查推送群消息**：群「投资驾驶舱」（`chat_id` 形如 `oc_b347…`，用 `lark-cli im +chat-list --as user` 复核）。⚠️ `im +chat-messages-list` 没有 `--output`，直接 `--order desc` 拉最近 50 条看首条时间最可靠；若 `lark-cli` 用户 token 过期（`RC: 4`），可先通过公开 GitHub Actions REST API 查 `runs` / `jobs` 步骤状态确认推送步骤是否成功。
- 🔍 **LLM 降级链**：主 `DeepSeek-V3.2`(1024) → 备 `Qwen3.5-9B`(512 填空式) → 纯文本兜底；代金券**只覆盖 Qwen 系列**。**吞字防护**：`finish_reason=="length"` → `_truncate_at_sentence_boundary`。

## 13. 持仓结构认知与通用判据
- **29 条持仓 = 13 个底层主题**（⚠️ **A/C/E 是费率外壳，不是分散**）；分仓标签：长期底仓 16 只 / 普通持有 10 只 / 观察仓 3 只。
- ⚠️ 三个通用判据：① **指数基金不能用「跑输同类」判据**；② **摊薄只对均值回归型（宽基/债券）有意义，对单一行业主题有害**；③ **机械绝对止盈会"卖飞"宽基**（宽基用从最高点回撤 ≥8pp 止盈且长期底仓同样生效；主动/主题型用 +15% 绝对止盈线）。

## 14. 文档与技能库索引
- **核心文档**：`TODO.md`（待办唯一真源）· `CLAUDE.md`（本地隔离硬规范）· `docs/ACTION_RULES.md`（规则唯一来源）· `docs/PROJECT_MEMORY.md`（本文件）· `docs/SYSTEM_AUDIT_20261005.md`（全模块深度审计）· `docs/CONVERT_DESIGN.md` · `docs/FAILED_TRADE_SOP.md` · `docs/MANUAL_OPS.md`。
- **仓库内置 Agent Skills（`.agents/skills/`，共 8 个）**：
  1. `ai-investment-verify-release`：本地测试、干跑简报与代理/直连发布 SOP
  2. `feishu-bitable-lark-cli`：飞书多维表格与群消息 `lark-cli` 读写规范及字段形态陷阱
  3. `feishu-trade-failure-rollback`：支付宝/银行失败单回滚底仓份额与成本 SOP
  4. `github-actions-run-diagnosis`：无 `gh` CLI 环境下用 REST API 诊断 Actions 超时/排队/失败（附 `scripts/run_durations.py` 与 `references/timeout-guard.md`）
  5. `icloud-shortcut-inspector`：iOS 快捷指令 iCloud 链接逆向解析与复核
  6. `portfolio-attribution-review`：全组合收益归因体检（行情/配置/节奏三段拆解）
  7. `qdii-share-class-advisor`：场外 QDII 基金 A/C/E 份额选择、FIFO 赎回费与限额窗口决策
  8. `feishu-ledger-spending-analysis`：飞书个人记账本消费分析与四口径储蓄率模型

