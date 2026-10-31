---
name: ai-investment-verify-release
description: AI-Investment 项目（飞书多维表格驱动的量化简报）改完代码后的本地验证与发布流程。当需要验证 src/ 改动、跑测试、本地干跑简报、确认推送输出、或提交并推送 GitHub 时使用。包含本地打桩绕过飞书隔离、验证 global_news 流水线的陷阱、Clash 代理 push 的固定姿势。
agent_created: true
---

# AI-Investment 本地验证与发布

项目根目录：`/Users/chenyimin/Documents/AI-Investment`

## 环境要点

- Python 用项目自带 venv：`.venv/bin/python`（**不要**用系统 python 或 `python3 -m pytest`）
- 待办唯一真源是 `TODO.md`，不要在别处维护待办；长期工程记忆见 `docs/PROJECT_MEMORY.md`
- 本地 `is_production()` 恒为 False → 禁止调飞书 API（`CLAUDE.md` 本地隔离规范）
- 本机无 `timeout` 命令（macOS）→ 不要写 `timeout 300 ...`

## 标准流程

### 1. 跑全量测试
```bash
cd /Users/chenyimin/Documents/AI-Investment && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -6
```
预期基线：**667+ passed（28 个测试文件）**。数字变了说明有新增/回归。
⚠️ 若新增或改动了函数引用，必跑 `tests/test_undefined_names.py` 与 `tests/test_builder_smoke.py`（双模式 `compact`/`full` 冒烟）。

### 2. 本地干跑简报
```bash
.venv/bin/python -m src.briefing morning --dry-run
.venv/bin/python -m src.briefing closing --dry-run --compact
.venv/bin/python -m src.briefing closing --dry-run --full
```
可换 `midday` / `closing` / `evening` / `sat_morning` / `sun_evening`，支持 `--compact`（默认精简版）与 `--full`（完整版）。
⚠️ **切勿不带 `--dry-run` 在本地运行 `python -m src.briefing <slot>`**，否则 `FeishuPusher` 会直连 webhook 真发卡片到飞书群。

### 3. commit + push（国内网络需走代理或直连解析）
直接 push GitHub 可能 `Empty reply from server` 或 `Connection refused`。先确认代理端口：
```bash
nc -z 127.0.0.1 7890 && echo OPEN || echo CLOSED
```
若 `OPEN`：
```bash
git -c http.proxy=http://127.0.0.1:7890 -c https.proxy=http://127.0.0.1:7890 push origin main
```
若 `CLOSED`（Clash 内核未起），使用已验证的 GitHub IP 直连通道：
```bash
git -c http.curloptResolve=github.com:443:140.82.112.3 push origin main
```
**不要改全局 git config**。commit message 较长时写入 `/tmp/xxx.txt` 再用 `git commit -F`，避免中文引号被 shell 吞掉。

## ⚠️ 陷阱：本地验证 global_news 会"假失败"

直接调用 `_build_global_news_brief()` / `build_global_news_for_ai()` **大概率返回空**，看起来像 LLM 挂了，其实是没数据：

本地 `load_portfolio()` 返回 `[]` → `match_and_translate()` 命中
`if not holdings and not radar_items: return []` **提前返回**，压根没走到 LLM。

### 正确验证姿势（注入桩持仓）
```python
cd /Users/chenyimin/Documents/AI-Investment && .venv/bin/python -c "
import logging; logging.basicConfig(level=logging.INFO)
from dotenv import load_dotenv; load_dotenv()
import src.advisor as advisor
advisor.load_portfolio = lambda client=None: [
    {'name':'纳指100ETF','code':'019442','asset_class':'美股资产'},
    {'name':'红利低波ETF','code':'512890','asset_class':'A股资产'},
]
import src.global_news as gn
gn.load_portfolio = advisor.load_portfolio
gn.get_feishu_client_or_none = lambda: None
print(gn._build_global_news_brief())
print('---')
print(gn.build_global_news_for_ai())
"
```

### 其他注意事项
- `_cached_fetch_global_news()` **只在结果非空时写缓存**（`if data:`）→ 流水线失败时会重跑，"RSS 日志打印两次"属预期，别误判缓存失效。
- 本机 RSS（Yahoo / Google News）偶发瞬时失败（feedparser 返回 0 条），**重跑即恢复**。用 `requests` 直连返回 200 即可判定是瞬时抖动而非源失效。
- SiliconFlow LLM 会限流（429 带重置时间）。测主模型是否可用：
  ```bash
  .venv/bin/python -c "
from dotenv import load_dotenv; load_dotenv()
from src.llm import get_llm_client, get_llm_model
c=get_llm_client(); r=c.chat.completions.create(model=get_llm_model(),
  messages=[{'role':'user','content':'回复两个字：正常'}], max_tokens=16)
print(r.choices[0].message.content)"
  ```

## 验证 diff / 快照逻辑（无需网络）

`_diff_against_last` 只依赖 `_load_prev_snapshot`，打桩即可离线验证：
```python
import pytest
monkeypatch.setattr("src.briefing._load_prev_snapshot",
                    lambda slot: {"signature": "old", "payload": {"total_value": 70000}})
```
现成用例见 `tests/test_briefing_diff.py`（10 用例）与 `tests/test_global_news.py::TestGlobalNewsLayering`（6 用例）。

## 改动的自我检查清单

1. 是否新增/修改了 `FeishuClient()` 调用？必须包 `if is_production()` 或用 `get_feishu_client_or_none()`
2. 是否改了 `TARGET_WEIGHTS`？`prompt_templates.py` 宪法是动态拼的，改一处即可 —— 不要去手改宪法字符串
3. 是否改了展示文本？展示层与 AI 层必须分开构造（不要共用一个变量再截断）
4. 是否改了 `src/` 模块？同步 `README.md` 项目结构与 `TODO.md` §0 状态快照（`CLAUDE.md` 已不含结构清单）
5. 完成后同步 `TODO.md`（完成项 + 状态快照）
