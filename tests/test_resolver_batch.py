# -*- coding: utf-8 -*-
"""同一运行内多笔 pending 命中同一底仓 —— 缓存一致性回归测试。

背景（2026-09-17 真机事故）
    用户在 12:42 / 12:43 / 12:44 录了 3 笔 C→E 转换，12:51 又录了 1 笔 E 类买入。
    这 4 笔共用同一个 T 日（9/17），因此**在同一次 resolver 运行里一起结算**。

    `name_to_rec` 只在运行开始时读一次底仓表（`resolve_pending` 主流程），
    而每笔结算是「缓存里的旧份额 ± 本次份额」后整体覆写：
        C 转出腿：308.23 - 200 → 108.23 → 258.23 → 278.23（后写覆盖前写）
        E 转入腿：0 + 198.20 → 49.55 → 29.73 → 16.22（最后一笔买入覆盖全部）
    结果：C 应 308.23-280 = 28.23，实际 278.23；E 应 0+277.48+16.22 = 293.70，实际 16.22。

    两处误差方向相反（C 虚增 250 份 ≈ +¥717、E 虚减 277.48 份 ≈ -¥803），
    总市值只差约 -¥86 —— 从简报上几乎看不出来，**完全静默**。必须靠用例守住。

修复方式：底仓写回飞书成功后，把同一份变更同步进内存缓存
（`_cache_apply`），使同一次运行内的后续笔基于最新份额计算。
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from src import pending_resolver as pr

TZ = timezone(timedelta(hours=8))

C_NAME = "景顺长城纳斯达克科技市值加权ETF联接（QDII）C"
E_NAME = "景顺长城纳斯达克科技市值加权ETF联接（QDII）E"

C_CODE = "017093"
E_CODE = "019118"

# 2026-09-17 真机数据
NAV_C = 2.8667
NAV_E = 2.8927


def _ts(y, mo, d, h, mi, s=0) -> int:
    return int(datetime(y, mo, d, h, mi, s, tzinfo=TZ).timestamp() * 1000)


# ═══════════════════════════════════════════════════════════════
# 假飞书客户端（自包含，不依赖其它测试文件）
# ═══════════════════════════════════════════════════════════════

class FakeClient:
    def __init__(self, tables: dict, fail_update: set | None = None):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.updates = []
        self.creates = []
        self.deletes = []
        # 指定底仓 record_id 的写回返回 False，用于验证"写失败不得污染缓存"
        self.fail_update = fail_update or set()
        self._n = 0

    def list_records(self, table):
        return [dict(r) for r in self.tables.get(table, [])]

    def update_record(self, table, rid, fields):
        self.updates.append((table, rid, dict(fields)))
        if table == "底仓表" and rid in self.fail_update:
            return False
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
        self.tables[table] = [r for r in self.tables.get(table, [])
                              if r.get("_record_id") != rid]
        return True


def _holdings(c_shares=308.23, e_shares=0.0) -> list:
    return [
        {"_record_id": "hC", "标的名称": C_NAME, "标的代码": C_CODE,
         "持仓份额": c_shares, "成本均价": 2.2898, "资产大类": "美股资产"},
        {"_record_id": "hE", "标的名称": E_NAME, "标的代码": E_CODE,
         "持仓份额": e_shares, "成本均价": 0, "资产大类": "美股资产"},
    ]


def _convert_row(rid, shares, h=12, mi=42) -> dict:
    return {
        "_record_id": rid,
        "产品名称": C_NAME,
        "转入标的": E_NAME,
        "转出份额": str(shares),
        "买卖方向": "convert",
        "状态": "pending",
        "交易时间": _ts(2026, 9, 17, h, mi, 52),
    }


def _buy_row(rid, amount, h=12, mi=51) -> dict:
    return {
        "_record_id": rid,
        "产品名称": E_NAME,
        "转入标的": None,
        "转出份额": None,
        "买卖方向": "buy",
        "状态": "pending",
        "交易金额": amount,
        "交易时间": _ts(2026, 9, 17, h, mi, 46),
    }


def _patch(monkeypatch, client, nav_map=None, auto_code=""):
    monkeypatch.setattr("src.env.is_production", lambda: True)
    monkeypatch.setattr(pr, "FeishuClient", lambda *a, **k: client)
    navs = {C_CODE: NAV_C, E_CODE: NAV_E} if nav_map is None else nav_map
    monkeypatch.setattr(pr, "_fetch_nav_on_date", lambda code, d: navs.get(code))
    monkeypatch.setattr(pr, "_auto_detect_fund_code", lambda name: auto_code)
    monkeypatch.setattr(pr, "_fuzzy_find_code", lambda name, codes: auto_code)


def _holding(client, rid):
    for r in client.tables["底仓表"]:
        if r.get("_record_id") == rid:
            return r
    return None


def _rows(client):
    return {r["_record_id"]: r for r in client.tables["交易流水表"]}


# ═══════════════════════════════════════════════════════════════
# 1. 真机事故复现：3 笔转换 + 1 笔买入，同一次运行
# ═══════════════════════════════════════════════════════════════

def test_three_converts_plus_buy_same_run(monkeypatch):
    """2026-09-17 事故：E 类转入份额被最后一笔买入覆盖掉。"""
    client = FakeClient({
        "交易流水表": [
            _convert_row("c1", 200, mi=42),
            _convert_row("c2", 50, mi=43),
            _convert_row("c3", 30, mi=44),
            _buy_row("b1", 46.93),
        ],
        "底仓表": _holdings(),
        "雷达观测表": [],
    })
    _patch(monkeypatch, client)

    res = pr.resolve_pending(dry_run=False)

    assert res["resolved"] == 4, res
    assert res["errors"] == 0, res

    c = _holding(client, "hC")
    e = _holding(client, "hE")

    # 转出腿必须**累加**扣减：308.23 - (200+50+30)
    assert c["持仓份额"] == pytest.approx(28.23, abs=1e-6), f"C 类份额错误: {c['持仓份额']}"
    # 转入腿必须**累加**增加：200→198.20、50→49.55、30→29.73，再加买入 46.93/2.8927=16.22
    assert e["持仓份额"] == pytest.approx(293.70, abs=1e-6), f"E 类份额错误: {e['持仓份额']}"

    # 四笔都应置 completed
    rows = _rows(client)
    assert all(rows[k]["状态"] == "completed" for k in ("c1", "c2", "c3", "b1"))

    # 转入腿确认份额逐笔正确（净值折算 198.20 / 49.55 / 29.73）
    assert rows["c1"]["确认份额"] == pytest.approx(198.20, abs=0.01)
    assert rows["c2"]["确认份额"] == pytest.approx(49.55, abs=0.01)
    assert rows["c3"]["确认份额"] == pytest.approx(29.73, abs=0.01)
    assert rows["b1"]["确认份额"] == pytest.approx(16.22, abs=0.01)


# ═══════════════════════════════════════════════════════════════
# 2. 同标的连续两笔买入
# ═══════════════════════════════════════════════════════════════

def test_two_buys_same_fund_same_run(monkeypatch):
    """两笔买入必须累加，而不是后一笔覆盖前一笔。"""
    client = FakeClient({
        "交易流水表": [_buy_row("b1", 100, mi=10), _buy_row("b2", 200, mi=11)],
        "底仓表": _holdings(e_shares=10.0),
        "雷达观测表": [],
    })
    _patch(monkeypatch, client)

    res = pr.resolve_pending(dry_run=False)
    assert res["resolved"] == 2, res

    e = _holding(client, "hE")
    expected = 10.0 + round(100 / NAV_E, 2) + round(200 / NAV_E, 2)
    assert e["持仓份额"] == pytest.approx(expected, abs=1e-6), f"E 类份额错误: {e['持仓份额']}"


# ═══════════════════════════════════════════════════════════════
# 3. 同标的先卖后买（同一运行）
# ═══════════════════════════════════════════════════════════════

def test_sell_then_buy_same_fund_same_run(monkeypatch):
    sell = {
        "_record_id": "s1", "产品名称": E_NAME, "买卖方向": "sell",
        "状态": "pending", "交易时间": _ts(2026, 9, 17, 10, 0, 0),
        "转出份额": "100", "交易金额": None,
    }
    client = FakeClient({
        "交易流水表": [sell, _buy_row("b1", 300, mi=11)],
        "底仓表": _holdings(e_shares=200.0),
        "雷达观测表": [],
    })
    _patch(monkeypatch, client)

    res = pr.resolve_pending(dry_run=False)
    assert res["resolved"] == 2, res

    e = _holding(client, "hE")
    expected = 200.0 - 100.0 + round(300 / NAV_E, 2)
    assert e["持仓份额"] == pytest.approx(expected, abs=1e-6), f"E 类份额错误: {e['持仓份额']}"


# ═══════════════════════════════════════════════════════════════
# 4. 清仓删除后同批次再来一笔 → 必须新建，不能写已删记录
# ═══════════════════════════════════════════════════════════════

def test_sold_out_then_buy_same_run_creates_new_row(monkeypatch):
    sell_all = {
        "_record_id": "s1", "产品名称": E_NAME, "买卖方向": "sell",
        "状态": "pending", "交易时间": _ts(2026, 9, 17, 10, 0, 0),
        "转出份额": str(200), "交易金额": None,
    }
    client = FakeClient({
        "交易流水表": [sell_all, _buy_row("b1", 300, mi=11)],
        "底仓表": _holdings(e_shares=200.0),
        "雷达观测表": [],
    })
    _patch(monkeypatch, client, auto_code=E_CODE)

    res = pr.resolve_pending(dry_run=False)

    # 清仓记录必须被删除
    assert ("底仓表", "hE") in client.deletes
    assert _holding(client, "hE") is None

    # 后面的买入不得再写已删记录，而应新建一行
    assert not any(rid == "hE" for tbl, rid, _ in client.updates if tbl == "底仓表")
    new_rows = [r for r in client.tables["底仓表"] if r.get("_record_id") != "hC"]
    assert len(new_rows) == 1, [r["_record_id"] for r in new_rows]
    assert new_rows[0]["持仓份额"] == pytest.approx(round(300 / NAV_E, 2), abs=1e-6)
    assert res["errors"] == 0, res


# ═══════════════════════════════════════════════════════════════
# 5. 写回失败时不得污染缓存
# ═══════════════════════════════════════════════════════════════

def test_failed_write_does_not_poison_cache(monkeypatch):
    """第一笔写回失败 → 缓存保持真实值，第二笔仍按正确基数累加。"""
    client = FakeClient({
        "交易流水表": [_buy_row("b1", 100, mi=10), _buy_row("b2", 200, mi=11)],
        "底仓表": _holdings(e_shares=10.0),
        "雷达观测表": [],
    }, fail_update={"hE"})  # 两笔都会写 hE，都失败
    _patch(monkeypatch, client)

    res = pr.resolve_pending(dry_run=False)

    assert res["errors"] == 2, res
    e = _holding(client, "hE")
    # 一笔都没写成功 → 份额保持原值
    assert e["持仓份额"] == pytest.approx(10.0, abs=1e-6)
    rows = _rows(client)
    # 流水表照旧置 completed（与既有行为一致），但底仓未动 → 已 errors 告警
    assert rows["b1"]["状态"] == "completed"
