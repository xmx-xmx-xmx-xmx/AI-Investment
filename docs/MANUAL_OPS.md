# 手动操作清单（需要本人点界面 / 改快捷指令的事项）

> 代码侧改不动的、只能由本人在面板或手机上完成的操作，集中放这里。
> 每项都写成「点哪里 → 填什么 → 怎么验证 → 怎么回退」，照做即可。

---

## #25 Render 面板：把 Start Command 钉死

**背景**：仓库里**没有 `render.yaml`** → 启动命令只存在于 Render 网页面板上，
出问题时（已复发 2 次的 `Port scan timeout / no open ports detected`）
代码侧无法自查，只能靠猜。

**怎么做**

1. 打开 <https://dashboard.render.com>，登录后进入服务 **`ai-investment-server`**。
2. 左侧点 **Settings** → 找到 **Build & Deploy** 区块。
3. 找到 **Start Command** 输入框，把内容**整行替换**为：

   ```
   uvicorn bot_server:app --host 0.0.0.0 --port $PORT
   ```

   > ⚠️ `$PORT` 要原样保留（Render 运行时注入）。写成固定端口会被平台判为「没有监听端口」。

4. 点 **Save Changes**。

**为什么是这一行**：`bot_server.py` 末尾的 `if __name__ == "__main__"` 分支走的是
脚本内 `uvicorn.run`；面板上的显式命令走的是模块加载。两者行为**不完全一致**
（生产 `reload` 已在代码侧关掉，但命令不一致会让排查时分不清是谁在启动）。
钉死后两边一致。

**顺便可做（可选）**：同一页面的 **Auto-Deploy** 开关。目前每次 push 都会触发构建，
短时间连推多 commit 会让免费实例排队 → 诱发端口扫描超时。若想避开，可设为
**Off**，改完代码后在面板手动点 **Manual Deploy**。**接受偶发抖动则不用动。**

**怎么验证**：保存后等服务重新部署完成，访问
<https://ai-investment-server.onrender.com/health> → 返回 `200` 即正常。
若失败，Render **会保留上一成功版本继续服务**，所以线上不会立刻坏。

---

## #24 快捷指令：更换 OCR 模型（可选）

**背景**：`Qwen_Core` 只是把 `choices.1.message.content` **原样**回传，
没有任何 schema 约束。当前用的是 `Qwen/Qwen3-30B-A3B-Instruct-2507`。

**唯一要改的地方**：子快捷指令 **`Qwen_Core` 的动作 [2]**
（POST `api.siliconflow.cn/v1/chat/completions`）请求体里的 `"model"` 字段。
**主快捷指令「理财买入/卖出记账模块」完全不用动**（提示词、取字段、POST body 都与模型无关）。

**挑模型四条硬约束**（详见 `docs/CONVERT_DESIGN.md` §3.5.6）

| # | 约束 | 原因 |
|---|---|---|
| 1 | **必须是指令型，不能是推理型** | 推理型会带思考过程或用 ` ```json ` 包裹 → 主快捷指令解析失败。**这是换型最容易踩的坑** |
| 2 | **尽量留在 Qwen 系列** | SiliconFlow 代金券只覆盖 Qwen 系列，换 DeepSeek/GLM 会开始计费 |
| 3 | **不要为「更聪明」付溢价** | 入参只是一段 OCR 文本 + 约 1000 字提示词，输出 10 键 JSON。任务极窄，大模型收益有限 |
| 4 | **别动 `choices.1` 的索引** | Shortcuts 点号路径取数组是 **1-based**（看着像 bug，但实测可用）。改索引 = 直接拿不到内容 |

**怎么挑**：在 SiliconFlow 的模型列表里选一个「Instruct」命名的 Qwen。
换之前先单独验证一次它返回的是**纯 JSON**（不带代码块围栏、不带解释文字）。

**验收（3 笔，缺一不可）**

1. **买入**一笔 → 重点核对**份额类别字母**（A/C/E，历史上模型照抄示例值把 E 类记成过 C 类）
2. **普通卖出**一笔 → 核对 `action` / `amount`
3. **转换**一笔 → 核对 `action=convert` + `target_product` + `transfer_shares`

三项都对再留用。

**怎么回退**：把 `"model"` 改回 `Qwen/Qwen3-30B-A3B-Instruct-2507`，1 分钟。

> ⚠️ 该 SiliconFlow Key **明文写在动作 [2] 的 `Authorization` 头部**。
> 若一并轮换了 Key，记得只改这一处。

---

## 附：改了这两处之后要不要动代码？

**都不用。** 两处都是纯配置/界面操作，与仓库代码无交集。
