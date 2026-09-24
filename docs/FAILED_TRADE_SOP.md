# 交易失败处理 SOP（`failed`）

> **场景**：支付宝 / 银行提示「交易失败」，但交易流水表里那笔已经是 `completed`
> —— 系统已经按「成功」把它记进了底仓。
>
> **首次实战**：2026-09-24 · 万家纳斯达克100指数（QDII）C · ¥100 · 单号 `20260922001080012204660040841236`

---

## 一、为什么会发生

`pending_resolver` 的**唯一判据是「T 日净值发布没发布」**，它**从不校验交易是否真实成立**。
支付宝的失败对它完全不可见。

而它是**一次性结算机** —— 只筛 `状态 == "pending"`，置 `completed` 后**永不回看**；
全仓库也没有「从流水重算底仓」的逻辑。

> ⇒ **失败单若不管，虚增的份额会永久留在底仓里，而且完全静默。**

两个放大因素：

1. **QDII 净值 T+1/T+2 才发布** → 失败往往要 **1–2 天后**才以「错误记账」的形式暴露
   （9/22 提交的单，9/24 早上才结算 —— 因为要等 9/22 的净值）。
2. **结算结果对用户不可见**。`pending_resolver` 是 `daily-run.yml` 的 **Step 0**
   （每个时段都先跑，注释写着"风雨无阻"），但它的**输出被直接丢弃**，
   既没传给 `briefing` 也没进卡片 → 用户不知道自己被记账了。

---

## 二、处理四步（约 1 分钟）

设 `BT` = `.env` 里的 `FEISHU_BITABLE_TOKEN`。

### 1. 找到结算前的值 —— **优先读流水的快照列**

**⭐ 2026-09-24 之后结算的单**：流水行里已经直接记着 `结算前份额` / `结算前成本`
（L2 已落地），**照抄这两个值即可，不必翻历史**：

```bash
BT=$(grep -E '^FEISHU_BITABLE_TOKEN=' .env | cut -d= -f2)
lark-cli base +record-list --base-token $BT --table-id tblbnD3uaEdohjji \
  --limit 200 --format ndjson --output /tmp/trades.ndjson --as user
grep -E "<单号>" /tmp/trades.ndjson      # 取 record_id + 结算前份额/成本
```

**更早的历史单（快照列为空）** 才需要翻变更历史。先按产品名在底仓表定位：

```bash
lark-cli base +record-list --base-token $BT --table-id tblpiht8ex94bM6x \
  --limit 100 --format ndjson --output /tmp/holdings.ndjson --as user
grep -E "万家纳斯达克" /tmp/holdings.ndjson      # 从中取 record_id
```

再查该行的历史，**取结算前的精确值**：

```bash
lark-cli base +record-history-list --base-token $BT \
  --table-id tblpiht8ex94bM6x --record-id <底仓record_id> --as user
```

找 `持仓份额  before -> after` 那一行（`成本均价 before -> after` 通常同一时间戳）：

```
09-24 08:31   持仓份额  1185.69 -> 1243.29
              成本均价    1.57   -> 1.58
```

**`before` 值就是要写回的目标值**（上面即 1185.69 / 1.57）。

### 2. 改底仓

```bash
lark-cli base +record-batch-update --base-token $BT --table-id tblpiht8ex94bM6x \
  --json '{"update_records":{"<底仓record_id>":{"持仓份额":1185.69,"成本均价":1.57}}}' --as user
```

`市值` 是公式字段（`ROUND([持仓份额]*[现价],2)`），会**自动重算，不要手写**。

### 3. 改流水状态

流水 `record_id` 按 `交易单号` 在流水表里搜。

```bash
lark-cli base +record-batch-update --base-token $BT --table-id tblbnD3uaEdohjji \
  --json '{"update_records":{"<流水record_id>":{"状态":["failed"]}}}' --as user
```

### 4. 验证

```bash
lark-cli base +record-get --base-token $BT --table-id tblpiht8ex94bM6x \
  --record-id <底仓record_id> --as user
lark-cli base +record-get --base-token $BT --table-id tblbnD3uaEdohjji \
  --record-id <流水record_id> --as user
```

⚠️ **必须用 `+record-get` 单条读** —— `+record-list` 返回的是公式字段的**缓存值**，刚改完会读到旧数。

---

## 三、三个坑

