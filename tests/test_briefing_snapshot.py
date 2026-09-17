# -*- coding: utf-8 -*-
"""briefing.py 快照读写链路单元测试（E 改造的持久化底座）。

覆盖三块此前**零测试**的代码：
  1. `_make_signature` —— 信息面指纹的生成口径
  2. `_extract_metrics_from_verdict` —— diff 判据的指标来源
  3. `read/write_briefing_snapshot` 的本地 fixture 与生产飞书两条分支

E 改造（变化感知）历史上两次翻车都出在"签名语义"上（占位符恒变、卡片时间戳
恒变 → 减负全部失效）。这一层如果坏了，症状是**静默的**：简报照常推送，
只是天天"维持不动"或者天天重复解读，从日志里看不出来。所以这里的测试重点是
"写入什么、读回什么"必须严格可预期。

⚠️ fixture 分支会写真实文件 `tests/fixtures/briefing_snapshots_mock.json`。
所有用例都把 `_SNAPSHOT_FIXTURE_PATH` 重定向到 tmp_path，绝不污染仓库内 fixture。
"""

from __future__ import annotations

import json

import pytest


# ═══════════════════════════════════════════════════════════════
# 桩
# ═══════════════════════════════════════════════════════════════

class FakeClient:
    """只实现快照用到的三件事：列记录 / 删记录 / 建记录。"""

    def __init__(self, records=None):
        self.records = [dict(r) for r in (records or [])]
        self.deletes: list[tuple[str, str]] = []
        self.creates: list[tuple[str, dict]] = []
        self._n = 0

    def list_records(self, table):
        return [dict(r) for r in self.records]

    def delete_record(self, table, rid):
        self.deletes.append((table, rid))
        self.records = [r for r in self.records if r.get("_record_id") != rid]
        return True

    def create_record(self, table, fields):
        self._n += 1
        rid = f"new{self._n}"
        self.records.append({"_record_id": rid, **fields})
        self.creates.append((table, dict(fields)))
        return rid


@pytest.fixture
def local_fixture(monkeypatch, tmp_path):
    """把快照文件重定向到临时目录，返回该路径。"""
    path = tmp_path / "briefing_snapshots.json"
    monkeypatch.setattr("src.feishu_client._SNAPSHOT_FIXTURE_PATH", str(path))
    return path


@pytest.fixture
def prod(monkeypatch):
    """进入"生产"分支（函数内 `from src.env import is_production`，故打 src.env）。"""
    monkeypatch.setattr("src.env.is_production", lambda: True)


def _fs_client(monkeypatch, client):
    monkeypatch.setattr("src.feishu_client.get_feishu_client_or_none", lambda: client)


# ═══════════════════════════════════════════════════════════════
# 1. 信息面指纹
# ═══════════════════════════════════════════════════════════════

class TestMakeSignature:
    def test_deterministic_and_short(self):
        from src.briefing import _make_signature
        a = _make_signature("今日新闻：纳指创新高")
        b = _make_signature("今日新闻：纳指创新高")
        assert a == b
        assert len(a) == 12

    def test_head_change_alters_signature(self):
        from src.briefing import _make_signature
        assert _make_signature("A" + "x" * 799) != _make_signature("B" + "x" * 799)

    def test_only_first_800_chars_count(self):
        """已知边界：只有前 800 字符参与指纹。

        固化它是有意的 —— 指纹的用途是"信息面换没换"，而卡片尾部的
        HH:MM 时间戳恰好落在 800 字符之后，这正是"卡片文本不可作签名"的
        那条教训的补救方式之一。若日后有人把采样长度改短到时间戳之前，
        这条用例会失败并提醒风险。
        """
        from src.briefing import _make_signature
        head = "H" * 800
        assert _make_signature(head + "尾部A") == _make_signature(head + "尾部B")


# ═══════════════════════════════════════════════════════════════
# 2. verdict → key_metrics
# ═══════════════════════════════════════════════════════════════

