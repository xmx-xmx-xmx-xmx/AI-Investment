# -*- coding: utf-8 -*-
"""要闻展示层策展测试（P1 #4 第 4 刀，2026-09-23）。

用例全部取自 9/21–9/23 的**真实卡片原文**（从飞书群 oc_b347… 拉下来的 11 条推送），
不是编造的样本 —— 因此天然锁住"真实世界里会出现什么重复"。

背景：要闻是多源抓取（金十数据 + 华尔街见闻），而 `fetch_all_news()` 的去重只有
`title[:60]` 的**字面精确匹配**，语义级同一事件会各留一条。实测每张卡片要闻块
有 1/4–1/3 的条目是同一事件的第二个来源。
"""

from __future__ import annotations

import pytest

from src.news_fetcher import (
    curate_for_display,
    is_quote_line,
    normalize_for_dedup,
    same_event,
)


# ═══════════════════════════════════════════════════════════════
# 真实重复样本（应合并）
# ═══════════════════════════════════════════════════════════════

MS_A = ('【微软拟推Copilot最高五折企业折扣，同步上线AI“超级应用”】金十数据9月23日讯，'
        '据The Information报道，知情人士透露，微软(MSFT.O)高管本周告诉销售人员，'
        '针对承诺购买大量席位并根据某些功能的使用情况进行额外支付的企业客户，'
        '将授权对其CopilotAI软件订阅提供30%到50%的更大幅度折扣。')
MS_B = '据The Information：微软(MSFT.O)将提高Copilot折扣力度，同时推出AI“超级应用”。'
MS_C = '微软将加大Copilot折扣，推出人工智能“超级应用”。（The Information）'

GOOL_A = ('美联储古尔斯比：通胀面临的供给和需求混合压力，对步伐的影响大于对终点的影响。 '
          '如果通胀主要由需求驱动，利率应对将需要更激进且更前置。')
GOOL_B = '美联储古尔斯比：不会将未来可能的加息视为对去年降息的逆转。'
GOOL_C = '美联储古尔斯比：如果通胀主要由需求驱动，利率政策应对将需要更加激进，并更早采取行动。'
GOOL_D = ('[译] 美联储Goolsbee称，多重通胀压力下，美联储更接近需要“采取行动”。'
          '（Fed\'s Goolsbee says officials getting closer to needing to act）')

IRAN_A = '[译] 伊朗议会副议长称霍尔木兹海峡属伊朗，阿曼亦接壤（Iran\'s parliament deputy speaker said）'
IRAN_B = '[译] 伊朗议会副议长称伊朗过去并持续履行海峡承诺（Iran\'s parliament deputy speaker said Ir）'
IRAN_C = '[译] 伊朗议会副议长称当前会谈与霍尔木兹海峡紧密相关（Iran\'s parliament deputy speaker said ta）'

HONOR_A = ('[译] 荣耀方飞称荣耀与阿里将联合开发下一代AI手机的五个行业模型及解决方案。'
           '（Honor’s Fang Fei says Honor and Alibaba will jointly develop）')
HONOR_B = ('【荣耀方飞：与阿里共建面向下一代AI手机的五大垂域模型与解决方案】金十数据9月22日讯，'
           '9月22日，荣耀产品线总裁方飞在阿里云栖主论坛上表示')

MACRON_A = ('[译] 马克龙称美法将重点保护霍尔木兹海峡航运并推进俄乌和平协议'
            '（Macron: US, France to focus on protecting shipping）')
MACRON_B = ('[译] 沙特国家电视台：马克龙称与特朗普讨论重新开放霍尔木兹海峡方案'
            '（According to Saudi Arabian state television）')

QUOTE_A = ('上证指数早盘收报3938.08点，跌0.36%。 深证成指早盘收报13644.72点，跌0.58%。 '
           '创业板指早盘收报3385.30点，跌0.43%。')
QUOTE_B = ('【A股午评：三大股指早盘水下震荡 玻璃基板、CRO概念表现活跃】金十数据9月23日讯，'
           'A股三大股指今早高开低走，创业板指盘初快速下探翻绿，大盘于水下震荡盘整')


# ═══════════════════════════════════════════════════════════════
# 真实"形似但不同"样本（不得合并）—— 误合的代价比漏合高
# ═══════════════════════════════════════════════════════════════

