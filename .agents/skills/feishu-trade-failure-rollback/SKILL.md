---
name: feishu-trade-failure-rollback
description: 处理「支付宝/银行提示交易失败，但飞书交易流水表里那笔已被系统按成功记账」——把底仓份额与成本价精确回滚，并把流水标记为 failed。当用户说"某笔交易失败了""支付宝提示交易失败""这笔没成交但我看表里记上了""帮我改一下表格""这个失败单怎么处理"时使用。
agent_created: true
---

# 交易失败单回滚（飞书账本）

## 0. 症状识别

用户说"支付宝提示我交易失败 / 没成交"，但交易流水表里那笔状态是 `completed`。

**关键认识**：这不是"没处理"，而是**按成功处理错了** —— `pending_resolver` 只看「T 日净值发布没发布」，
**从不校验交易是否真实成立**，所以它已经把份额加进底仓了。
⚠️ **只改流水不改底仓 = 没修**；只改底仓不改流水 = 统计里仍算一笔成功买入。

## 1. 先确认三件事（别跳过）

1. **支付宝里的最终状态**：完全失败且钱已退回？还是失败后重试成功了？（后者不同处理）
2. **流水状态**：已是 `completed` ⇒ 确认已记账（去查它的 `record-history-list` 可看到结算时刻）。
3. **同批次的其他单**（同一天、时间接近）是否也失败？逐笔核；顺带全表扫一遍有无残留 `pending`。
4. **卡片上有没有出现过 `📌 本时段自动入账 N 笔`**？（2026-09-24 起简报标题下方会有这条
   结算回执）→ 拿它列出的笔数对照支付宝逐笔核，**这是最快的定位方式**。

## 2. 定位两条记录

```bash
BT=$(grep -E '^FEISHU_BITABLE_TOKEN=' .env | cut -d= -f2)

# 流水行：按「交易单号」搜 → 取 record_id + 产品名称
lark-cli base +record-list --base-token $BT --table-id tblbnD3uaEdohjji \
  --limit 200 --format ndjson --output /tmp/trades.ndjson --as user
grep -E "<交易单号>" /tmp/trades.ndjson

# 底仓行：按「标的名称」搜 → 取 record_id
lark-cli base +record-list --base-token $BT --table-id tblpiht8ex94bM6x \
  --limit 100 --format ndjson --output /tmp/holdings.ndjson --as user
grep -E "<产品名关键词>" /tmp/holdings.ndjson
```

## 3. ⭐ 拿「结算前的精确值」

### 3a. 优先：读流水行的快照列（**2026-09-24 之后结算的单**）

交易流水表新增了 `结算前份额` / `结算前成本` 两列，由 `pending_resolver` 在写
`completed` 时一并写入（#38 L2，2026-09-24 落地）。§2 已导出 `trades.ndjson`，
直接从那行取：

```bash
lark-cli base +record-get --base-token $BT --table-id tblbnD3uaEdohjji \
  --record-id <流水rid> --as user
```

⇒ 直接读 `结算前份额` / `结算前成本`，**照抄即可，不必翻 history**。

⚠️ **两列都为空** ⇒ 属于下面两种情况，走 3b：
- 2026-09-24 **之前**结算的历史单（改造只对之后生效，不回填）
- **convert**（转换需要 4 个值：转出腿/转入腿各自的 prev 份额与成本，两列装不下）

### 3b. 兜底：查底仓变更历史

```bash
lark-cli base +record-history-list --base-token $BT \
  --table-id tblpiht8ex94bM6x --record-id <底仓rid> --as user
```

找 `持仓份额  before -> after` 那一行（`成本均价 before -> after` 通常同一时间戳）：

```
09-24 08:31   持仓份额  1185.69 -> 1243.29
              成本均价    1.57   -> 1.58
```

⇒ **`before` 值就是要写回的目标值**。

⚠️ **为什么不能算**：份额可以反推（加减无损），但**成本均价不行** ——
`pending_resolver._apply_buy()` 里是 `round(new_cost, 2)`，**有损舍入**，
用「当前份额 − 确认份额」反推可能落在四舍五入边界外。**必须拿 before 值。**

✅ 顺手交叉验证：`当前份额 × 现价` 应等于底仓的公式市值字段（对得上说明定位正确）。

## 4. 写入（两处，缺一不可）

```bash
# 4a 底仓：份额 + 成本均价（市值是公式 ROUND(持仓份额*现价,2)，自动重算，别手写）
lark-cli base +record-batch-update --base-token $BT --table-id tblpiht8ex94bM6x \
  --json '{"update_records":{"<底仓rid>":{"持仓份额":1185.69,"成本均价":1.57}}}' --as user

# 4b 流水：状态 → failed
lark-cli base +record-batch-update --base-token $BT --table-id tblbnD3uaEdohjji \
  --json '{"update_records":{"<流水rid>":{"状态":["failed"]}}}' --as user
```

⚠️ 若 `failed` 选项不存在（字段原仅 `pending`/`completed`）：先 `+field-get` **读全定义**，
再把整个定义（含全部 options）+ 新增项一起 `+field-update --yes` 提交
—— 它是**全量 PUT 语义，不是 patch**。

## 5. 验证（必须）

```bash
lark-cli base +record-get --base-token $BT --table-id tblpiht8ex94bM6x \
  --record-id <底仓rid> --as user
lark-cli base +record-get --base-token $BT --table-id tblbnD3uaEdohjji \
  --record-id <流水rid> --as user
```

⚠️ **必须用 `+record-get` 单条读** —— `+record-list` 返的是公式字段**缓存值**，刚改完会读到旧数。

## 6. 三个坑

1. 🔴 **绝不能把状态改回 `pending`** —— 下一轮 `pending_resolver` 会重新结算，**又加一次份额**。
2. 🔴 **别反推成本均价**（见 §3）。份额可反推，成本不行。
3. ⚠️ `failed` **零代码改动即生效**：`briefing._build_trade_summary`(L296) 与
   `strategy._check_cooldown`(L228) 都是 `!= "completed"` 就跳过。
   **`确认份额` / `确认净值` 保留作审计痕迹，别清空**（它们记录了"系统当时算了什么"）。

## 7. 收尾

- ⚠️ 若该笔标签是「**网格标的**」→ 提醒用户查支付宝失败原因（常见：银行卡限额 / 余额不足），**可能会重复失败**。
- 若该笔已进入某条简报，说明修正后**下一条**就正常，无需另做处理。
- 项目内完整版：`docs/FAILED_TRADE_SOP.md`（含根因分析与根治方案 #38）。
- 表格 ID / 字段口径见 `docs/PROJECT_MEMORY.md` §3。
- ✅ **2026-09-24 起**：#38 的 **L1 结算回执**（卡片顶部显示"本时段自动入账 N 笔"）与
  **L2 结算前快照**（§3a 的两列）已落地 ⇒ 定位与回滚都比首次事故时快。剩 L3（快捷指令自动标记）。
