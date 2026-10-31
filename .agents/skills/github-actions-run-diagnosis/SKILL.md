---
name: github-actions-run-diagnosis
description: 在本机没有 gh CLI 的情况下，用 REST API 诊断 GitHub Actions 的运行情况——定位"某次定时/触发任务为什么没出结果"。能区分"根本没触发"与"跑起来但被强杀"，并按时段统计耗时分布找出撞破 timeout 的时段，还能下钻到步骤级耗时和完整日志。当用户说"XX 任务某次没跑出来""推送没收到""CI 偶发失败/超时""Actions 是不是没触发""这个 job 为什么挂了"时使用。
agent_created: true
---

# GitHub Actions 运行诊断（无 gh CLI）

## 0. 核心判断：先分清两种失败

用户说"某次没出结果"时，**不要先入为主认为是触发失败**。可能的根因分两类，处理方式完全不同：

| 现象 | 判据 | 含义 |
|---|---|---|
| **根本没触发** | 该时间点**不存在** run 记录 | 触发链问题（webhook / cron / 上游服务） |
| **跑起来但被强杀** | 存在 run 记录，`conclusion == "cancelled"` | 撞破了 `timeout-minutes`，或被人取消 |

⚠️ 这是一个真实踩过的坑：曾凭"群消息少了一条"就断定触发失败、建议加 cron 兜底。实际是 job 被强杀——**加 cron 完全救不了这个故障**。所以第一步永远是**拉 run 记录**，用证据说话。

## 1. 鉴权（重要：别泄露 token）

本机常常没有 `gh`。对公开仓库（如本项目），只读拉取 `runs` / `jobs` 可**不带 token** 直接请求；下载日志 zip 或高频请求时用 PAT：

```bash
GH_TOKEN=$(cat /path/to/pat_file)        # 该文件应在 .gitignore 内
```
- ⚠️ **绝不要把 token 内容 echo / print 出来**，也不要写进任何示例或提交。只通过环境变量传递。
- 先用一次只读调用验证 token 可用（见下），失败再排查权限。
- 顺手确认仓库可见性——**公开仓库 Actions 额度无限**，抬高 `timeout-minutes` 是零成本的：

```bash
curl -s -x http://127.0.0.1:7890 \
  -H "Authorization: Bearer $GH_TOKEN" \
  https://api.github.com/repos/<owner>/<repo> | python3 -c "import sys,json;d=json.load(sys.stdin);print('private:',d.get('private'),'| default_branch:',d.get('default_branch'))"
```

## 2. 网络：按需加代理

⚠️ 若直连 `api.github.com` 返回空或超时，加 Clash 代理（默认 7890）：

```bash
curl -s -m 30 -x http://127.0.0.1:7890 ...
```
对应环境变量形态：`HTTPS_PROXY=http://127.0.0.1:7890`。

## 3. 三层下钻

### 第 1 层：run 列表——先看有没有、结论是什么

```bash
curl -s -m 60 -x http://127.0.0.1:7890 \
  "https://api.github.com/repos/<owner>/<repo>/actions/runs?per_page=60" \
  -o /tmp/runs.json
```

关注字段：`total_count`、`conclusion`、`created_at`（入队时间）、`run_started_at`（真正开跑时间）、`updated_at`、`event`、`name`、`head_sha`。
⚠️ **注意对比 `created_at` 与 `run_started_at`**：GitHub Actions 免费 runner 在高峰期 `schedule` 事件可能排队延迟数小时（见 `docs/PROJECT_MEMORY.md` §6 N8），导致 `created_at` 远晚于 cron 设定点。

**先单独捞出所有非 success 的 run**：

```bash
python3 - <<'PY'
import json
d = json.load(open('/tmp/runs.json'))
print('total_count =', d['total_count'], '| 本页', len(d['workflow_runs']))
for r in d['workflow_runs']:
    if r['conclusion'] != 'success':
        print(r['run_started_at'], r['conclusion'], r['name'], '|', r['head_sha'][:7])
PY
```

### 第 2 层：按时段统计耗时分布——找出离撞墙最近的时段

⚠️ **只看"失败的那一次"会得出错误结论**（会以为纯属偶发）。必须看**分布**：中位数告诉你常态，最大值告诉你余量。

