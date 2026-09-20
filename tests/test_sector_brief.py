# -*- coding: utf-8 -*-
"""板块轮动「揉进 AI 汇总」改造的单元测试（2026-09-20）。

改造前的形态：独立的「🔄 板块轮动」块，实测占夜盘卡片 47.5%/50.3%、
午间 44.8%/49.6% 的篇幅，且夜盘与午间内容高度重复——而 AI 综合解读本来
就已经拿到这些数字（9/18 夜盘解读里就写了"SoXX 相对纳指领涨 +1.7%"）。
用户结论：「我自己都没怎么关注，作用有限」。

改造后的形态：
  · 展示层 —— 只在出现强信号（|温差| ≥ 2pct）时留一行提示，平淡日完全折叠
  · AI 层  —— 全量温差并入解读上下文，并被**强制要求**翻译成
              "钱在往哪挪 + 对哪个持仓大类意味着什么"

这一层坏了的症状是**静默的**：卡片照常推送，只是板块信息要么整段消失、
要么退回成"罗列数字但没人解读"。所以测试重点是三件事：
  1. 展示层什么时候出、什么时候必须不出
  2. AI 层拿到了什么（强信号是否排前、条数是否受控）
  3. 规则有没有真的进 prompt（而不是只把数据丢进去）
"""

from __future__ import annotations

import pytest

from src import briefing


# ═══════════════════════════════════════════════════════════════
# 桩与夹具
# ═══════════════════════════════════════════════════════════════

def _sector(label: str, market: str, sector_pct: float, benchmark_pct: float) -> dict:
    """按 market_data.fetch_sector_deltas() 的真实字段形状造一条板块记录。"""
    delta = round(sector_pct - benchmark_pct, 2)
    if delta > 2:
        signal = "🔥 强势领涨"
    elif delta < -2:
        signal = "⚠️ 领跌大盘"
    else:
        signal = ""
    return {
        "sector": label, "code": f"C-{label}", "sector_pct": sector_pct,
        "benchmark_pct": benchmark_pct, "delta": delta, "signal": signal,
        "label": label, "market": market,
    }


# 覆盖三种状态：美强壮、港股平淡、A股弱信号（应只被 AI 层看到）
FIXTURE_SECTORS = [
    _sector("半导体", "us", 4.3, 1.5),        # 温差 +2.8 → 🔥
    _sector("生物科技", "us", -3.1, 1.5),     # 温差 -4.6 → ⚠️
    _sector("恒生红利", "hk", 0.6, 0.4),      # 温差 +0.2 → 无信号
    _sector("A股环保", "cn", 1.1, 0.9),       # 温差 +0.2 → 无信号
]


class FakeCompletions:
    def __init__(self, sink: dict):
        self._sink = sink

    def create(self, **kwargs):
        self._sink["messages"] = kwargs.get("messages")
        self._sink["model"] = kwargs.get("model")
        content = "测试解读：资金正从防御板块流向半导体，对应你的美股持仓。内容足够长以通过长度校验。"
        msg = type("M", (), {"content": content})()
        choice = type("C", (), {"message": msg, "finish_reason": "stop"})()
        return type("R", (), {"choices": [choice]})()


class FakeLLMClient:
    def __init__(self, sink: dict):
        self.chat = type("Chat", (), {"completions": FakeCompletions(sink)})()


@pytest.fixture
def captured_llm(monkeypatch):
    """把主模型客户端换成假客户端，捕获实际发出的 prompt。"""
    sink: dict = {}

    def _fake_get_client():
        return FakeLLMClient(sink)

    monkeypatch.setattr("src.llm.get_llm_client", _fake_get_client)
    monkeypatch.setattr("src.llm.get_llm_model", lambda: "fake-model")
    # 避免真实持仓计算带来 I/O
    monkeypatch.setattr(briefing, "_build_portfolio_summary", lambda: "固收 50% 美股 20%")
    return sink


# ═══════════════════════════════════════════════════════════════
# 1. 数据抓取与市场过滤
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("market_filter,expected", [
    ("all", ["us", "us", "hk", "cn"]),
    ("us", ["us", "us"]),
    ("hk_cn", ["hk", "cn"]),
    ("hk", ["hk"]),
    ("cn", ["cn"]),
])
def test_fetch_sector_safe_market_filter(monkeypatch, market_filter, expected):
    monkeypatch.setattr(briefing.market_data, "fetch_sector_deltas",
                        lambda: [dict(d) for d in FIXTURE_SECTORS])
    got = briefing._fetch_sector_safe(market_filter)
    assert [d["market"] for d in got] == expected


