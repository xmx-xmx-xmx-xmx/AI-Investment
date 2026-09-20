# -*- coding: utf-8 -*-
"""宏观日历「揉进 AI 汇总」改造的回归测试（2026-09-20）。

用户原话（看到 9/20 周报末尾那一屏）：
    "说实话，我根本没看懂这个内容，这个内容到底是什么东西，
     如果没什么用的话，要么把它融合到解析AI解析部分，要么就给他删掉算了"

改造前的形态：卡片末尾一屏「📅 今日宏观日历」原始列表 ——
    · ★★ [EUR] French Flash Manufacturing PMI（预期 50.9，前值 51.5）
    · ★★ [EUR] ECB President Lagarde Speaks
    · ★★ [USD] Unemployment Claims（预期 201K，前值 196K）
    ...

它有三个问题：
  1. **标题写死「今日」，但周日周报展示的是下周事件** —— 标签本身就是错的
  2. 只罗列指标名，既不说"这是什么"也不说"跟我有什么关系"
  3. **同一份数据 AI 已经拿到并用过了** —— 9/18 周报里 AI 自己写了
     "如果德法PMI数据集体不及预期→…"，所以卡片末尾那份纯属发第二遍

改造后的形态（与「板块轮动」一致的展示层/AI 层分层）：
  · 展示层 —— 只留 ★★★（FOMC/CPI/非农级）一行，无则完全静默
  · AI 层  —— 过滤掉区域性噪声后全量喂给 LLM，并由 _MACRO_AI_RULE
              强制"先翻译成大白话，再落到具体持仓大类"

这一层坏了的症状是**静默的**：卡片照常推送，只是要么整屏看不懂的指标名回来了，
要么 AI 不再解释宏观数据。所以测试重点是：
  1. 原始列表必须从卡片里彻底消失
  2. 全 ★★ 的平淡周必须一行都不占
  3. 规则有没有真的进 prompt（而不是只把数据丢进去）
"""

from __future__ import annotations

import pytest

from src import briefing


# ═══════════════════════════════════════════════════════════════
# 夹具：直接照抄 2026-09-20 真实周报末尾那一屏的 8 条事件
# ═══════════════════════════════════════════════════════════════

def _evt(title: str, country: str, impact: str, day: int = 21) -> dict:
    """按 fetch_calendar() 的真实返回形状造一条事件。"""
    stars = {"High": "★★★", "Medium": "★★", "Low": "★"}[impact]
    return {
        "title": title, "country": country, "impact": impact,
        "date": f"2026-09-{day:02d}T08:30:00-04:00",
        "forecast": "", "previous": "", "stars": stars,
        "is_high_priority": False, "asset_class": "", "sensitivity_group": "",
    }


# 用户实际看到的那 8 条 —— 全部 ★★
REAL_WEEK_EVENTS = [
    _evt("French Flash Manufacturing PMI", "EUR", "Medium"),
    _evt("French Flash Services PMI", "EUR", "Medium"),
    _evt("German Flash Manufacturing PMI", "EUR", "Medium"),
    _evt("German Flash Services PMI", "EUR", "Medium"),
    _evt("ECB President Lagarde Speaks", "EUR", "Medium"),
    _evt("ECB President Lagarde Speaks", "EUR", "Medium"),
    _evt("Unemployment Claims", "USD", "Medium"),
    _evt("Revised UoM Consumer Sentiment", "USD", "Medium"),
]


class FakeCompletions:
    def __init__(self, sink: dict):
        self._sink = sink
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        self._sink.setdefault("prompts", []).append(
            (kwargs.get("messages") or [{}])[0].get("content", "")
        )
        content = "测试解读：德法PMI是欧洲制造业景气度，走弱会拖累全球风险偏好，对应你的美股持仓。内容够长通过校验。"
        msg = type("M", (), {"content": content})()
        choice = type("C", (), {"message": msg, "finish_reason": "stop"})()
        return type("R", (), {"choices": [choice]})()


class FakeLLMClient:
    def __init__(self, sink: dict):
        self.completions = FakeCompletions(sink)
        self.chat = type("Chat", (), {"completions": self.completions})()


@pytest.fixture
def captured_llm(monkeypatch):
    """把主模型换成假客户端，捕获实际发出的 prompt。"""
    sink: dict = {}

    monkeypatch.setattr("src.llm.get_llm_client", lambda: FakeLLMClient(sink))
    monkeypatch.setattr("src.llm.get_llm_model", lambda: "fake-model")
    monkeypatch.setattr(briefing, "_build_portfolio_summary", lambda: "固收 50% 美股 20%")
    return sink


# ═══════════════════════════════════════════════════════════════
# 1. AI 层：规则必须真的进 prompt
# ═══════════════════════════════════════════════════════════════