```bash
python3 .agents/skills/github-actions-run-diagnosis/scripts/run_durations.py /tmp/runs.json
```
→ 输出按小时分组的 次数 / 中位 / 最长 / 撞墙次数，并自动读取 workflow 的 `timeout-minutes` 指出余量最小的时段。

🔍 **决定性信号**：若多个 `cancelled` 的耗时**都精确等于墙上时钟上限**（如 15.3m / 15.4m 撞 15m），即可确认是 timeout 强杀，而非偶发网络中断。

### 第 3 层：步骤级耗时 + 完整日志

```bash
# 步骤级
curl -s -m 60 -x http://127.0.0.1:7890 \
  "https://api.github.com/repos/<owner>/<repo>/actions/runs/<RUN_ID>/jobs" -o /tmp/jobs.json

python3 - <<'PY'
import json
from datetime import datetime
d = json.load(open('/tmp/jobs.json'))
for j in d['jobs']:
    print(f"job={j['name']}  {j['conclusion']}")
    for s in j['steps']:
        a, b = s.get('started_at'), s.get('completed_at')
        if a and b:
            fmt = '%Y-%m-%dT%H:%M:%SZ'
            mins = (datetime.strptime(b, fmt) - datetime.strptime(a, fmt)).total_seconds() / 60
            print(f"   {mins:6.1f}m  {s['conclusion']:<10} {s['name']}")
PY
```
👉 **这一步能排除误判**：把失败的 run 和**同一时段的成功 run** 并排比，就能看出是"哪一步变慢了"而不是"整体慢"。常见结论是「安装依赖稳定 0.4m，业务步骤从 4m 涨到 14.7m」→ 瓶颈在业务代码。

```bash
# 完整日志（zip，需带 GH_TOKEN）
curl -sL -m 120 -x http://127.0.0.1:7890 \
  -H "Authorization: Bearer $GH_TOKEN" \
  "https://api.github.com/repos/<owner>/<repo>/actions/runs/<RUN_ID>/logs" -o /tmp/logs.zip
unzip -o -q /tmp/logs.zip -d /tmp/run_logs
ls /tmp/run_logs
```
每个 step 一个 `.txt`（编号前缀，如 `0_briefing.txt` = 第一个 step）。

## 4. 日志里读什么

**找"静默空白期"**——它直接指出卡点：

```bash
grep -nE "^[0-9]{4}-[0-9]{2}-[0-9]{2}T? ?[0-9]{2}:[0-9]{2}:[0-9]{2}" /tmp/run_logs/*.txt | tail -40
```
把时间戳连起来看，**相邻两行时间差突然变成几分钟**的位置就是卡死点。典型形态：进度条走到 `2/8` 后**静默十几分钟**，然后 `##[error]The operation was canceled.`

**进度条是谁打的**：如果日志里有 `tqdm` 风格进度条（`0%|  | 0/8 [00:00<?, ?it/s]`），先确认它不是自己代码的：

```bash
grep -rn "tqdm" --include='*.py' .        # 无命中 → 是第三方库内部的
```
⚠️ 第三方库（如 akshare）**内部的 requests 无法注入 timeout**，这是"偶发挂死"的常见根源。对策是给该库调用套**外层**硬超时（daemon 线程 + `join(timeout)`，见 `references/timeout-guard.md`），而不是去改库。

## 5. 收尾比对：改动何时真正生效

⚠️ **别把平台 A 的部署状态当成任务是否上新代码的依据**。要看**实际执行任务的平台**。

- 若任务由 GitHub Actions 跑（`workflow_dispatch` / `cron`）→ 生效时间是**下一次 run**。
- 若另有 PaaS（Render / Vercel 等）承载触发入口 → 它可能"落后几个 commit"却**不影响**任务本身（懒加载）。
- PaaS 健康检查要**给足超时**：免费实例冷启动可达 30s+，`-m 25` 会误报超时。用 `-m 90`。
- PaaS 部署失败时**通常保留上一成功版本继续服务** → 先探 `/health`，**200 就说明没坏**，不必紧张。

## 参考

- `scripts/run_durations.py` —— 按时段统计耗时分布 + 余量分析
- `references/timeout-guard.md` —— 给不可控的第三方库调用套外层超时的正确姿势（含一个反直觉的坑）
