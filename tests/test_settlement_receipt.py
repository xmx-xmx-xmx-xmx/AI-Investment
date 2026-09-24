"""#38 结算回执 + 结算前快照（2026-09-24）。

背景：`pending_resolver` 是 daily-run.yml 的 **Step 0**（每时段都先跑），
但它的输出**被直接丢弃** → 用户完全不知道系统在他背后改了底仓。
2026-09-24 事故：一笔支付宝已失败的单被按成功结算（静默虚增 57.6 份），
直到用户收到支付宝短信才发现。

本模块锁定两件事：
- **L1 结算回执**：结算结果落盘 → briefing 读出来摆到卡片标题下方
- **L2 结算前快照**：把「本笔结算前」的份额/成本写进流水行，回滚无需翻历史

全部 mock，零网络。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import src.pending_resolver as pr
from src.briefing import (
    _build_settlement_receipt,
    _fmt_settle_item,
    _inject_receipt_after_title,
)

tz_cn = timezone(timedelta(hours=8))


def _today() -> str:
    return datetime.now(tz_cn).date().isoformat()


# ═══════════════════════════════════════════════════════════════
# L1：明细格式化
# ═══════════════════════════════════════════════════════════════

class TestFmtSettleItem:
    def test_buy(self):
        assert _fmt_settle_item(
            {"product": "万家纳斯达克100指数C", "action": "buy", "shares": 57.6}
        ) == "· 万家纳斯达克100指数C +57.60 份"

    def test_sell(self):
        assert _fmt_settle_item(
            {"product": "华宝致远混合C", "action": "sell", "shares": 30}
        ) == "· 华宝致远混合C -30.00 份"

    def test_convert(self):
        assert _fmt_settle_item(
            {"product": "A→B", "action": "convert", "shares": 33.66}
        ) == "· A→B 转换 33.66 份"

    def test_defaults_to_buy_when_action_missing(self):
        assert _fmt_settle_item({"product": "X", "shares": 10}) == "· X +10.00 份"

    def test_no_shares(self):
        assert _fmt_settle_item({"product": "X", "shares": None}) == "· X"

    def test_name_not_truncated(self):
        """⚠️ 份额类别字母在名称**末尾**，截断会把它切掉。"""
        long_name = "景顺长城纳斯达克科技市值加权ETF联接(QDII)C"
        out = _fmt_settle_item({"product": long_name, "action": "buy", "shares": 1})
        assert long_name in out
        assert out.endswith("C +1.00 份")


# ═══════════════════════════════════════════════════════════════
# L1：回执落盘（pending_resolver 侧）
# ═══════════════════════════════════════════════════════════════

class TestWriteReceipt:
    def test_writes_only_resolved_items(self, tmp_path, monkeypatch):
        target = tmp_path / "receipt.json"
        monkeypatch.setattr(pr, "_RECEIPT_PATH", str(target))

        pr._write_receipt({
            "resolved": 1, "skipped": 1, "errors": 0,
            "details": [
                {"product": "万家纳斯达克100指数C", "shares": 57.6,
                 "action": "buy", "status": "resolved"},
                {"product": "某只 QDII", "status": "skipped", "reason": "净值未发布"},
            ],
        })

        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["date"] == _today()
        assert data["resolved"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["product"] == "万家纳斯达克100指数C"
        assert data["items"][0]["action"] == "buy"

    def test_writes_empty_receipt_when_nothing_resolved(self, tmp_path, monkeypatch):
        """⚠️ 0 笔也要落盘：否则本时段 briefing 会读到上一时段的旧回执，
        把旧结算误报成「本时段结算」。"""
        target = tmp_path / "receipt.json"
        monkeypatch.setattr(pr, "_RECEIPT_PATH", str(target))

        pr._write_receipt({"resolved": 0, "skipped": 0, "errors": 0, "details": []})

        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["items"] == []
        assert data["date"] == _today()

    def test_creates_missing_parent_dir(self, tmp_path, monkeypatch):
        target = tmp_path / "deep" / "nested" / "receipt.json"
        monkeypatch.setattr(pr, "_RECEIPT_PATH", str(target))

        pr._write_receipt({"resolved": 0, "details": []})
        assert target.exists()

    def test_write_failure_does_not_raise(self, monkeypatch):
        """落盘失败只告警：结算本身已经写进飞书了，回执只是通知。"""
        monkeypatch.setattr(pr, "_RECEIPT_PATH", "/proc/definitely/not/writable.json")
        pr._write_receipt({"resolved": 1, "details": []})  # 不应抛异常


# ═══════════════════════════════════════════════════════════════
# L1：回执读取（briefing 侧）
# ═══════════════════════════════════════════════════════════════

def _point_receipt(monkeypatch, path):
    monkeypatch.setattr("src.briefing._SETTLE_RECEIPT_PATH", str(path))


class TestBuildSettlementReceipt:
    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        _point_receipt(monkeypatch, tmp_path / "nope.json")
        assert _build_settlement_receipt() == ""

    def test_corrupt_json_returns_empty(self, tmp_path, monkeypatch):
        f = tmp_path / "bad.json"
        f.write_text("{not json", encoding="utf-8")
        _point_receipt(monkeypatch, f)
        assert _build_settlement_receipt() == ""

    def test_stale_date_returns_empty(self, tmp_path, monkeypatch):
        """⚠️ 必须校验日期：CI 里 data/ 每次新建，但本地跑会残留旧文件。"""
        f = tmp_path / "old.json"
        f.write_text(json.dumps({
            "date": "2026-09-20",
            "items": [{"product": "昨天的标的", "shares": 10, "action": "buy"}],
        }), encoding="utf-8")
        _point_receipt(monkeypatch, f)
        assert _build_settlement_receipt() == ""

    def test_zero_resolved_returns_empty(self, tmp_path, monkeypatch):
        f = tmp_path / "empty.json"
        f.write_text(json.dumps({"date": _today(), "items": [], "errors": 0}),
                     encoding="utf-8")
        _point_receipt(monkeypatch, f)
        assert _build_settlement_receipt() == ""

    def test_lists_items_with_reminder(self, tmp_path, monkeypatch):
        f = tmp_path / "ok.json"
        f.write_text(json.dumps({
            "date": _today(),
            "items": [
                {"product": "万家纳斯达克100指数C", "shares": 57.6, "action": "buy"},
                {"product": "景顺长城纳斯达克科技ETF联接(QDII)C", "shares": 33.66, "action": "buy"},
            ],
            "errors": 0,
        }), encoding="utf-8")
        _point_receipt(monkeypatch, f)

        out = _build_settlement_receipt()
        assert "本时段自动入账 2 笔" in out
        assert "万家纳斯达克100指数C +57.60 份" in out
        assert "景顺长城纳斯达克科技ETF联接(QDII)C +33.66 份" in out
        # 这句是这次改造的**真正目的**：让失败单有机会被看见
        assert "回滚" in out

    def test_caps_at_three_items(self, tmp_path, monkeypatch):
        f = tmp_path / "many.json"
        f.write_text(json.dumps({
            "date": _today(),
            "items": [{"product": f"标的{i}", "shares": 1, "action": "buy"} for i in range(5)],
            "errors": 0,
        }), encoding="utf-8")
        _point_receipt(monkeypatch, f)

        out = _build_settlement_receipt()
        assert "本时段自动入账 5 笔" in out
        assert "另有 2 笔" in out
        assert "标的4" not in out

    def test_errors_are_surfaced(self, tmp_path, monkeypatch):
        f = tmp_path / "err.json"
        f.write_text(json.dumps({"date": _today(), "items": [], "errors": 2}),
                     encoding="utf-8")
        _point_receipt(monkeypatch, f)

        out = _build_settlement_receipt()
        assert "2 笔写回底仓失败" in out


class TestInjectReceiptAfterTitle:
    def test_inserts_right_after_first_line(self):
        card = "☀️ **2026-09-24 早间简报**　|　08:30\n\n**📰 隔夜要闻**\nfoo"
        out = _inject_receipt_after_title(card, "📌 回执")
        lines = out.split("\n")
        assert lines[0].startswith("☀️")
        assert lines[1] == "📌 回执"
        assert "**📰 隔夜要闻**" in out

    def test_empty_receipt_is_noop(self):
        card = "标题\n正文"
        assert _inject_receipt_after_title(card, "") == card

    def test_single_line_card(self):
        assert _inject_receipt_after_title("只有一行", "📌 回执") == "只有一行\n📌 回执"


# ═══════════════════════════════════════════════════════════════
# L2：结算前快照真的写进了流水行
# ═══════════════════════════════════════════════════════════════

class _FakeClient:
    """最小可用替身：记录所有写回调用。"""

    def __init__(self, trades, holdings):
        self._data = {"交易流水表": trades, "底仓表": holdings, "雷达观测表": []}
        self.updates: list[tuple[str, str, dict]] = []
        self.deleted: list[tuple[str, str]] = []

    def list_records(self, table):
        return list(self._data.get(table, []))

    def update_record(self, table, record_id, fields):
        self.updates.append((table, record_id, fields))
        return True

    def delete_record(self, table, record_id):
        self.deleted.append((table, record_id))
        return True

    def create_record(self, table, fields):
        return "rec_new"

    def batch_update_records(self, table, updates):
        return len(updates)


@pytest.fixture
def resolver_env(tmp_path, monkeypatch):
    """把 resolve_pending 变成纯离线可跑：真飞书、真网络全部掐断。"""
    trades = [{
        "_record_id": "rec_tx_1",
        "状态": "pending",
        "产品名称": "招商产业债券A",
        "交易时间": "2026-09-22 14:42:00",
        "买卖方向": "买入",
        "交易金额": 100,
        "转出份额": [],
    }]
    holdings = [{
        "_record_id": "rec_h_1",
        "标的名称": "招商产业债券A",
        "标的代码": "217022",
        "持仓份额": 1000.0,
        "成本均价": 1.20,
        "资产大类": ["固收资产"],
    }]
    client = _FakeClient(trades, holdings)

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr("src.env.is_production", lambda: True)
    monkeypatch.setattr(pr, "FeishuClient", lambda *a, **k: client)
    monkeypatch.setattr(pr, "_fetch_nav_on_date", lambda code, d: 1.25)
    monkeypatch.setattr(pr, "_RECEIPT_PATH", str(tmp_path / "receipt.json"))
    return client, tmp_path / "receipt.json"


class TestSnapshotRollbackFields:
    def test_writes_pre_settlement_shares_and_cost(self, resolver_env):
        client, _ = resolver_env

        result = pr.resolve_pending()

        assert result["resolved"] == 1
        tx_updates = [f for t, r, f in client.updates if t == "交易流水表"]
        assert len(tx_updates) == 1
        fields = tx_updates[0]

        # 状态照旧
        assert fields["状态"] == "completed"
        assert fields["确认净值"] == 1.25
        assert fields["确认份额"] == pytest.approx(80.0)

        # ⭐ 这两项就是 #38 L2 的交付：
        #    结算前份额/成本，回滚时直接读它们，不必再翻 record-history
        assert fields["结算前份额"] == pytest.approx(1000.0)
        assert fields["结算前成本"] == pytest.approx(1.20)

    def test_caches_new_state_after_success(self, resolver_env):
        """回归锁：快照取的是「变更前」的值，不能因为缓存同步而取成变更后的。"""
        client, _ = resolver_env

        result = pr.resolve_pending()

        # 底仓被改成 1080 份；快照仍必须是 1000
        holding_updates = [f for t, r, f in client.updates if t == "底仓表"]
        assert holding_updates[0]["持仓份额"] == pytest.approx(1080.0)
        tx_fields = [f for t, r, f in client.updates if t == "交易流水表"][0]
        assert tx_fields["结算前份额"] == pytest.approx(1000.0)

    def test_receipt_written_for_same_run(self, resolver_env):
        """落盘 → 同一 run 的 briefing 能读到（端到端串起来）。"""
        client, receipt_path = resolver_env

        pr.resolve_pending()
        data = json.loads(receipt_path.read_text(encoding="utf-8"))

        assert data["date"] == _today()
        assert data["resolved"] == 1
        assert data["items"][0]["product"] == "招商产业债券A"
        assert data["items"][0]["action"] == "buy"

    def test_dry_run_writes_no_receipt(self, resolver_env, tmp_path, monkeypatch):
        """dry_run 是本地/测试路径，不该产生回执（否则测试会互相污染）。"""
        client, receipt_path = resolver_env

        pr.resolve_pending(dry_run=True)

        assert not receipt_path.exists()