def test_macro_rule_injected_when_macro_context_present(captured_llm):
    briefing._ai_insight(
        "早间简报", "某条新闻标题足够长以通过长度校验",
        macro_context="今日（及近期）宏观经济日历：\n· ★★★ [USD] US CPI m/m",
    )
    prompt = captured_llm["prompts"][-1]
    assert "【宏观日历的用法】" in prompt          # 规则进去了
    assert "这是什么" in prompt                    # 三要素①
    assert "对你哪一个持仓大类意味着什么" in prompt  # 三要素③
    assert "禁止只罗列指标名" in prompt             # 明令禁止罗列
    assert "不要硬凑" in prompt


def test_macro_rule_absent_without_macro_context(captured_llm):
    """没有日历数据时不许注入规则，否则是空喊口号、白占 prompt。"""
    briefing._ai_insight("早间简报", "某条新闻标题足够长以通过长度校验")
    prompt = captured_llm["prompts"][-1]
    assert "【宏观日历的用法】" not in prompt


def test_macro_context_data_reaches_prompt(captured_llm):
    """数据本身也要在（规则之外）。"""
    briefing._ai_insight(
        "早间简报", "某条新闻标题足够长以通过长度校验",
        macro_context="今日（及近期）宏观经济日历：\n· ★★★ [USD] US CPI m/m",
    )
    prompt = captured_llm["prompts"][-1]
    assert "US CPI m/m" in prompt


# ═══════════════════════════════════════════════════════════════
# 2. 端到端：周日周报卡片里原始列表必须彻底消失
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def sun_evening_stubs(monkeypatch):
    """把 _build_sun_evening 的所有 I/O 打桩，只留卡片装配逻辑。"""
    import src.macro_calendar as mc
    import src.global_news as gn
    import src.earnings_calendar as ec
    import src.strategy as st

    monkeypatch.setattr(briefing, "load_portfolio", lambda: [])
    monkeypatch.setattr(briefing, "_build_weekly_return",
                        lambda pf: "**📊 本周仓位盘点**\n总市值 ¥70,968")
    monkeypatch.setattr(briefing, "fetch_all_news", lambda **kw: [])
    monkeypatch.setattr(briefing, "_filter_by_keywords",
                        lambda arts, pf, top_n=6: [])
    monkeypatch.setattr(briefing, "_save_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(st, "judge_from_feishu",
                        lambda: {"health_report": "**仓位健康报告**\n· 美股资产 16.96%"})
    monkeypatch.setattr(mc, "fetch_past_calendar", lambda **kw: [])
    monkeypatch.setattr(gn, "_build_global_news_brief", lambda: "")
    monkeypatch.setattr(ec, "fetch_weekly_earnings", lambda **kw: [])
    return monkeypatch


def test_sun_evening_card_drops_raw_macro_list(sun_evening_stubs, captured_llm):
    """用户实际遇到的那一屏 8 条 ★★ —— 必须一条都不剩。"""
    import src.macro_calendar as mc
    sun_evening_stubs.setattr(mc, "fetch_upcoming_calendar",
                              lambda **kw: [dict(e) for e in REAL_WEEK_EVENTS])

    card = briefing._build_sun_evening()

    assert "今日宏观日历" not in card
    assert "French Flash Manufacturing PMI" not in card
    assert "German Flash Services PMI" not in card
    assert "ECB President Lagarde Speaks" not in card
    assert "Unemployment Claims" not in card
    # 星级标记也不该漏出来（那是原始列表的形态）
    assert "★★ [" not in card


def test_sun_evening_shows_single_line_for_high_impact(sun_evening_stubs, captured_llm):
    """真有 ★★★ 时，必须给一行"下周什么时候要留意"，且只有一行。"""
    import src.macro_calendar as mc
    events = [dict(e) for e in REAL_WEEK_EVENTS]
    events.append(_evt("FOMC Rate Decision", "USD", "High", day=24))
    sun_evening_stubs.setattr(mc, "fetch_upcoming_calendar", lambda **kw: events)

    card = briefing._build_sun_evening()

    assert "📅 **下周关键**：" in card
    assert "FOMC Rate Decision" in card
    # 噪声仍然不许出现
    assert "French Flash Manufacturing PMI" not in card


def test_sun_evening_ai_prompt_carries_macro_rule(sun_evening_stubs, captured_llm):
    """周报那条 LLM 调用也必须带上宏观解析规则。"""
    import src.macro_calendar as mc
    sun_evening_stubs.setattr(mc, "fetch_upcoming_calendar",
                              lambda **kw: [dict(e) for e in REAL_WEEK_EVENTS])

    briefing._build_sun_evening()

    assert captured_llm["prompts"], "周报 LLM 没被调用"
    prompt = captured_llm["prompts"][-1]
    assert "【宏观日历的用法】" in prompt
    # 区域性噪声不应进入 prompt（过滤在喂给 AI 之前）
    assert "French Flash Manufacturing PMI" not in prompt
    # 但 USD 数据要留着（有直接链条）
    assert "Unemployment Claims" in prompt
