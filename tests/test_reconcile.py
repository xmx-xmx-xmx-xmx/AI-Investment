"""#4b 底仓增量对账测试。

覆盖：写回自洽核对的全部分支 + 告警文案 + fail-silent + 回执锚点透传。
全部离线（FakeClient / 注入回执文件）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

import src.reconcile as rc
from src.reconcile import build_reconcile_alert, reconcile_items

TZ = timezone(timedelta(hours=8))
TODAY = datetime.now(TZ).date().isoformat()


class FakeClient:
    """list_records 按表名返回；可注入异常模拟读表失败。"""

    def __init__(self, trades=None, holdings=None, exc=None):
        self._tables = {"交易流水表": trades or [], "底仓表": holdings or []}
        self._exc = exc

    def list_records(self, table):
        if self._exc:
            raise self._exc
        return self._tables[table]


def _trade(rid, prev):
    return {"_record_id": rid, "结算前份额": prev}


def _holding(rid, shares):
    return {"_record_id": rid, "持仓份额": shares}


def _item(action="buy", shares=53.16, rid="tr1", hid="h1", sold_out=False, **kw):
    d = {"product": "测试产品C", "action": action, "shares": shares,
         "record_id": rid, "holding_record_id": hid, "sold_out": sold_out,
         "status": "resolved"}
    d.update(kw)
    return d


# ── reconcile_items：核心分支 ──

def test_buy_match_no_alert():
    items = [_item(shares=53.16, rid="tr1", hid="h1")]
    client = FakeClient(
        trades=[_trade("tr1", 1380.24)],
        holdings=[_holding("h1", 1380.24 + 53.16)],  # 1433.40
    )
    assert reconcile_items(items, client) == []


def test_buy_mismatch_alerts():
    items = [_item(shares=53.16, rid="tr1", hid="h1")]
    client = FakeClient(
        trades=[_trade("tr1", 1380.24)],
        holdings=[_holding("h1", 1500.00)],  # 被覆写成别的值
    )
    a = reconcile_items(items, client)
    assert len(a) == 1
    assert a[0]["kind"] == "shares_mismatch"
    assert a[0]["expected"] == 1433.40
    assert a[0]["actual"] == 1500.00
    assert a[0]["diff"] == 66.60


def test_sell_match_and_mismatch():
    items = [_item(action="sell", shares=100.0, rid="tr1", hid="h1")]
    ok = FakeClient(trades=[_trade("tr1", 500.0)], holdings=[_holding("h1", 400.0)])
    assert reconcile_items(items, ok) == []
    bad = FakeClient(trades=[_trade("tr1", 500.0)], holdings=[_holding("h1", 350.0)])
    a = reconcile_items(items, bad)
    assert len(a) == 1 and a[0]["expected"] == 400.0 and a[0]["actual"] == 350.0


def test_sold_out_deleted_ok():
    items = [_item(action="sell", shares=400.0, rid="tr1", hid="h1", sold_out=True)]
    client = FakeClient(trades=[_trade("tr1", 400.0)], holdings=[])  # 行已删
    assert reconcile_items(items, client) == []


def test_sold_out_but_holding_remains_alerts():
    items = [_item(action="sell", shares=400.0, rid="tr1", hid="h1", sold_out=True)]
    client = FakeClient(trades=[_trade("tr1", 400.0)], holdings=[_holding("h1", 12.5)])
    a = reconcile_items(items, client)
    assert len(a) == 1 and a[0]["kind"] == "sold_out_not_deleted"


def test_no_snapshot_baseline_skipped():
    # 首笔买入（prev=0 合法）与快照列没写上不可区分 → 宁可漏核不可误报
    items = [_item(shares=50.0, rid="tr1", hid="h1")]
    client = FakeClient(trades=[_trade("tr1", 0)], holdings=[_holding("h1", 50.0)])
    assert reconcile_items(items, client) == []
    # 快照字段整个缺失（旧数据/兜底重试路径）
    client2 = FakeClient(trades=[{"_record_id": "tr1"}], holdings=[_holding("h1", 50.0)])
    assert reconcile_items(items, client2) == []


def test_convert_and_missing_anchors_skipped():
    items = [
        _item(action="convert", rid="tr1", hid="h1"),      # convert 不核
        {"product": "x", "status": "resolved"},            # 无锚点
        {"product": "y", "status": "skipped", "record_id": "tr2",
         "holding_record_id": "h2", "action": "buy"},      # 非 resolved
    ]
    client = FakeClient(
        trades=[_trade("tr1", 100), _trade("tr2", 100)],
        holdings=[_holding("h1", 100), _holding("h2", 999)],
    )
    assert reconcile_items(items, client) == []


def test_float_noise_within_tolerance():
    items = [_item(shares=53.16, rid="tr1", hid="h1")]
    client = FakeClient(
        trades=[_trade("tr1", 1380.24)],
        holdings=[_holding("h1", 1433.400000001)],  # 二进制浮点噪声
    )
    assert reconcile_items(items, client) == []


def test_client_none_returns_empty():
    assert reconcile_items([_item()], None) == []


def test_empty_items_returns_empty():
    assert reconcile_items([], FakeClient()) == []


def test_base_row_missing_treated_as_zero():
    # 底仓行不存在（误删）且 expected>0 → 异常
    items = [_item(shares=50.0, rid="tr1", hid="h1")]
    client = FakeClient(trades=[_trade("tr1", 100.0)], holdings=[])
    a = reconcile_items(items, client)
    assert len(a) == 1 and a[0]["actual"] == 0.0


# ── build_reconcile_alert：文件/文案/fail-silent ──

def _write_receipt(tmp_path, items, date=TODAY):
    p = tmp_path / "receipt.json"
    p.write_text(json.dumps({"date": date, "items": items}, ensure_ascii=False),
                 encoding="utf-8")
    return str(p)


def test_alert_no_file_returns_empty(tmp_path):
    assert build_reconcile_alert(FakeClient(), receipt_path=str(tmp_path / "none.json")) == ""


def test_alert_stale_date_returns_empty(tmp_path):
    p = _write_receipt(tmp_path, [_item()], date="2020-01-01")
    assert build_reconcile_alert(FakeClient(), receipt_path=p) == ""


def test_alert_no_items_returns_empty(tmp_path):
    p = _write_receipt(tmp_path, [])
    assert build_reconcile_alert(FakeClient(), receipt_path=p) == ""


def test_alert_mismatch_text_contains_next_step(tmp_path):
    p = _write_receipt(tmp_path, [_item(shares=53.16)])
    client = FakeClient(trades=[_trade("tr1", 1380.24)], holdings=[_holding("h1", 1500.0)])
    text = build_reconcile_alert(client, receipt_path=p)
    assert "对账告警" in text
    assert "应为 1433.40" in text and "实际 1500.00" in text
    # 告警必须自带"下一步"：两种用户动作都写明
    assert "我自己动的" in text
    assert "定位回滚" in text


def test_alert_all_match_returns_empty(tmp_path):
    p = _write_receipt(tmp_path, [_item(shares=53.16)])
    client = FakeClient(trades=[_trade("tr1", 1380.24)], holdings=[_holding("h1", 1433.40)])
    assert build_reconcile_alert(client, receipt_path=p) == ""


def test_alert_fail_silent_on_client_error(tmp_path):
    p = _write_receipt(tmp_path, [_item()])
    client = FakeClient(exc=RuntimeError("feishu down"))
    assert build_reconcile_alert(client, receipt_path=p) == ""


def test_alert_local_no_client(tmp_path):
    # 本地（client=None）：回执有 items 也不告警
    p = _write_receipt(tmp_path, [_item(shares=53.16)])
    assert build_reconcile_alert(None, receipt_path=p) == ""


# ── 回执锚点透传（pending_resolver 侧）──

def test_write_receipt_passes_reconcile_anchors(tmp_path, monkeypatch):
    from src.pending_resolver import _write_receipt
    monkeypatch.setattr("src.pending_resolver._RECEIPT_PATH", str(tmp_path / "r.json"))
    _write_receipt({"resolved": 1, "skipped": 0, "errors": 0, "details": [
        {"product": "X", "action": "buy", "shares": 10.0, "amount": 100,
         "status": "resolved", "record_id": "trX", "holding_record_id": "hX",
         "sold_out": False},
        {"product": "Y", "status": "skipped", "reason": "净值未发布"},
    ]})
    data = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert len(data["items"]) == 1
    it = data["items"][0]
    assert it["record_id"] == "trX"
    assert it["holding_record_id"] == "hX"
    assert it["sold_out"] is False