class TestExtractMetrics:
    def test_total_value_and_deviations(self):
        from src.briefing import _extract_metrics_from_verdict
        m = _extract_metrics_from_verdict({
            "total_value": 70000.126,
            "signals": [
                {"asset_class": "美股资产", "deviation_pct": "+6.2%"},
                {"asset_class": "固收资产", "deviation_pct": "-1.4%"},
            ],
        })
        assert m["total_value"] == pytest.approx(70000.13)
        assert m["deviation_美股"] == pytest.approx(6.2)
        assert m["deviation_固收"] == pytest.approx(-1.4)

    def test_falls_back_to_actual_minus_target(self):
        """deviation_pct 缺失时用 actual - target 现算（两者都带 %）。"""
        from src.briefing import _extract_metrics_from_verdict
        m = _extract_metrics_from_verdict({
            "total_value": 100,
            "signals": [{
                "asset_class": "A股资产",
                "actual_weight": "14.5%",
                "target_weight": "10%",
            }],
        })
        assert m["deviation_A股"] == pytest.approx(4.5)

    def test_skips_unparsable_signal(self):
        from src.briefing import _extract_metrics_from_verdict
        m = _extract_metrics_from_verdict({
            "total_value": 100,
            "signals": [{"asset_class": "港股资产", "deviation_pct": "N/A"}],
        })
        assert not any(k.startswith("deviation_") for k in m)

    def test_zero_total_value_omitted(self):
        """0 市值不写进快照 —— 避免下次 diff 报出"新增指标 total_value=0"。"""
        from src.briefing import _extract_metrics_from_verdict
        m = _extract_metrics_from_verdict({"total_value": 0, "signals": []})
        assert "total_value" not in m

    def test_missing_signals_is_tolerated(self):
        from src.briefing import _extract_metrics_from_verdict
        assert _extract_metrics_from_verdict({"total_value": 100})["total_value"] == 100


# ═══════════════════════════════════════════════════════════════
# 3a. 本地 fixture 分支
# ═══════════════════════════════════════════════════════════════

class TestSnapshotFixtureBranch:
    def test_read_missing_file_returns_none(self, local_fixture):
        from src.feishu_client import read_briefing_snapshot
        assert not local_fixture.exists()
        assert read_briefing_snapshot("morning") is None

    def test_write_then_read_roundtrip(self, local_fixture):
        from src.feishu_client import read_briefing_snapshot, write_briefing_snapshot
        payload = {"total_value": 70123.45, "deviation_美股": 6.2}
        assert write_briefing_snapshot("morning", payload, "sig-abc") == "fixture"

        got = read_briefing_snapshot("morning")
        assert got is not None
        assert got["slot"] == "morning"
        assert got["signature"] == "sig-abc"
        assert got["payload"] == payload

    def test_timestamp_is_millisecond_epoch(self, local_fixture):
        from src.feishu_client import read_briefing_snapshot, write_briefing_snapshot
        write_briefing_snapshot("morning", {"total_value": 1}, "s")
        ts = read_briefing_snapshot("morning")["timestamp"]
        assert ts > 1_700_000_000_000          # 毫秒，不是秒

    def test_slots_are_isolated(self, local_fixture):
        from src.feishu_client import read_briefing_snapshot, write_briefing_snapshot
        write_briefing_snapshot("morning", {"total_value": 100}, "sig-m")
        write_briefing_snapshot("evening", {"total_value": 200}, "sig-e")

        assert read_briefing_snapshot("morning")["payload"]["total_value"] == 100
        assert read_briefing_snapshot("evening")["payload"]["total_value"] == 200
        assert read_briefing_snapshot("evening")["signature"] == "sig-e"

    def test_same_slot_overwrites(self, local_fixture):
        """同一 slot 只保留最新一次 —— 否则 diff 会读到中间态。"""
        from src.feishu_client import read_briefing_snapshot, write_briefing_snapshot
        write_briefing_snapshot("morning", {"total_value": 100}, "old")
        write_briefing_snapshot("morning", {"total_value": 300}, "new")

        data = json.loads(local_fixture.read_text(encoding="utf-8"))
        assert list(data.keys()) == ["morning"]
        got = read_briefing_snapshot("morning")
        assert got["signature"] == "new"
        assert got["payload"]["total_value"] == 300

    def test_unknown_slot_returns_none(self, local_fixture):
        from src.feishu_client import read_briefing_snapshot, write_briefing_snapshot
        write_briefing_snapshot("morning", {"total_value": 100}, "sig")
        assert read_briefing_snapshot("closing") is None

    def test_corrupt_json_returns_none_without_raising(self, local_fixture):
        """快照文件被写坏时，简报必须照常推送（退化为一律解读），不能崩。"""
        from src.feishu_client import read_briefing_snapshot
        local_fixture.write_text("{ this is not json", encoding="utf-8")
        assert read_briefing_snapshot("morning") is None

    def test_corrupt_json_can_be_overwritten(self, local_fixture):
        from src.feishu_client import read_briefing_snapshot, write_briefing_snapshot
        local_fixture.write_text("{ broken", encoding="utf-8")
        write_briefing_snapshot("morning", {"total_value": 5}, "sig")
        assert read_briefing_snapshot("morning")["signature"] == "sig"

    def test_chinese_payload_survives_roundtrip(self, local_fixture):
        """payload 里有中文键名（deviation_美股）—— 不能因编码问题变形。"""
        from src.feishu_client import read_briefing_snapshot, write_briefing_snapshot
        write_briefing_snapshot("evening", {"deviation_避险": -3.3}, "sig")
        raw = local_fixture.read_text(encoding="utf-8")
        assert "避险" in raw                       # ensure_ascii=False 落盘
        assert read_briefing_snapshot("evening")["payload"]["deviation_避险"] == -3.3