def test_fetch_sector_safe_swallows_exception(monkeypatch):
    """板块数据只是锦上添花，抓取失败绝不能中断整份简报。"""
    def boom():
        raise RuntimeError("飞书 500")

    monkeypatch.setattr(briefing.market_data, "fetch_sector_deltas", boom)
    assert briefing._fetch_sector_safe() == []


def test_fetch_sector_safe_empty_source(monkeypatch):
    monkeypatch.setattr(briefing.market_data, "fetch_sector_deltas", lambda: [])
    assert briefing._fetch_sector_safe() == []


# ═══════════════════════════════════════════════════════════════
# 2. 展示层：只在强信号时出，且必须够短
# ═══════════════════════════════════════════════════════════════

def test_signal_line_empty_without_strong_signal():
    """平淡日必须完全折叠——这正是减负的目的。"""
    quiet = [d for d in FIXTURE_SECTORS if not d["signal"]]
    assert briefing._build_sector_signal_line(quiet) == ""
    assert briefing._build_sector_signal_line([]) == ""


def test_signal_line_only_contains_strong_ones():
    line = briefing._build_sector_signal_line([dict(d) for d in FIXTURE_SECTORS])
    assert line.startswith("🔄 **板块异动**")
    assert "半导体" in line and "生物科技" in line
    # 无信号的板块不得出现在展示层
    assert "恒生红利" not in line and "A股环保" not in line
    # 温差方向要能看出来
    assert "🔺+2.8pct" in line and "🔻-4.6pct" in line


def test_signal_line_sorted_by_absolute_delta_and_capped():
    many = [
        _sector("S1", "us", 3.0, 0.0),    # +3.0
        _sector("S2", "us", -9.0, 0.0),   # -9.0 ← 应排第一
        _sector("S3", "us", 5.0, 0.0),    # +5.0
        _sector("S4", "us", 2.5, 0.0),    # +2.5
    ]
    line = briefing._build_sector_signal_line(many)
    assert line.index("S2") < line.index("S3") < line.index("S1")
    assert "S4" not in line               # 只展示前 3 条
    assert "等 4 项异动" in line           # 但总数要如实告知


def test_signal_line_no_tail_when_three_or_fewer():
    two = [_sector("A", "us", 4.0, 0.0), _sector("B", "us", -4.0, 0.0)]
    assert "等" not in briefing._build_sector_signal_line(two)


# ═══════════════════════════════════════════════════════════════
# 3. AI 层：全量喂、强信号排前、条数受控
# ═══════════════════════════════════════════════════════════════

def test_sector_for_ai_is_full_and_strong_first():
    text = briefing.build_sector_for_ai([dict(d) for d in FIXTURE_SECTORS])
    assert text.startswith("[板块温差]")
    # 全量：4 个板块一个都不能少（无信号的也要让 LLM 知道"没轮动"）
    for name in ["半导体", "生物科技", "恒生红利", "A股环保"]:
        assert name in text
    # 强信号排前
    assert text.index("生物科技") < text.index("恒生红利")
    assert text.index("半导体") < text.index("恒生红利")
    # 温差口径写清楚，避免 LLM 误读成绝对涨跌
    assert "温差" in text and "基准" in text


def test_sector_for_ai_respects_max_items():
    many = [_sector(f"S{i}", "us", float(i) + 3, 0.0) for i in range(12)]
    text = briefing.build_sector_for_ai(many, max_items=3)
    assert text.count("；") == 2          # 3 条 → 2 个分隔符
    assert "S11" in text and "S0" not in text   # 温差最大的在前


def test_sector_for_ai_empty():
    assert briefing.build_sector_for_ai([]) == ""


def test_build_sector_parts_fetches_once(monkeypatch):
    """一个时段只允许请求一次网络——否则会被 fetch_sector_deltas 的
    time.sleep(0.2)/条 限速拖成两倍耗时。"""
    calls = []

    def _counting():
        calls.append(1)
        return [dict(d) for d in FIXTURE_SECTORS]

    monkeypatch.setattr(briefing.market_data, "fetch_sector_deltas", _counting)
    line, ai_text = briefing._build_sector_parts()
    assert len(calls) == 1
    assert line and ai_text


# ═══════════════════════════════════════════════════════════════
# 4. 规则是否真的进了 prompt（用户诉求：要有解析，不能只罗列）
# ═══════════════════════════════════════════════════════════════

def _prompt_of(sink) -> str:
    msgs = sink.get("messages") or []
    return "\n".join(m.get("content", "") for m in msgs)


