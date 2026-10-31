---
name: icloud-shortcut-inspector
description: 下载并解析 iCloud 共享链接（https://www.icloud.com/shortcuts/<id>）指向的 iOS 快捷指令，还原它的动作序列、参数、提示词原文与 HTTP 请求体。当用户发来 iCloud 快捷指令链接、要求"看看这个快捷指令怎么运行的""它写了哪些字段""它调用了什么 API"时使用。
agent_created: true
---

# iCloud 快捷指令逆向解析

用户发来 `https://www.icloud.com/shortcuts/<recordId>` 时，**不要**用 WebFetch 抓那个页面（只是一层 JS 壳，拿不到逻辑）。走下面的接口。

## 1. 取元数据

```bash
curl -s -A "Mozilla/5.0" \
  "https://www.icloud.com/shortcuts/api/records/<recordId>" \
  -o /tmp/shortcut_meta.json
```

返回 JSON，`fields` 下关键项：

| 键 | 含义 |
|----|------|
| `name.value` | 快捷指令名称 |
| `shortcut.value.downloadURL` | **未签名 plist**（要解析的就是它） |
| `signedShortcut.value.downloadURL` | 签名版（一般不需要） |
| `signingStatus.value` | `APPROVED` = 已通过审核 |

## 2. 下载 shortcut 文件

downloadURL 形如 `https://cvws.icloud-content.com/B/<checksum>/${f}?o=...`

**把 `${f}` 替换成任意以 `.shortcut` 结尾的文件名即可**（例如 `shortcut.shortcut`）。
用中文原名会 500，别试。URL 里的签名参数原样保留，不要重排。

```bash
curl -s -L -A "Mozilla/5.0" -o /tmp/kuaijie.shortcut "<替换后的URL>"
ls -l /tmp/kuaijie.shortcut   # 字节数应与 shortcut.value.size 一致
```

## 3. 解析 plist

⚠️ **必须在同一条 Bash 调用里完成下载+解析**——沙箱的 `/tmp` 不跨调用保留。

```python
import plistlib, json
pl = plistlib.loads(open('/tmp/kuaijie.shortcut','rb').read())
pl['WFWorkflowActions']            # 动作列表
# 每个动作：WFWorkflowActionIdentifier + WFWorkflowActionParameters
```

关键动作标识（去掉 `is.workflow.actions.` 前缀）：

| 标识 | 含义 |
|------|------|
| `takescreenshot` / `extracttextfromimage` | 截屏 / 本地 OCR |
| `gettext` | 内嵌长文本（**提示词通常在这里**） |
| `runworkflow` | 调用**子快捷指令**（参数 `WFWorkflow.workflowName` 给出名字） |
| `getvalueforkey` + `setvariable` | 从 JSON 取字段 → 存变量 |
| `detect.date` / `gettimebetweendates` | 日期 → 时间戳 |
| `url` / `downloadurl` | HTTP 请求（`WFURL`、`WFHTTPHeaders`、`WFJSONValues`） |

## 4. 提取要点

- **提示词原文**：`参数['WFTextActionText']['Value']['string']`（`gettext` 动作）
- **HTTP 目标**：`参数['WFURL']['Value']['string']` 或嵌套在 `WFTextTokenString`
- **POST body 字段**：递归 `WFJSONValues → WFDictionaryFieldValueItems → WFKey.Value.string`
- **变量绑定**：`attachmentsByRange` 里的 `VariableName` 说明该位置插入的是哪个变量
- 文本里的 `\ufffc`（`￼`）是变量占位符，不是乱码
- **API Key**：明文常写在 `WFHTTPHeaders` 的 `Authorization` 里。**输出时务必打码**（只留前 8 / 后 6 位），并提醒用户"分享此文件/链接即泄露该 Key"。
- `content` 值为 `{"Type": "ExtensionInput"}` 表示"主快捷指令传入的整段文本原样塞进来"。

🔢 **`getvalueforkey` 的点号路径取数组是 1-based（实测）**