# ═══════════════════════════════════════════════════════════════
# 3b. 生产飞书分支
# ═══════════════════════════════════════════════════════════════

def _fs_row(rid, slot, ts, signature, payload):
    return {
        "_record_id": rid,
        "时段": slot,
        "时间戳": ts,
        "签名": signature,
        "数据载荷": payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
    }


class TestSnapshotFeishuBranch:
    def test_read_picks_latest_by_timestamp(self, monkeypatch, prod):
        from src.feishu_client import read_briefing_snapshot
        fake = FakeClient([
            _fs_row("r1", "morning", "1000", "old", {"total_value": 1}),
            _fs_row("r2", "morning", "3000", "newest", {"total_value": 3}),
            _fs_row("r3", "morning", "2000", "mid", {"total_value": 2}),
        ])
        _fs_client(monkeypatch, fake)
        got = read_briefing_snapshot("morning")
        assert got["signature"] == "newest"
        assert got["payload"]["total_value"] == 3

    def test_read_filters_by_slot(self, monkeypatch, prod):
        from src.feishu_client import read_briefing_snapshot
        fake = FakeClient([
            _fs_row("r1", "morning", "1000", "sig-m", {"total_value": 1}),
            _fs_row("r2", "evening", "9000", "sig-e", {"total_value": 9}),
        ])
        _fs_client(monkeypatch, fake)
        assert read_briefing_snapshot("morning")["signature"] == "sig-m"

    def test_read_missing_slot_returns_none(self, monkeypatch, prod):
        from src.feishu_client import read_briefing_snapshot
        _fs_client(monkeypatch, FakeClient([_fs_row("r1", "evening", "1", "s", {})]))
        assert read_briefing_snapshot("morning") is None

    def test_read_survives_non_numeric_timestamp(self, monkeypatch, prod):
        """时间戳脏数据时排序会抛异常，代码里是 except: pass —— 不能因此丢快照。"""
        from src.feishu_client import read_briefing_snapshot
        fake = FakeClient([
            _fs_row("r1", "morning", "not-a-number", "sig-a", {"total_value": 1}),
            _fs_row("r2", "morning", "2000", "sig-b", {"total_value": 2}),
        ])
        _fs_client(monkeypatch, fake)
        got = read_briefing_snapshot("morning")
        assert got is not None
        assert got["signature"] in ("sig-a", "sig-b")

    def test_read_parses_json_payload_string(self, monkeypatch, prod):
        from src.feishu_client import read_briefing_snapshot
        _fs_client(monkeypatch, FakeClient([
            _fs_row("r1", "morning", 1000, "s", '{"total_value": 123}'),
        ]))
        assert read_briefing_snapshot("morning")["payload"] == {"total_value": 123}

    def test_read_accepts_dict_payload(self, monkeypatch, prod):
        """飞书有时直接回 dict（已解析过）—— 不能再 json.loads 一遍。"""
        from src.feishu_client import read_briefing_snapshot
        fake = FakeClient([{
            "_record_id": "r1", "时段": "morning", "时间戳": 1000,
            "签名": "s", "数据载荷": {"total_value": 77},
        }])
        _fs_client(monkeypatch, fake)
        assert read_briefing_snapshot("morning")["payload"] == {"total_value": 77}

    def test_read_corrupt_payload_falls_back_to_empty_dict(self, monkeypatch, prod):
        from src.feishu_client import read_briefing_snapshot
        _fs_client(monkeypatch, FakeClient([
            _fs_row("r1", "morning", 1000, "s", "{ broken json"),
        ]))
        assert read_briefing_snapshot("morning")["payload"] == {}

    def test_write_deletes_only_same_slot_then_creates(self, monkeypatch, prod):
        from src.feishu_client import write_briefing_snapshot
        fake = FakeClient([
            _fs_row("old1", "morning", "1", "s1", {}),
            _fs_row("old2", "morning", "2", "s2", {}),
            _fs_row("keep", "evening", "3", "s3", {}),
        ])
        _fs_client(monkeypatch, fake)

        rid = write_briefing_snapshot("morning", {"total_value": 500}, "sig-new")
        assert rid == "new1"
        assert sorted(r for _, r in fake.deletes) == ["old1", "old2"]
        assert all(t == "简报快照表" for t, _ in fake.deletes)
        assert [r["_record_id"] for r in fake.records if r["时段"] == "evening"] == ["keep"]

    def test_write_persists_four_fields(self, monkeypatch, prod):
        from src.feishu_client import write_briefing_snapshot
        fake = FakeClient()
        _fs_client(monkeypatch, fake)
        write_briefing_snapshot("closing", {"total_value": 1}, "sig-x")

        table, fields = fake.creates[0]
        assert table == "简报快照表"
        assert set(fields) == {"时段", "时间戳", "签名", "数据载荷"}
        assert fields["时段"] == "closing"
        assert fields["签名"] == "sig-x"
        assert json.loads(fields["数据载荷"]) == {"total_value": 1}

    def test_write_tolerates_delete_failure(self, monkeypatch, prod):
        """删旧记录失败不能阻断写新快照（否则快照会永久停在旧值）。"""
        from src.feishu_client import write_briefing_snapshot

        class FlakyClient(FakeClient):
            def delete_record(self, table, rid):
                raise RuntimeError("boom")

        fake = FlakyClient([_fs_row("old1", "morning", "1", "s", {})])
        _fs_client(monkeypatch, fake)
        assert write_briefing_snapshot("morning", {"total_value": 9}, "sig") == "new1"
        assert fake.creates[0][1]["签名"] == "sig"

    def test_read_returns_none_without_client(self, monkeypatch, prod):
        from src.feishu_client import read_briefing_snapshot
        _fs_client(monkeypatch, None)
        assert read_briefing_snapshot("morning") is None

    def test_write_returns_none_without_client(self, monkeypatch, prod):
        from src.feishu_client import write_briefing_snapshot
        _fs_client(monkeypatch, None)
        assert write_briefing_snapshot("morning", {"total_value": 1}, "s") is None


