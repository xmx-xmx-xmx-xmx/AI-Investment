# -*- coding: utf-8 -*-
"""底仓名称匹配的「歧义安全网」测试。

背景（2026-09-17 真机踩坑）：
  用户买入 E 类 46.93 元，快捷指令却把它记成了 C 类。根因是**提示词的 few-shot
  示例里写了具体基金名（结尾恰是 C）**，弱模型照抄示例的类别字母。用户把示例改成
  占位符 + 显式要求识别份额类别字母后已修复。

  但代码侧原先没有安全网：`_fuzzy_match_product` 对**缺份额类别字母**的名称
  （如 `……联接(QDII)`）会"取第一个子串命中"，而底仓里 A/C/E 三行都会命中 ——
  后果是把 E 类的份额静默记到 C 类头上，且**不会报错**。

本文件锁住新行为：命中多只不同标的 → 拒绝猜测 + 明确告警跳过，绝不静默记错。
"""

from __future__ import annotations

import pytest

from src import pending_resolver as pr
from tests.test_convert import (
    FakeClient,
    OUT_NAME,     # ……ETF联接（QDII）C
    IN_NAME,      # ……ETF联接（QDII）E
    _patch,
    _ts,
)

BASE_NAME = "景顺长城纳斯达克科技市值加权ETF联接（QDII）"
A_NAME = "景顺长城纳斯达克科技市值加权ETF联接（QDII）A"


def _holdings3() -> list:
    """底仓：同一只基金的 A / C / E 三个份额类别同行（真实表就是这样）。"""
    return [
        {"_record_id": "hA", "标的名称": A_NAME, "标的代码": "017091",
         "持仓份额": 0, "成本均价": 0, "资产大类": "美股资产"},
        {"_record_id": "hC", "标的名称": OUT_NAME, "标的代码": "017093",
         "持仓份额": 308.23, "成本均价": 2.2898, "资产大类": "美股资产"},
        {"_record_id": "hE", "标的名称": IN_NAME, "标的代码": "019118",
         "持仓份额": 0, "成本均价": 0, "资产大类": "美股资产"},
    ]


def _name_map(holdings):
    return {str(h["标的名称"]): dict(h) for h in holdings}


def _buy_row(**over) -> dict:
    row = {
        "_record_id": "recBUY",
        "产品名称": IN_NAME,
        "交易金额": 46.93,
        "买卖方向": "buy",
        "状态": "pending",
        "交易时间": _ts(2026, 9, 17, 12, 51, 46),
    }
    row.update(over)
    return row


def _client(trades, holdings) -> FakeClient:
    return FakeClient({
        "交易流水表": trades,
        "底仓表": holdings,
        "雷达观测表": [],
    })


def _holding(client, rid):
    for r in client.tables["底仓表"]:
        if r.get("_record_id") == rid:
            return r
    return None


# ═══════════════════════════════════════════════════════════════
# 1. 名称匹配本身
# ═══════════════════════════════════════════════════════════════

def test_full_name_with_class_letter_matches_its_own_class():
    """带类别字母的全名 → 各命中各的（C 不能命中 E，反之亦然）。"""
    nm = _name_map(_holdings3())
    assert pr._fuzzy_match_product(IN_NAME, nm)["标的代码"] == "019118"
    assert pr._fuzzy_match_product(OUT_NAME, nm)["标的代码"] == "017093"
    assert pr._fuzzy_match_product(A_NAME, nm)["标的代码"] == "017091"


def test_wrong_class_letter_never_falls_back_to_another_class():
    """关键：底仓**没有** E 类时，E 类名称不能子串落到 C 类上。"""
    nm = _name_map([h for h in _holdings3() if h["标的代码"] != "019118"])
    assert pr._fuzzy_match_product(IN_NAME, nm) is None


def test_missing_class_letter_is_ambiguous_not_guessed():
    """缺类别字母 → 命中 A/C/E 三只 → 判定歧义，返回 None。"""
    nm = _name_map(_holdings3())
    assert pr._fuzzy_match_product(BASE_NAME, nm) is None
    conflicts = pr._find_holding_conflicts(BASE_NAME, nm)
    assert {c["标的代码"] for c in conflicts} == {"017091", "017093", "019118"}


