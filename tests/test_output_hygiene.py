# -*- coding: utf-8 -*-
"""LLM 输出卫生（2026-09-20）—— 思维链泄漏 + 开场白 + 四星号的回归测试。

## 背景（用户反馈）

用户看到 9/20 周报后问：
    "思维链我觉得可以摘除？但是里面有没有什么有用的信息呢？"

查证发现两件事：

**一、被当成"AI 综合解读"的那一段，本身就是思维链输出。**
`prompt_templates.CHAIN_OF_THOUGHT` 原文写着「请按以下步骤推理，**输出时体现思考过程**」，
于是模型老老实实把 4 步推理写进了卡片。实测 9 条真实推送：

| 时段 | 卡片总长 | AI 段 | 占比 | 编号步骤 |
|---|---|---|---|---|
| 9/18 早间 | 2274 | 921 | 40% | 4 |
| 9/18 午间 | 2372 | 974 | 41% | 4 |
| 9/18 夜盘 | 3225 | 1086 | 33% | 4 |
| 9/17 夜盘 | 2749 | 802 | 29% | 4 |
| 9/19 周末复盘 | 1976 | 1155 | **58%** | 6 |
| 9/18 收盘前 | 2000 | **154** | **7%** | 0 |

收盘前那条只有 7% 不是偶然 —— 它走 `fast_mode=True`，**不加载宪法与思维链**。
这反证了"AI 段篇幅 ≈ 思维链要求"。

**二、还夹带开场白与破损 markdown。**
正文里有「好的，这是根据您的投资宪法和市场信息生成的周报。」，
推理段里还有 `****固收 (偏离 +17.24%)****` 这种四星号。

## 改造原则

**保留推理、不输出过程。** 那 4 步推理是结论质量的保证（它逼模型查偏离度、
查铁律、做交叉判断），不能删；要删的是"把过程写出来"这个输出要求。
所以：
  · prompt 层：推理步骤保留，改用 `<output_style>` 明确要求"结论在前 + 依据在后"
  · 展示层：`_sanitize_llm_output()` 兜底剥离（Qwen 降级链不保证遵守指令）

## 这层坏了的症状是静默的

卡片照常推送，只是又变回"1. 环境基调 2. 持仓分析…"四大段编号长文。
所以测试重点是：
  1. 推理过程标题必须真的能从 prompt 拿到"不要输出"的指令
  2. 万一模型不听话，兜底必须能把尾巴剥掉
  3. 正常正文一个字都不能被误删
"""

from __future__ import annotations

import pytest

from src import briefing
from src.prompt_templates import build_analysis_prompt, CHAIN_OF_THOUGHT


# ═══════════════════════════════════════════════════════════════
# 0. 照抄线上真实文本
# ═══════════════════════════════════════════════════════════════

# 9/18 早间简报 AI 段的真实形态（结论在前无、编号推理在后）
REAL_MORNING_LEAK = """1. **当前环境与新闻解读**：今天宏观日历有日本央行和欧洲央行的讲话，可能影响全球资金流向。

2. **持仓偏离与信号对照**：
   - **A股**（偏离-7.4%）：低配，但系统提示"趋势左侧，等企稳再动手"。

3. **板块温差与机会**：今日日经225（+1.10%）领涨亚太。

4. **综合结论**：今天所有持仓大类均**按兵不动**。

**🧠 顾问推理过程**
1.  **环境基调**：宏观日历密集，以欧美经济数据为主。
2.  **仓位与板块分析**：
    *   ****固收****：严重超配(+17.2%)，应停止买入。*
3.  **结论**：必须动手，但方向唯一。
"""

# 用户看到的周报开场白
REAL_PREAMBLE = "好的，这是根据您的投资宪法和市场信息生成的周报。"


# ═══════════════════════════════════════════════════════════════
# 1. prompt 层：思维链还在，但"输出过程"的要求必须消失
# ═══════════════════════════════════════════════════════════════

def test_cot_still_carries_the_four_steps():
    """推理步骤本身要留住 —— 它是结论质量的保证，删了模型就不查铁律了。"""
    assert "当前大环境基调" in CHAIN_OF_THOUGHT
    assert "交叉判断" in CHAIN_OF_THOUGHT
    assert "给出结论" in CHAIN_OF_THOUGHT
    # 关键纪律不能丢
    assert "禁止用\"建议关注\"这类废话收尾" in CHAIN_OF_THOUGHT


def test_cot_no_longer_demands_showing_the_process():
    """病根：原来的「输出时体现思考过程」必须被移除。"""
    assert "输出时体现思考过程" not in CHAIN_OF_THOUGHT
    assert "不要把过程写进输出" in CHAIN_OF_THOUGHT