# ═══════════════════════════════════════════════════════════════
# 4. briefing 侧封装（参数顺序容易搞混）
# ═══════════════════════════════════════════════════════════════

class TestBriefingWrappers:
    def test_save_snapshot_reorders_args(self, monkeypatch):
        """`_save_snapshot(slot, signature, metrics)` → `write(slot, payload, signature)`。

        签名与 payload 顺序相反，是这一层唯一的存在理由；
        搞反了会把 dict 当签名写进去（且不会报错）。
        """
        captured = {}

        def fake_write(slot, payload, signature):
            captured.update(slot=slot, payload=payload, signature=signature)
            return "ok"

        monkeypatch.setattr("src.feishu_client.write_briefing_snapshot", fake_write)
        from src.briefing import _save_snapshot
        _save_snapshot("morning", "SIG-1", {"total_value": 42})

        assert captured == {
            "slot": "morning",
            "payload": {"total_value": 42},
            "signature": "SIG-1",
        }

    def test_load_prev_snapshot_passes_slot(self, monkeypatch):
        seen = []

        def fake_read(slot):
            seen.append(slot)
            return {"signature": "s"}

        monkeypatch.setattr("src.feishu_client.read_briefing_snapshot", fake_read)
        from src.briefing import _load_prev_snapshot
        assert _load_prev_snapshot("sun_evening") == {"signature": "s"}
        assert seen == ["sun_evening"]