def test_missing_class_letter_but_single_candidate_still_matches():
    """只有一只同族标的时不算歧义，照常匹配（不过度拦截）。"""
    nm = _name_map([h for h in _holdings3() if h["标的代码"] == "017093"])
    assert pr._fuzzy_match_product(BASE_NAME, nm)["标的代码"] == "017093"
    assert pr._find_holding_conflicts(BASE_NAME, nm) == []


def test_full_width_and_half_width_parens_are_equal():
    """全角/半角括号归一化后视为同一只。"""
    nm = _name_map(_holdings3())
    half = IN_NAME.replace("（", "(").replace("）", ")")
    assert pr._fuzzy_match_product(half, nm)["标的代码"] == "019118"


# ═══════════════════════════════════════════════════════════════
# 2. 端到端：买入行
# ═══════════════════════════════════════════════════════════════

def test_buy_with_full_name_resolves_positive_control(monkeypatch):
    """阳性对照：带类别字母的 E 类买入能正常成交（安全网不误伤）。"""
    client = _client([_buy_row()], _holdings3())
    _patch(monkeypatch, client, {"019118": 2.8386})

    res = pr.resolve_pending(dry_run=False)

    assert res["resolved"] == 1
    e = _holding(client, "hE")
    assert e["持仓份额"] == pytest.approx(round(46.93 / 2.8386, 2), abs=1e-6)
    assert _holding(client, "hC")["持仓份额"] == pytest.approx(308.23)
    assert client.tables["交易流水表"][0]["状态"] == "completed"


def test_buy_without_class_letter_is_skipped_not_guessed(monkeypatch):
    """缺类别字母 → 跳过 + 告警；绝不建新底仓、绝不动任何持仓。"""
    client = _client([_buy_row(产品名称=BASE_NAME)], _holdings3())
    _patch(monkeypatch, client, {"019118": 2.8386, "017093": 2.8131})

    res = pr.resolve_pending(dry_run=False)

    assert res["resolved"] == 0
    assert res["skipped"] == 1
    assert client.creates == [] and client.updates == []
    assert "歧义" in res["details"][0]["reason"]
    for rid in ("hA", "hC", "hE"):
        assert _holding(client, rid)["持仓份额"] == pytest.approx(
            {"hA": 0, "hC": 308.23, "hE": 0}[rid]
        )
    assert client.tables["交易流水表"][0]["状态"] == "pending"


# ═══════════════════════════════════════════════════════════════
# 3. 端到端：转换行（任一转腿歧义 → 两腿都不落库）
# ═══════════════════════════════════════════════════════════════

def test_convert_in_leg_without_class_letter_blocks_both_legs(monkeypatch):
    """转入腿名称缺类别字母 → 跳过，转出腿也**不许**扣份额。"""
    row = {
        "_record_id": "recCONV",
        "产品名称": OUT_NAME,
        "转入标的": BASE_NAME,          # ← 缺类别字母
        "转出份额": "200",
        "买卖方向": "convert",
        "状态": "pending",
        "交易时间": _ts(2026, 9, 17, 12, 42, 52),
    }
    client = _client([row], _holdings3())
    _patch(monkeypatch, client, {"017093": 2.8131, "019118": 2.8386})

    res = pr.resolve_pending(dry_run=False)

    assert res["resolved"] == 0
    assert res["skipped"] == 1
    assert "缺少份额类别字母" in res["details"][0]["reason"]
    assert _holding(client, "hC")["持仓份额"] == pytest.approx(308.23)   # 未扣
    assert _holding(client, "hE")["持仓份额"] == 0                        # 未加
    assert client.tables["交易流水表"][0]["状态"] == "pending"


def test_convert_out_leg_without_class_letter_blocks_too(monkeypatch):
    """转出腿名称缺类别字母 → 同样跳过。"""
    row = {
        "_record_id": "recCONV",
        "产品名称": BASE_NAME,          # ← 缺类别字母
        "转入标的": IN_NAME,
        "转出份额": "200",
        "买卖方向": "convert",
        "状态": "pending",
        "交易时间": _ts(2026, 9, 17, 12, 42, 52),
    }
    client = _client([row], _holdings3())
    _patch(monkeypatch, client, {"017093": 2.8131, "019118": 2.8386})

    res = pr.resolve_pending(dry_run=False)

    assert res["skipped"] == 1
    assert "缺少份额类别字母" in res["details"][0]["reason"]
    assert _holding(client, "hC")["持仓份额"] == pytest.approx(308.23)