常见形态 `choices.1.message.content` 看着像"取第 2 个元素"，实际取的是**第 1 个**（即 `choices[0]`）。
判断依据：这类取法在真实链路上**确实能跑通**（产出了正确记录）。**看到 `choices.1` 不要"顺手修正"成 `choices.0`**，会直接取空。

⚠️ 写自己的递归格式化函数时**不要过滤 `WFWorkflow*` 开头的键**——`WFWorkflow` 恰好存着子快捷指令引用。

## 5. 局限

- **子快捷指令的内容看不到**：`runworkflow` 只存名称与 identifier。要解析它必须再要一个 iCloud 链接。
- 只能读，不能改回 iCloud：改动需自己在快捷指令 App 里重建，或导出为文件让用户导入。

## 6. 实战流程（照这个顺序走，能少踩坑）

1. **先取元数据、打名称**——用户给的链接**经常是错的**（复制重复、错发父/子快捷指令）。已踩过两次：用户想给子指令却发回父指令的同一 ID。
   → **名称对不上就别急着解析内容**，先回报"这是 X，不是 Y"，请用户重发。
2. 名称正确后，**在同一条 Bash 里**下载 + 解析 + 列出动作序列（`/tmp` 不跨调用保留）。
3. 再按需深挖：提示词原文 → HTTP 目标/头部 → POST body 字段 → 变量绑定。
4. 汇报时把**能落地的结论**放前面（"要不要改、改哪几处"），把动作清单放后。
5. 若链路上还有子快捷指令，**再要一个链接**，重复步骤 1。

## 7. 复核"用户改完的版本"（高价值场景）

用户按你给的方案自改后发来新链接让你验对，是这类任务最常见的收尾。做法：

1. **把新旧两版都拉下来对比**（保留旧 recordId）。动作数变化 = 新插入了几组；再核对**后续动作是否整体后移且未错位**。
2. 按 **"改动清单 → 逐项定位"** 的方式验，不要凭印象。每项给出**动作序号 + 证据**。
3. 验完输出 ✅/❌ 分列，❌ 的给出**精确到动作与字段的补法**。

⚠️ **经验：用户最容易漏的永远是"流程末尾那个 HTTP POST 动作"** —— 提示词、取字段都在同一片区域、顺结构复制即可；而 POST body 在另一个动作里、离得远，最常被忘。变量取到了但没塞进 body = 静默不生效。**验对时必须单独查这一项。**

⚠️ 同理易漏：**few-shot 示例没同步加新字段**——模型会照抄示例的输出形状，导致新键根本不出现。

✅ 值得单独查的还有：**文本框末尾的变量芯片**是否还在（iPhone 上全选粘贴会把它冲掉，链路直接断）。

### ⚠️ few-shot 示例里的**值**也会被照抄 —— 取值类字段必须写成占位符

已踩坑（2026-09-17，同一个快捷指令）：提示词里两条 few-shot 示例的**产品名结尾都是 `C`**，
用户拍的是 **E 类**截图，模型却输出 `…(QDII)C` —— 它照抄了示例的字母，而不是从截图 OCR。
用户的原话很准确：**"里面说 C 他就给我 C，说 A 就给我 A。"**

这类错误比"漏键"更危险：**完全静默**。金额、时间、单号都对，只有"单字符差异"的类别字母错了，
下游根本无法察觉，账就记到隔壁类别上了。

规则（给任何"从文本/截图抽取"的场景写提示词时都适用）：

| 字段类型 | 示例里怎么写 |
|---------|------------|
| **取值类**（要抽取的业务值：产品名、类别字母、单号…） | **一律占位符**（如 `基金名称`），并在规则里显式声明"必须以输入文本中的实际值为准，严禁沿用示例值"。判据：**示例值与真实值差一个字符就能改变归属 → 绝不能给真实值** |
| **形状类**（键名、类型、`null` 还是 `""`、嵌套结构） | 必须给**完整样例**，一个键都不能省 |

### ⚠️ 给用户"要粘贴进快捷指令的原文"时，必须给**完整全文**