def test_cot_carries_output_style_requirements():
    """输出格式硬要求要齐（结论在前/禁编号小标题/禁开场白/字数）。"""
    assert "<output_style>" in CHAIN_OF_THOUGHT
    assert "不要在输出里写推理过程" in CHAIN_OF_THOUGHT
    assert "先给判断，再给依据" in CHAIN_OF_THOUGHT
    assert "禁止开场白" in CHAIN_OF_THOUGHT
    assert "150-250 字" in CHAIN_OF_THOUGHT


def test_prompt_reaches_llm_with_output_style():
    """端到端：style 要求必须真的到 prompt 里，且排在最后（指令权重最高）。"""
    prompt = build_analysis_prompt(role="你是量化投资顾问。", holdings_text="固收 67%")
    assert "<output_style>" in prompt
    assert "不要在输出里写推理过程" in prompt
    # 位于 prompt 末尾 —— 越靠后指令权重越高
    assert prompt.rstrip().endswith("</output_style>")


def test_include_cot_false_drops_whole_block():
    prompt = build_analysis_prompt(
        role="你是量化投资顾问。", holdings_text="固收 67%", include_cot=False
    )
    assert "<chain_of_thought>" not in prompt
    assert "<output_style>" not in prompt


# ═══════════════════════════════════════════════════════════════
# 2. 兜底剥离：模型不听话时也能收拾干净
# ═══════════════════════════════════════════════════════════════

def test_strips_reasoning_tail_with_emoji_header():
    out = briefing._sanitize_llm_output(REAL_MORNING_LEAK)
    assert "顾问推理过程" not in out
    assert "环境基调" not in out
    # 结论必须还在
    assert "按兵不动" in out


def test_strips_reasoning_tail_variants():
    """标题的各种写法都要认（粗体/井号/无 emoji/AI 前缀/冒号在星号内）。

    ⚠️ `**思考过程：**` 是实跑中真实出现的形态（闭合星号在冒号**之后**），
    早期正则按「`**` 再 `[:：]`」的顺序写导致漏判 —— 这里必须锁住。
    """
    body = "今天按兵不动，固收超配 +17.2% 是主因，A股低配 -7.4% 但仍在趋势左侧。" * 2
    for header in [
        "**推理过程**", "### 推理过程", "🧠 思考过程",
        "AI 分析过程：", "顾问推理过程", "**🧠 思维过程**",
        "**思考过程：**", "**思考过程**：", "思考过程：", "💭 推理逻辑",
    ]:
        out = briefing._sanitize_llm_output(f"{body}\n\n{header}\n1. 第一步推理……")
        assert "第一步推理" not in out, f"未剥离: {header}"
        assert "按兵不动" in out


def test_strips_preamble():
    out = briefing._sanitize_llm_output(REAL_PREAMBLE + "\n\n📅 **本周宏观回顾**\n· 无重大事件。")
    assert "好的，这是根据" not in out
    assert out.startswith("📅")


def test_preamble_variants():
    for pre in ["以下是本周的投资周报。", "以下是根据市场数据生成的简报。",
                "好的，以下是解读。"]:
        out = briefing._sanitize_llm_output(pre + "\n按兵不动，等右侧信号。")
        assert out.startswith("按兵不动"), f"未去开场白: {pre} → {out[:30]}"


def test_fixes_four_star_bold():
    """****固收**** → **固收**（否则飞书原样显示星号）。"""
    out = briefing._sanitize_llm_output("主要矛盾是 ****固收**** 超配 (+17.2%)。")
    assert "****" not in out
    assert "**固收**" in out


def test_cleans_trailing_orphan_asterisk():
    """坏列表常在末尾留一行孤立的 '*'。"""
    out = briefing._sanitize_llm_output("结论：按兵不动。\n*")
    assert out == "结论：按兵不动。"


# ── 最关键的一组：不许误伤 ──

def test_does_not_touch_normal_output():
    """正常输出必须一字不改（这是"清洗"而不是"改写"）。"""
    normal = (
        "今天按兵不动。固收超配 +17.2%，增量资金不得再投；"
        "A股低配 -7.4% 但仍在趋势左侧，按铁律 3 等右侧企稳。"
    )
    assert briefing._sanitize_llm_output(normal) == normal


def test_does_not_eat_body_starting_with_according_to():
    """正文首句含"根据…偏离度"不许被当成开场白删掉。"""
    text = "根据偏离度，A股是最该补的方向，-7.4% 是最大负偏离。"
    assert briefing._sanitize_llm_output(text) == text


def test_keeps_content_when_header_is_at_the_very_top():
    """异常形态：模型**先推理后结论**（实测旧 prompt 就是这个形态 ——
    输出第一行就是「**思考过程：**」）。此时截断会把整段清空，
    所以保持原文 + 告警：宁可留下噪声，也绝不丢结论。

    这一类只能靠 prompt 层从源头解决（实测已生效：1220 → 196 字）。
    """
    text = "**思考过程：**\n1. 环境基调……\n\n结论：本周资金投向 A 股。"
    out = briefing._sanitize_llm_output(text)
    assert "结论：本周资金投向 A 股。" in out, "误把结论一起删了"