1. 🔴 **绝不能把状态改回 `pending`**
   下一轮 `pending_resolver` 会把它**重新结算一遍**，又加一次份额。

2. 🔴 **不要用「当前份额 − 确认份额」反推成本均价**
   *份额*可以反推（加减无损），但*成本均价*不行 —— `_apply_buy()` 里是 `round(new_cost, 2)`，
   有损舍入，反推结果可能落在四舍五入边界外。
   **必须查 history 拿精确值。**

3. ⚠️ `failed` 是 **2026-09-24 新增**的选项（原字段只有 `pending` / `completed`）。
   若迁移或重建表，需要重新加：

   ```bash
   lark-cli base +field-update --base-token $BT --table-id tblbnD3uaEdohjji \
     --field-id fldkbJHmih \
     --json '{"name":"状态","type":"select","multiple":false,"options":[
       {"name":"pending","hue":"Blue","lightness":"Lighter"},
       {"name":"completed","hue":"Orange","lightness":"Lighter"},
       {"name":"failed","hue":"Red","lightness":"Lighter"}]}' --yes --as user
   ```

   （`+field-update` 是**全量 PUT 语义**，不是 patch —— 必须先把当前定义读全再提交。）

---

## 四、`failed` 为什么不用改代码

| 消费点 | 判据 | 行为 |
|---|---|---|
| `briefing._build_trade_summary` 第 296 行 | `str(status) != "completed"` | 跳过 → **不计入交易汇总** |
| `strategy._check_cooldown` 第 228 行 | `_one(rec.get("状态")) != "completed"` | 跳过 → **不计入冷却期** |

两处都是「不等于 `completed` 就忽略」，所以标成 `failed` 后**自动从所有统计中消失**，零代码改动。

保留 `确认份额` / `确认净值` 作为「系统当时算了什么」的审计痕迹，**不要清空**。

---

## 五、根治方案（待办 #38）

> ✅ **L1 + L2 已于 2026-09-24 落地**（见 `TODO.md` §1.17）。⏳ 只剩 L3。

| 层 | 做法 | 状态 | 解决什么 |
|---|---|---|---|
| **L1 结算回执** | 每次 `pending_resolver` 结算后把结果落盘 `data/pending_resolve_result.json`，`briefing` 读出来摆到**卡片标题正下方** | ✅ **已完成** | **让失败单有机会被看见**（此前完全静默）。⚠️ 接入点是**文件桥**：Step 0 与 Step 1 是两个进程，不能传内存变量 |
| **L2 快照回滚** ⭐ | 流水表加 `结算前份额` / `结算前成本` 两列，`pending_resolver` 写 `completed` 时一并写入 | ✅ **已完成** | **回滚不用再翻 history**，且精确无损 |
| **L3 快捷指令标记失败** | 扫支付宝失败短信 → 解析单号 → 找流水行 → 置 `failed` + 按 L2 快照回滚 | ⏳ 待做 | 用户零学习成本（复用现有拍照录入习惯） |

⚠️ L3 若要做，注意快捷指令的**数字类型空值落表是 `0`**（判断"填了没"必须用 `> 0`，不能用 `is not None`）。

**L2 的两个已知边界**（写在代码注释里，改之前先看）：
1. **只覆盖 buy/sell**：convert 需要 4 个值（转出腿/转入腿各自的 prev 份额与成本），两列装不下；
   且它已有「两腿都成功才 completed」的原子性保障。
2. **历史记录没有快照值**：只对 2026-09-24 之后结算的单生效。更早的失败单仍得翻 `record-history`。

---

## 六、顺带自查清单

发现一笔失败后，**顺手确认这几件事**：

- [ ] **同批次的其他单**（同一天、时间接近）是否也失败了？→ 按单号逐笔核
- [ ] 这笔的**标签**是不是「网格标的」？如果是网格自动单，**下一期可能还会失败**
- [ ] 支付宝里**钱退回来没有**？（退款不影响表，但如果没退需要追）
- [ ] 该产品**底仓份额是否对得上**（改完后用 `+record-get` 复核一次）

---

_首次实战记录：2026-09-24 · 万家纳斯达克100指数（QDII）C_
_`1185.69 → 1243.29`（+57.6 份 / ¥100）已回滚；市值 `2158.48 → 2058.48`；收益率 `9.88% → 10.58%`_