SAME_CARD_ITEMS = [
    '【银河证券：产业韧性凸显 美联储加息不改AI长期逻辑】金十数据9月21日讯，银河证券研报称，长期维度',
    '【摩根大通：韩国利率前景存上行风险，终端利率或超过3.75%】金十数据9月21日讯，摩根大通认为韩国',
    '【韩国本月前20天出口创历史新高 芯片出口飙升近260%】金十数据9月21日讯，韩国海关周一数据显示',
    '【贝森特身家披露：资产至少2.28亿美元，去年赚510万美元】金十数据9月23日讯，最新公布的财务披露文件显示',
    '欧洲央行管委Nagel警告政策利率可能需要进入轻度限制性区间',
    '境外机构看好中国资产，人民币债券全球配置价值提升',
]


# ═══════════════════════════════════════════════════════════════
# same_event —— 正例
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("a,b", [
    (MS_A, MS_B), (MS_A, MS_C), (MS_B, MS_C),                  # 微软 Copilot ×3
    (GOOL_A, GOOL_C), (GOOL_A, GOOL_B),                         # 古尔斯比同一场讲话
    (IRAN_A, IRAN_B), (IRAN_A, IRAN_C), (IRAN_B, IRAN_C),       # 伊朗议会副议长 ×3
    (HONOR_A, HONOR_B),                                         # 荣耀与阿里
    (MACRON_A, MACRON_B),                                       # 马克龙/霍尔木兹海峡
])
def test_same_event_true(a, b):
    assert same_event(a, b) is True


def test_known_miss_is_intentional():
    """已知漏网（**故意不合**）：`N大指数早盘收报` vs `【A股午评：…】`。

    两者其实都在讲"上午 A 股表现"，但一个是裸报指数数字、一个是带分析的成稿，
    措辞几乎无重叠。早期版本会把 `跌0.36%` + `深证成指` 连成 `跌036深证成指`
    （8 字公共串）而"偶然合并" —— 那是数字对齐巧合，不是同一事件的证据，
    会误伤不相关条目。现已去掉数字，正确降级为不合。
    """
    assert same_event(QUOTE_A, QUOTE_B) is False


# ═══════════════════════════════════════════════════════════════
# same_event —— 反例（不得误合）
# ═══════════════════════════════════════════════════════════════

def test_no_false_merge_within_one_card():
    """同一张卡片里的 6 条不同新闻，两两都不得被判为同一事件。

    ⚠️ 这组是真实的误合陷阱：它们都带「金十数据9月X日讯」套话，
    若不做套话剥离，会被算成高度相似。
    """
    for i in range(len(SAME_CARD_ITEMS)):
        for j in range(i + 1, len(SAME_CARD_ITEMS)):
            assert not same_event(SAME_CARD_ITEMS[i], SAME_CARD_ITEMS[j]), \
                f"误合：{SAME_CARD_ITEMS[i][:20]} ≈ {SAME_CARD_ITEMS[j][:20]}"


def test_short_titles_never_merged():
    """过短的标题不参与判重（避免"某基金涨"这类噪声互相吞掉）"""
    assert same_event("沪指涨", "沪指涨了") is False


def test_boilerplate_stripped_by_normalize():
    n = normalize_for_dedup('【A股午评：三大股指走高】金十数据9月23日讯，A股三大股指今早高开')
    assert '金十数据' not in n
    assert '9月23日' not in n
    assert n.startswith('A股午评')


# ═══════════════════════════════════════════════════════════════
# is_quote_line —— 纯行情行
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("line", [
    '纳指期货: 31,073.05　🔺+0.14% 14:33:55',
    '标普期货: 7,840.32　🔺+0.11% 14:33:53',
    '纳指期货: 30,781.74　🔻-0.01% 14:34:23',
    '恒指期货：25,100.00　🔺+0.32% 09:15',
])
def test_quote_line_detected(line):
    assert is_quote_line(line) is True


@pytest.mark.parametrize("line", [
    '夜盘开盘，国内期货主力合约涨多跌少，沪铜、国际铜涨超1%。',
    '现货白银日内涨幅扩大至1.00%，现报66.68美元/盎司。',
    '中国央行在香港发行600亿元人民币6个月期央票，中标利率1.37%。',
    '【A股午评：三大股指早盘水下震荡】金十数据9月23日讯，A股三大股指今早高开低走',
    '微软将加大Copilot折扣，推出人工智能“超级应用”。',
])
def test_quote_line_not_false_positive(line):
    """真新闻（含期货/现货字样）不得被当成报价行剥掉"""
    assert is_quote_line(line) is False


