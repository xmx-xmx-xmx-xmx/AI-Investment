---
name: feishu-bitable-lark-cli
description: 用 lark-cli 读写飞书多维表格与群消息的操作手册（AI-Investment 项目）。当需要查/改交易流水表、底仓表、雷达观测表、简报快照表，或导出群消息历史、做离线复算时使用。含命令模板、字段返回形态陷阱、写入语义陷阱。当用户说"查一下表里""导一下流水""群里那条推送说了什么""复算一下系统会说什么"时使用。
agent_created: true
---

# 飞书多维表格 lark-cli 操作手册

## 0. 铁律

- ⚠️ **本地禁止跑 `python -m src.xxx` 调飞书**（被 `is_production()` 拦；且会真写表）。本地唯一合规姿势 = **lark-cli 只读导出**，或把导出数据喂给纯函数离线复算。
- ⚠️⚠️ **macOS BSD grep 不支持 BRE 的 `\|` 转义**：`grep "港股\|恒生"` **静默返回空**（已两次据此误判"代码里没这个功能"）。
  → **排查性 grep 一律 `grep -E`**。用 Grep 工具（ripgrep）时注意它**默认跳过 `.github/` 等隐藏目录**，要直接 Read。

## 1. 基本变量

```bash
BT=$(grep -E '^FEISHU_BITABLE_TOKEN=' .env | cut -d= -f2)
```

⚠️ base token 用 `.env` 的 `FEISHU_BITABLE_TOKEN`。**快捷指令 URL 里是另一个 wiki 节点 token**，**lark-cli 只认前者**。

表 ID 从 `src/feishu_client.py` 的 `TABLE_MAP` 读，别硬记。

## 2. 读

```bash
# 全量导出（ndjson 每行一条记录）
lark-cli base +record-list --base-token $BT --table-id <tid> \
  --limit 200 --format ndjson --output /tmp/x.ndjson --as user

# 单条读 —— ⚠️ 公式字段只有它准
lark-cli base +record-get --base-token $BT --table-id <tid> \
  --record-id <rid> --as user

# 逐笔变更历史（排查"谁在什么时候改了什么"）
lark-cli base +record-history-list --base-token $BT --table-id <tid> \
  --record-id <rid> --as user

# 字段定义
lark-cli base +field-get --base-token $BT --table-id <tid> --field-id <fid> --as user
lark-cli base +field-list --base-token $BT --table-id <tid> --as user
```

⚠️⚠️ **`+record-list` 返回的是公式字段的缓存值** → 刚改完份额立刻读会拿到**旧数**。要准确值必须 `+record-get` 单条读。

## 3. 写

```bash
# 改记录（write 级，无需 --yes）
lark-cli base +record-batch-update --base-token $BT --table-id <tid> \
  --json '{"update_records":{"<rid>":{"字段":值}}}' --as user

# 改字段定义 —— ⚠️ high-risk，需要 --yes
lark-cli base +field-update --base-token $BT --table-id <tid> --field-id <fid> \
  --json '{"name":"状态","type":"select","multiple":false,"options":[{"name":"pending"},{"name":"completed"},{"name":"failed"}]}' \
  --yes --as user

# 建字段 —— ⚠️ 没有 --yes 标志
lark-cli base +field-create --base-token $BT --table-id <tid> \
  --json '[{"name":"结算前份额","type":"number"}]' --as user
```

三个写入陷阱：

1. ⚠️ **`+field-update` 是「全量 PUT 语义」，不是 patch** → 改一个 option 也必须**先 `+field-get` 读全定义**，再把完整定义提交，否则其余 option 会被抹掉。
2. ⚠️ **`+field-create` 没有 `--yes`**（不是所有 write 命令都有）；且**不接受 `property` 键**（`{"name":..,"type":"number","property":{...}}` 会报 `Unrecognized key(s)`）。想设格式就传最简形式，事后用 `+field-update`。
3. ⚠️ **写入了未知字段名 → 整个 update 失败**（不是部分成功）。所以新增字段类改动要「**先建字段、再上代码**」，或给代码加降级重试。

## 4. 群消息

```bash
lark-cli im +chat-messages-list --chat-id <chat_id> --as user \
  --order desc --start <date> --format ndjson > /tmp/msgs.ndjson
```

⚠️⚠️ **`im +chat-messages-list` 没有 `--output` 参数**（`base +record-list` 有）→ 只能 shell 重定向。
⚠️ **默认只返 50 条**，要看更多必须分页/追加时间窗。
⚠️ **解析必须跳过含 `_diagnostic` 的行**，否则 `json.loads` 会炸。
⚠️ `create_time` 有**两种形态**（秒级 / 毫秒级字符串），转换前先判位数。

## 5. ndjson 解析模板

```bash
python3 -c "
import json
for ln in open('/tmp/x.ndjson'):
    ln = ln.strip()
    if not ln or '_diagnostic' in ln: continue
    r = json.loads(ln)
    print(r.get('record_id'), r.get('字段名'))
"
```

## 6. ⚠️ 字段返回形态不统一

| 表类型 | 返回形态 |
|---|---|
| 文本 / 数字 / 公式 | **标量**（数字可能是字符串，如公式字段 `"284.18"`） |
| 单选 / 多选 | **数组**（`["固收资产"]`、`["网格标的"]`） |

⚠️ 只做 `str(field)` 会得到 `"['固收资产']"` 这种**带方括号的脏值** → 静默匹配不上任何大类。
→ 用项目的 `radar._field_text(value)` 归一（list 取首个，标量转 str）。

## 7. 离线复算（不触网、不碰飞书）

**这是"评估系统会说什么"的正确姿势** —— 把导出数据喂给纯函数：

- **策略侧**：`strategy.judge(portfolio, client=None)`，portfolio 元素映射成
  `{name, code, asset_class, shares, cost, latest_price, currency, tags, trend}`
- **雷达闸门**：`radar._calc_overweight_classes(holdings)`，holdings 直接喂 ndjson 记录

⚠️ 两者的 `asset_class` 都不该照抄底仓表的 `资产大类` 列（**手填且已失真**，亚洲半导体/韩国被标成港股）；判据要细化时用 `classification.infer_asset_class(code, name)` 现算（⚠️ 它对场外基金会触发 akshare 查询，**批量调用会拖慢/挂死 CI**）。

## 8. 常用排查配方

| 要查什么 | 怎么做 |
|---|---|
| 某笔交易是否已记账 | 流水表按单号搜 → `状态`；`completed` = 已结算 |
| 回滚失败单要的"结算前值" | ✅ 优先读流水行的 `结算前份额`/`结算前成本`；为空（历史单/convert）才 `+record-history-list` |
| 谁改的、什么时候改的 | `+record-history-list`，看 `field_changes` 的 before/after + `create_time` |
| 底仓份额为何对不上 | 按「同产品 + 同交易日多行」找脆弱批次（缓存覆写事故特征） |
| 总市值核对 | `当前份额 × 现价` 应等于公式字段 `市值` |
