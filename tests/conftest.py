"""全项目共用的 pytest fixture。

只做一件事：**把测试产生的文件副作用挡在项目目录之外**。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_receipt_path(tmp_path, monkeypatch):
    """把「结算回执」的落盘路径重定向到 tmp_path。

    为什么必须做（2026-09-24）
        #38 L1 让 `pending_resolver.resolve_pending()` 在**每次真实调用**后写
        `data/pending_resolve_result.json`（供同一 run 的 briefing 读取）。
        而 `test_convert.py` / `test_holding_match.py` / `test_resolver_batch.py`
        里有大量 `resolve_pending(dry_run=False)` —— 它们 mock 了飞书 client，
        但**不会** mock 文件路径，于是测试会真往项目 `data/` 里写东西，
        留下一个 `errors: N` 的回执（日期是当天）→ **本地跑 briefing 时会误报**
        「另有 N 笔写回底仓失败」。

    ⚠️ 只在「尚未被显式覆盖」时重定向：`test_settlement_receipt.py` 的
       `resolver_env` fixture 会自己设定路径并据此断言，不能把它顶掉
       （fixture 执行顺序不保证，故用值判据而非顺序依赖）。
    """
    import src.pending_resolver as pr

    if pr._RECEIPT_PATH == "data/pending_resolve_result.json":
        monkeypatch.setattr(pr, "_RECEIPT_PATH", str(tmp_path / "pending_resolve_result.json"))
