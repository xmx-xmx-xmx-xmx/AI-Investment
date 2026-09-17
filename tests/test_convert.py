# -*- coding: utf-8 -*-
"""基金转换（convert）功能测试。

覆盖 2026-09-17 convert 改造的 7 个关键场景：
  1. _parse_action：convert 识别 + 未知方向不得兜底为 buy
  2. _parse_shares：份额字段解析
  3. 正常两腿转换：转出扣份额、转入加权成本
  4. 转出全清 → 底仓记录删除
  5. 任一腿净值未发布 → 保持 pending，绝不写一半
  6. 转入标的无法匹配 → 跳过 + 明确原因（不静默）
  7. 确认份额用户填优先（D5）
  8. 下游读取方：pending 行不进简报、不触发冷却期
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from src import pending_resolver as pr

TZ = timezone(timedelta(hours=8))

OUT_NAME = "景顺长城纳斯达克科技市值加权ETF联接（QDII）C"
IN_NAME = "景顺长城纳斯达克科技市值加权ETF联接（QDII）E"


def _ts(y, mo, d, h, mi, s=0) -> int:
    return int(datetime(y, mo, d, h, mi, s, tzinfo=TZ).timestamp() * 1000)


# ═══════════════════════════════════════════════════════════════
# 假飞书客户端
# ═══════════════════════════════════════════════════════════════

class FakeClient:
    def __init__(self, tables: dict):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.updates = []
        self.creates = []
        self.deletes = []
        self._n = 0

    def list_records(self, table):
        return [dict(r) for r in self.tables.get(table, [])]

    def update_record(self, table, rid, fields):
        self.updates.append((table, rid, dict(fields)))
        for r in self.tables.get(table, []):
            if r.get("_record_id") == rid:
                r.update(fields)
        return True

    def create_record(self, table, fields):
        self._n += 1
        rid = f"new{self._n}"
        self.tables.setdefault(table, []).append({"_record_id": rid, **fields})
        self.creates.append((table, dict(fields)))
        return rid

    def delete_record(self, table, rid):
        self.deletes.append((table, rid))
        self.tables[table] = [r for r in self.tables.get(table, []) if r.get("_record_id") != rid]
        return True


def _convert_row(**over) -> dict:
    row = {
        "_record_id": "recCONV",
        "产品名称": OUT_NAME,
        "转入标的": IN_NAME,
        "转出份额": "200",
        "买卖方向": "convert",
        "状态": "pending",
        "交易时间": _ts(2026, 9, 17, 12, 42, 52),
    }
    row.update(over)
    return row


def _holdings() -> list:
    return [
        {"_record_id": "hC", "标的名称": OUT_NAME, "标的代码": "017093",
         "持仓份额": 308.23, "成本均价": 2.0, "资产大类": "美股资产"},
        {"_record_id": "hE", "标的名称": IN_NAME, "标的代码": "019118",
         "持仓份额": 0, "成本均价": 0, "资产大类": "美股资产"},
    ]


def _make_client(convert_row=None) -> FakeClient:
    return FakeClient({
        "交易流水表": [convert_row if convert_row is not None else _convert_row()],
        "底仓表": _holdings(),
        "雷达观测表": [],
    })


def _patch(monkeypatch, client, nav_map, auto_code=""):
    monkeypatch.setattr("src.env.is_production", lambda: True)
    monkeypatch.setattr(pr, "FeishuClient", lambda *a, **k: client)
    monkeypatch.setattr(pr, "_fetch_nav_on_date", lambda code, d: nav_map.get(code))
    # 防止测试联网查基金代码
    monkeypatch.setattr(pr, "_auto_detect_fund_code", lambda name: auto_code)


def _holding(client, rid):
    for r in client.tables["底仓表"]:
        if r.get("_record_id") == rid:
            return r
    return None


def _trade(client):
    return client.tables["交易流水表"][0]


# ═══════════════════════════════════════════════════════════════
# 1. 方向解析
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("raw,expected", [
    ("buy", "buy"),
    ("sell", "sell"),
    ("convert", "convert"),
    ("买入", "buy"),
    ("卖出", "sell"),
    ("转换", "convert"),
    (["convert"], "convert"),
    (["转换"], "convert"),
    ("买入金额", "buy"),
    ("卖出份额", "sell"),
    # ⚠️ 关键：未知方向必须 unknown，绝不能兜底成 buy
    ("", "unknown"),
    (None, "unknown"),
    ("转托管", "unknown"),
    ("garbage", "unknown"),
])
def test_parse_action(raw, expected):
    assert pr._parse_action(raw) == expected


def test_parse_action_unknown_never_buy():
    """回归护栏：曾经 `return "buy"` 会把任何未知方向当买入执行。"""
    for raw in ("未知", "transfer", "分红再投", None, ""):
        assert pr._parse_action(raw) != "buy"


def test_parse_shares():
    assert pr._parse_shares("200.5") == 200.5
    assert pr._parse_shares(30) == 30.0
    assert pr._parse_shares(["12"]) == 12.0
    assert pr._parse_shares(None) is None
    assert pr._parse_shares("") is None
    assert pr._parse_shares([]) is None
    assert pr._parse_shares(0) is None
    assert pr._parse_shares("abc") is None


# ═══════════════════════════════════════════════════════════════
# 2. 正常两腿转换
# ═══════════════════════════════════════════════════════════════

def test_convert_happy_path(monkeypatch):
    client = _make_client()
    _patch(monkeypatch, client, {"017093": 2.8131, "019118": 2.8386})

    res = pr.resolve_pending(dry_run=False)

    assert res["resolved"] == 1
    assert res["errors"] == 0

    # 转出腿：308.23 - 200 = 108.23，成本价不变
    c = _holding(client, "hC")
    assert c["持仓份额"] == pytest.approx(108.23, abs=1e-6)
    assert c["成本均价"] == pytest.approx(2.0, abs=1e-9)

    # 转入腿：200 × 2.8131 / 2.8386 ≈ 198.2 份，成本 = 转入净值
    expected_in = round(200 * 2.8131 / 2.8386, 2)
    e = _holding(client, "hE")
    assert e["持仓份额"] == pytest.approx(expected_in, abs=1e-6)
    assert e["成本均价"] == pytest.approx(2.84, abs=0.01)

    # 流水表：转入份额/净值回填、状态 completed
    t = _trade(client)
    assert t["状态"] == "completed"
    assert t["确认份额"] == pytest.approx(expected_in, abs=1e-6)
    assert t["确认净值"] == pytest.approx(2.8386, abs=1e-9)


def test_convert_sold_out_deletes_holding(monkeypatch):
    """转出份额 = 全部持仓 → 底仓记录被删除（不残留零头）。"""
    row = _convert_row(**{"转出份额": "308.23"})
    client = _make_client(row)
    _patch(monkeypatch, client, {"017093": 2.8131, "019118": 2.8386})

    res = pr.resolve_pending(dry_run=False)
    assert res["resolved"] == 1
    assert client.deletes == [("底仓表", "hC")]
    assert _holding(client, "hC") is None
    assert _holding(client, "hE")["持仓份额"] > 0


# ═══════════════════════════════════════════════════════════════
# 3. 熔断：净值未发布 / 标的匹配失败 / 缺份额
# ═══════════════════════════════════════════════════════════════

def test_convert_nav_missing_keeps_pending(monkeypatch):
    """任一腿净值未发布 → 保持 pending，两腿都不落库。"""
    client = _make_client()
    _patch(monkeypatch, client, {"017093": 2.8131})   # 缺转入腿 019118

    res = pr.resolve_pending(dry_run=False)

    assert res["resolved"] == 0
    assert res["skipped"] == 1
    assert client.updates == [] and client.deletes == []
    assert _holding(client, "hC")["持仓份额"] == pytest.approx(308.23)
    assert _holding(client, "hE")["持仓份额"] == 0
    assert _trade(client)["状态"] == "pending"
    assert "净值未发布" in res["details"][0]["reason"]


def test_convert_target_not_found_is_loud(monkeypatch):
    """转入标的无法识别代码 → 跳过且原因明确（不静默、不当买入）。"""
    row = _convert_row(**{"转入标的": "某只尚未建档的基金"})
    client = _make_client(row)
    _patch(monkeypatch, client, {"017093": 2.8131}, auto_code="")

    res = pr.resolve_pending(dry_run=False)

    assert res["resolved"] == 0
    assert res["skipped"] == 1
    assert "无法识别标的代码" in res["details"][0]["reason"]
    assert client.updates == []
    assert _holding(client, "hC")["持仓份额"] == pytest.approx(308.23)


def test_convert_missing_shares_skipped(monkeypatch):
    """转换行没有「转出份额」→ 跳过（转换单没有金额可回退）。"""
    row = _convert_row()
    row.pop("转出份额")
    client = _make_client(row)
    _patch(monkeypatch, client, {"017093": 2.8131, "019118": 2.8386})

    res = pr.resolve_pending(dry_run=False)
    assert res["resolved"] == 0
    assert "转出份额" in res["details"][0]["reason"]
    assert client.updates == []


def test_convert_missing_target_skipped(monkeypatch):
    row = _convert_row()
    row.pop("转入标的")
    client = _make_client(row)
    _patch(monkeypatch, client, {"017093": 2.8131, "019118": 2.8386})

    res = pr.resolve_pending(dry_run=False)
    assert res["resolved"] == 0
    assert "转入标的" in res["details"][0]["reason"]


# ═══════════════════════════════════════════════════════════════
# 4. 份额驱动 / 用户填优先
# ═══════════════════════════════════════════════════════════════

def test_convert_uses_shares_not_amount(monkeypatch):
    """份额驱动：即使没有「交易金额」，也能正确落地。"""
    row = _convert_row(**{"交易金额": None})
    client = _make_client(row)
    _patch(monkeypatch, client, {"017093": 2.8131, "019118": 2.8386})

    res = pr.resolve_pending(dry_run=False)
    assert res["resolved"] == 1
    assert _holding(client, "hC")["持仓份额"] == pytest.approx(108.23, abs=1e-6)


def test_convert_user_confirmed_shares_priority(monkeypatch):
    """D5：用户同时填了「确认份额 + 确认净值」→ 显式覆盖，不走折算。"""
    row = _convert_row(**{"确认份额": "150", "确认净值": "2.8400"})
    client = _make_client(row)
    _patch(monkeypatch, client, {"017093": 2.8131, "019118": 2.8386})

    res = pr.resolve_pending(dry_run=False)
    assert res["resolved"] == 1
    assert _holding(client, "hE")["持仓份额"] == pytest.approx(150.0, abs=1e-6)
    assert _trade(client)["确认份额"] == pytest.approx(150.0, abs=1e-6)
    # 成本改用用户给的净值
    assert _holding(client, "hE")["成本均价"] == pytest.approx(2.84, abs=0.01)


def test_convert_shares_alone_is_not_confirmation(monkeypatch):
    """闸门：只填「确认份额」（快捷指令会把申请转出份额写这儿）→ 不覆盖。
    否则 200 份转出会被误当成 200 份转入。"""
    row = _convert_row(**{"确认份额": "200", "确认净值": "0"})
    client = _make_client(row)
    _patch(monkeypatch, client, {"017093": 2.8131, "019118": 2.8386})

    res = pr.resolve_pending(dry_run=False)
    assert res["resolved"] == 1
    expected_in = round(200 * 2.8131 / 2.8386, 2)   # ≈198.20，不是 200
    assert _holding(client, "hE")["持仓份额"] == pytest.approx(expected_in, abs=1e-6)
    assert _holding(client, "hE")["持仓份额"] != pytest.approx(200.0, abs=0.5)


# ═══════════════════════════════════════════════════════════════
# 5. 下游读取方：pending 行不得进入简报 / 冷却期
# ═══════════════════════════════════════════════════════════════

def test_pending_excluded_from_trade_summary(monkeypatch):
    from src import briefing

    records = [
        {"_record_id": "a", "产品名称": "摩根标普500指数(QDII)C", "交易金额": 100,
         "买卖方向": "buy", "状态": "pending", "交易时间": _ts(2026, 9, 17, 10, 0)},
        {"_record_id": "b", "产品名称": "建信短债债券C", "交易金额": 200,
         "买卖方向": "buy", "状态": "completed", "交易时间": _ts(2026, 9, 17, 10, 0)},
    ]
    fake = FakeClient({"交易流水表": records})
    monkeypatch.setattr("src.feishu_client.get_feishu_client_or_none", lambda: fake)

    out = briefing._build_trade_summary()
    assert "建信短债债券C" in out
    assert "摩根标普500指数(QDII)C" not in out
    assert "None" not in out


def test_convert_row_rendered_with_target(monkeypatch):
    from src import briefing

    records = [{
        "_record_id": "c", "产品名称": OUT_NAME, "转入标的": IN_NAME,
        "买卖方向": "convert", "状态": "completed",
        "交易时间": _ts(2026, 9, 17, 12, 42),
    }]
    fake = FakeClient({"交易流水表": records})
    monkeypatch.setattr("src.feishu_client.get_feishu_client_or_none", lambda: fake)

    out = briefing._build_trade_summary()
    assert "转换" in out and IN_NAME[:10] in out


def test_cooldown_ignores_pending_and_counts_convert(monkeypatch):
    from src.strategy import _check_cooldown

    now = datetime.now(TZ)
    ts = int((now - timedelta(days=1)).timestamp() * 1000)
    records = [
        {"_record_id": "p", "产品名称": "某只美股基金", "买卖方向": "buy",
         "状态": "pending", "交易时间": ts},
    ]
    client = FakeClient({"交易流水表": records})
    assert _check_cooldown(client, "美股资产") is None  # pending 不产生冷却

    # 已完成的买入 → 触发
    records = [{"_record_id": "b", "产品名称": "摩根标普500指数(QDII)C",
                "买卖方向": "buy", "状态": "completed", "交易金额": 100, "交易时间": ts}]
    client = FakeClient({"交易流水表": records})
    assert _check_cooldown(client, "美股资产") is not None

    # 转换的「转入腿」计入该大类冷却（D3）
    records = [{"_record_id": "c", "产品名称": OUT_NAME, "转入标的": IN_NAME,
                "买卖方向": "convert", "状态": "completed", "交易时间": ts}]
    client = FakeClient({"交易流水表": records})
    assert _check_cooldown(client, "美股资产") is not None

    # 不同大类不受影响
    assert _check_cooldown(client, "固收资产") is None