def test_empty_and_whitespace_safe():
    assert briefing._sanitize_llm_output("") == ""
    assert briefing._sanitize_llm_output("   \n  ") == ""
    assert briefing._sanitize_llm_output(None) is None


# ═══════════════════════════════════════════════════════════════
# 3. 接线：清洗必须挂在所有 LLM 返回点上
# ═══════════════════════════════════════════════════════════════

class LeakyCompletions:
    """模拟"很听话地输了推理过程"的模型。"""

    def __init__(self, payload: str):
        self._payload = payload
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        msg = type("M", (), {"content": self._payload})()
        choice = type("C", (), {"message": msg, "finish_reason": "stop"})()
        return type("R", (), {"choices": [choice]})()


class LeakyClient:
    def __init__(self, payload: str):
        self.completions = LeakyCompletions(payload)
        self.chat = type("Chat", (), {"completions": self.completions})()


@pytest.fixture
def leaky_llm(monkeypatch):
    """返回假客户端本体，便于取到实际发出的 prompt。"""
    payload = REAL_PREAMBLE + "\n\n" + REAL_MORNING_LEAK
    client = LeakyClient(payload)
    monkeypatch.setattr("src.llm.get_llm_client", lambda: client)
    monkeypatch.setattr("src.llm.get_llm_model", lambda: "fake-model")
    monkeypatch.setattr(briefing, "_build_portfolio_summary", lambda: "固收 67% 美股 17%")
    return client


def test_ai_insight_cleans_leaky_output(leaky_llm):
    """日间简报（标准模式）出口必须已清洗。"""
    out = briefing._ai_insight("早间简报", "某条新闻标题足够长以通过长度校验")
    assert "顾问推理过程" not in out
    assert "环境基调" not in out
    assert "好的，这是根据" not in out
    assert "****" not in out


def test_ai_insight_fast_mode_cleans_too(leaky_llm):
    """收盘前 fast_mode 走另一条分支，也要清洗。"""
    out = briefing._ai_insight(
        "收盘前30分钟", "某条新闻标题足够长以通过长度校验",
        max_tokens=500, fast_mode=True,
    )
    assert "顾问推理过程" not in out
    assert "好的，这是根据" not in out


@pytest.fixture
def sun_evening_stubs(monkeypatch):
    """周报装配打桩（与 test_macro_brief 同款）。"""
    import src.macro_calendar as mc
    import src.global_news as gn
    import src.earnings_calendar as ec
    import src.strategy as st

    monkeypatch.setattr(briefing, "load_portfolio", lambda: [])
    monkeypatch.setattr(briefing, "_build_weekly_return",
                        lambda pf: "**📊 本周仓位盘点**\n总市值 ¥70,968")
    monkeypatch.setattr(briefing, "fetch_all_news", lambda **kw: [])
    monkeypatch.setattr(briefing, "_filter_by_keywords", lambda arts, pf, top_n=6: [])
    monkeypatch.setattr(briefing, "_save_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(st, "judge_from_feishu",
                        lambda: {"health_report": "**仓位健康报告**\n· 美股资产 16.96%"})
    # ⚠️ 必须给一条 past 事件：否则周报的 LLM 调用条件
    #    （past_events or future_events or global_news_text or weekend_news_summary）
    #    全为假，根本不会调模型，测试就空跑了
    monkeypatch.setattr(mc, "fetch_past_calendar",
                        lambda **kw: [{"title": "US CPI m/m", "date": "2026-09-16",
                                       "stars": "★★★", "country": "USD"}])
    monkeypatch.setattr(mc, "fetch_upcoming_calendar", lambda **kw: [])
    monkeypatch.setattr(gn, "_build_global_news_brief", lambda: "")
    monkeypatch.setattr(ec, "fetch_weekly_earnings", lambda **kw: [])
    return monkeypatch


def test_sun_evening_card_is_clean(sun_evening_stubs, leaky_llm):
    """周报是泄漏最严重的卡（58%）—— 端到端必须干净。"""
    card = briefing._build_sun_evening()
    assert "顾问推理过程" not in card
    assert "环境基调" not in card
    assert "好的，这是根据" not in card
    assert "****" not in card
    # 但正常内容必须在
    assert "本周仓位盘点" in card


def test_sun_evening_prompt_demands_conclusion_first(sun_evening_stubs, leaky_llm):
    """周报那条 LLM 调用也要带上输出格式要求（否则模型照旧写 4 步推理）。"""
    briefing._build_sun_evening()
    prompts = [c["messages"][0]["content"] for c in leaky_llm.completions.calls]
    assert prompts, "周报 LLM 没被调用"
    assert any("<output_style>" in p for p in prompts), "输出格式要求没进 prompt"
    assert any("不要在输出里写推理过程" in p for p in prompts)