def test_standard_prompt_carries_sector_data_and_rule(captured_llm):
    sector_brief = briefing.build_sector_for_ai([dict(d) for d in FIXTURE_SECTORS])
    briefing._ai_insight("早间简报", "某条新闻标题足够长", sector_brief=sector_brief)
    prompt = _prompt_of(captured_llm)

    assert "半导体" in prompt                      # 数据进去了
    assert "相对流向" in prompt                     # 指标含义解释进去了
    assert "钱正在往哪个方向挪" in prompt            # 强制解析要求进去了
    assert "不要硬编故事" in prompt                  # 平淡日不许编故事
    assert "不要把数字念一遍就完事" in prompt        # 不许变成罗列


def test_standard_prompt_without_sector_has_no_rule(captured_llm):
    """没传板块数据时不许注入本条规则（宪法 §2.5 的 CoT 步骤不在此列）。"""
    briefing._ai_insight("早间简报", "某条新闻标题足够长")
    prompt = _prompt_of(captured_llm)
    assert "相对流向" not in prompt
    assert "钱正在往哪个方向挪" not in prompt
    assert "不要硬编故事" not in prompt


def test_fast_mode_prompt_carries_sector(captured_llm):
    sector_brief = briefing.build_sector_for_ai([dict(d) for d in FIXTURE_SECTORS])
    briefing._ai_insight("收盘前30分钟", "行情摘要足够长的一段文字",
                         fast_mode=True, sector_brief=sector_brief)
    prompt = _prompt_of(captured_llm)
    assert "半导体" in prompt
    assert "相对流向" in prompt


# ═══════════════════════════════════════════════════════════════
# 5. 降级兜底：AI 不可用时，板块数据不能被截成半截
# ═══════════════════════════════════════════════════════════════

def test_fallback_keeps_full_sector_line():
    """降级兜底原按行砍 120 字符，会把单行的板块串截断 → 末尾板块整段丢失。"""
    long_sector = "[板块温差] " + "；".join(
        f"S{i}[us] 行业+1.0% / 基准+0.5% / 温差+0.5pct" for i in range(8)
    )
    out = briefing._build_fallback_insight(long_sector, "某条新闻标题足够长")
    assert "板块温差信号" in out
    assert "S7" in out            # 最后一个板块必须还在
    assert "S0" in out


# ═══════════════════════════════════════════════════════════════
# 6. 时段卡片装配（端到端：独立块必须消失、信号行必须保留）
# ═══════════════════════════════════════════════════════════════

SIGNAL_LINE = "🔄 **板块异动**：半导体 +4.3%（温差🔺+2.8pct）"
SECTOR_AI = "[板块温差] 半导体[us] 行业+4.3% / 基准+1.5% / 温差+2.8pct 🔥 强势领涨"


@pytest.fixture
def midday_env(monkeypatch):
    """把午间时段的外部依赖全部打桩，只验证装配逻辑。"""
    seen = {}

    monkeypatch.setattr(briefing, "_build_asia_pacific_market",
                        lambda: "**🌏 亚太午盘**\n· 沪深300 +0.5%")
    monkeypatch.setattr(briefing, "fetch_all_news", lambda **kw: [])
    monkeypatch.setattr(briefing, "load_portfolio", lambda: [])
    monkeypatch.setattr(briefing, "_filter_by_keywords",
                        lambda articles, pf, top_n=6: [{"title": "测试新闻标题一二三四五"}])
    monkeypatch.setattr(briefing, "_portfolio_value_summary", lambda label="auto": "总市值 ¥1")
    monkeypatch.setattr(briefing, "_build_sector_parts",
                        lambda market_filter="all", max_ai_items=8: (SIGNAL_LINE, SECTOR_AI))

    def _fake_insight(context, news_titles, **kwargs):
        seen["sector_brief"] = kwargs.get("sector_brief")
        return "AI 快评内容"

    monkeypatch.setattr(briefing, "_ai_insight", _fake_insight)
    return seen


def test_midday_card_drops_standalone_block_and_keeps_signal(midday_env):
    card = briefing._build_midday()

    assert "🔄 **板块轮动**" not in card          # 独立块必须消失（改造目标）
    assert SIGNAL_LINE in card                    # 强信号行保留
    assert "🧠 **午间快评**" in card               # AI 汇总仍在
    assert midday_env["sector_brief"] == SECTOR_AI  # 数据确实喂给了 AI 层


def test_midday_card_silent_when_no_strong_signal(monkeypatch, midday_env):
    """无强信号时展示层应完全不出现（哪怕 AI 层仍拿到数据）。"""
    monkeypatch.setattr(briefing, "_build_sector_parts",
                        lambda market_filter="all", max_ai_items=8: ("", SECTOR_AI))
    card = briefing._build_midday()
    assert "板块" not in card
    assert midday_env["sector_brief"] == SECTOR_AI