# ═══════════════════════════════════════════════════════════════
# curate_for_display
# ═══════════════════════════════════════════════════════════════

def _mk(titles):
    return [{"title": t, "source": "测试源"} for t in titles]


def test_curate_merges_multi_source_same_event():
    out = curate_for_display(_mk([MS_A, MS_B, MS_C]), max_items=8)
    assert len(out) == 1
    # 保留信息最全（最长）的那条
    assert out[0]["title"] == MS_A


def test_curate_keeps_longest_variant():
    out = curate_for_display(_mk([IRAN_C, IRAN_A]), max_items=8)
    assert len(out) == 1
    assert out[0]["title"] == max([IRAN_A, IRAN_C], key=len), "应保留字面更长的那条"


def test_curate_does_not_pad_back_to_max_items():
    """⚠️ 刻意不补位：去重后剩几条就几条。

    用户要的是"同一件事不再说三遍"，不是"用新条目把省下的位置填满"。
    """
    titles = [MS_A, MS_B, MS_C] + SAME_CARD_ITEMS[:4]
    out = curate_for_display(_mk(titles), max_items=8)
    assert len(out) == 5, f"应只剩 1(微软) + 4(其它) = 5 条，实际 {len(out)}"


def test_curate_strips_quote_lines():
    out = curate_for_display(
        _mk(['纳指期货: 31,073.05　🔺+0.14% 14:33:55', MS_A]), max_items=8)
    assert len(out) == 1
    assert out[0]["title"] == MS_A


def test_curate_respects_max_items_before_dedup():
    """先按 max_items 截断再去重 —— 与改造前的截断语义一致，不会凭空多给内容"""
    many = [f'第{i}条完全不同的新闻内容{i}号' for i in range(20)]
    out = curate_for_display(_mk(many), max_items=6)
    assert len(out) <= 6


def test_curate_empty_list():
    assert curate_for_display([], max_items=8) == []


def test_curate_all_quote_lines_returns_empty():
    out = curate_for_display(
        _mk(['纳指期货: 31,073.05　🔺+0.14% 14:33:55',
             '标普期货: 7,840.32　🔺+0.11% 14:33:53']), max_items=8)
    assert out == []


def test_curate_does_not_mutate_input():
    src = _mk([MS_A, MS_B, MS_C, *SAME_CARD_ITEMS[:3]])
    before = [x["title"] for x in src]
    curate_for_display(src, max_items=8)
    assert [x["title"] for x in src] == before, "不得原地改动入参（AI 层还要用全量）"


# ═══════════════════════════════════════════════════════════════
# 端到端：接进 briefing._fmt_news
# ═══════════════════════════════════════════════════════════════

def test_fmt_news_renders_deduped(monkeypatch):
    from src import briefing
    monkeypatch.setattr(briefing, "_translate_english_titles", lambda items: None)
    out = briefing._fmt_news(_mk([MS_A, MS_B, MS_C, *SAME_CARD_ITEMS[:3]]), max_items=8)
    lines = [l for l in out.split('\n') if l.strip()]
    assert len(lines) == 4, f"1(微软) + 3(其它) = 4 行，实际 {len(lines)}"
    # 另外两条微软变体不应再出现（MS_A 保留了）
    assert MS_B not in out and MS_C not in out
    assert sum(1 for l in lines if "微软" in l) == 1, \
        "同一事件只能占一行 ← 这是本次改造的核心诉求"


def test_fmt_news_empty_after_curation():
    from src import briefing
    out = briefing._fmt_news(_mk(['纳指期货: 31,073.05　🔺+0.14% 14:33:55']), max_items=8)
    assert out == "（暂无）"


def test_fmt_news_keeps_real_content_untouched():
    """不重复的正常新闻必须原样保留（含源站标注）"""
    from src import briefing
    monkeypatch_ok = ["欧洲央行管委Nagel警告政策利率可能需要进入轻度限制性区间"]
    out = briefing._fmt_news(_mk(monkeypatch_ok), max_items=8)
    assert "欧洲央行管委Nagel" in out