已踩坑：给提示词里的一段**合法 JSON few-shot 示例**时，为了在聊天里显得简短，把中间字段写成 `...` 省略号。
用户直接复制粘贴 → 示例变成非法 JSON，且**被省略的字段正是最重要的几个** → 模型照抄示例形状、可能整段不输出这些键。

规则：
- 凡是"用户要复制进 App 的文本"，**一律给完整可用全文，一个字都不省略**。
- 若聊天里确实需要缩短，就把**完整版写进项目文档**、聊天里只指路（"照 `docs/xxx.md` §N.N 整段复制"）。
- 复核用户版本时，专门查一眼有没有把省略号、占位符、旧残留标题一起粘进去（本例还多留了一个重复的小标题）。

### 最快的复核手法：**递归 path→value 扁平化 + set-diff**

不要逐动作肉眼对。把新旧两版的 plist 在**同一条 Bash 调用**里下载好（`/tmp` 不跨调用保留），各递归成 `路径→标量` 的扁平字典，然后做集合差：

```python
def walk(o, path=''):
    if isinstance(o, dict):
        for k, v in o.items(): yield from walk(v, f'{path}.{k}')
    elif isinstance(o, list):
        for i, v in enumerate(o): yield from walk(v, f'{path}[{i}]')
    else:
        yield path, o

d_old, d_new = dict(walk(old)), dict(walk(new))
for k in sorted(set(d_old) | set(d_new)):
    if d_old.get(k) != d_new.get(k): print(k, d_old.get(k), '->', d_new.get(k))
```

一次就能把所有差异点全部列出（实测：一次改动的 22 处差异全部落在 3 个动作内，一眼看完）。**注意**：遍历函数里不要过滤 `WFWorkflow*` 开头的键。

### 复核时必查的 6 项（按"最常出错"排序）

| # | 查什么 | 怎么判 |
|---|--------|--------|
| 1 | **流程末尾那个 HTTP POST 动作的 body 是否加了新键** | 找到 `downloadurl`（`WFHTTPMethod=POST`）→ `WFJSONValues` → 递归 `WFDictionaryFieldValueItems` 数键。⚠️ 嵌套是 `WFValue.Value.Value.WFDictionaryFieldValueItems`（多一层 `Value`，容易取空）。**用户最常漏的就是这里**——提示词、取字段都在一起，POST body 在另一个动作里 |
| 2 | **数值字段的 `WFItemType` 是否与目标列类型一致** | `WFItemType`：`0`=文本、`3`=数字、`1`=字典。目标是数字列却留成 `0` → body 会带引号，可能被后端拒写。**新增数字键时要和同列族的老键对比类型** |
| 3 | **few-shot 示例是否同步加了新字段** | 模型照抄示例的输出形状，示例缺键 → 新键根本不出现 |
| 3b | **few-shot 示例里的"取值类"字段是否是占位符** | 示例里若出现**真实业务值**（真实产品名/类别字母），模型会照抄 → 静默错账（见上一节踩坑）。查法：在提示词里搜是否存在真实名称 |
| 4 | **文本框末尾的变量芯片是否还在** | 看 `attachmentsByRange` 的偏移量。⚠️ 文本变长时偏移量会跟着变大（如 `{1619,1}`→`{1790,1}`）——**这是正常的，不是错误**，只要还有 `Type: ActionOutput` 的附件即可 |
| 5 | **HTTP 动作的 `ShowHeaders` 是否被折起** | 同一快捷指令里通常有 2 个 POST 动作，若一个 `true` 一个 `false`，说明用户在编辑时把「标头」那行折叠了。header 字典本身没丢，但建议让用户展开一次肉眼复核 `Authorization`/`Content-Type`（展开是零成本的） |

## 注意

- 沙箱里 Bash 的 glob 会展开 `--include=*.py`，用 `--include='*.py'` 加引号。
- 若 Windows/Linux 上也用，`plistlib` 通用；只是 `/tmp` 路径按需调整。
- 解析出的**密钥、Token、内网地址**一律打码后再出现在回复里。
- **用户自改后的版本，与其猜不如下载下来验**——快捷指令是纯数据，比对成本极低。
