"""
tests/test_news_mapper.py —— 规则 6：资讯命中重仓主题（src/news_mapper.py）
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.news_mapper import (
    THEMES,
    build_news_links_block,
    map_news_to_holdings,
    render_news_links,
)


# ═══════════════════════════════════════════════════════════════
# fixtures —— 用飞书原始字段形态（资产大类是数组、其余标量）
# ═══════════════════════════════════════════════════════════════

def h(name, idx, cls, mv=1000.0, price=None, shares=None):
    rec = {
        "标的名称": name,
        "底层指数": idx,
        "资产大类": [cls],
        "市值": mv,
    }
    if price is not None:
        rec["现价"] = price
        rec["持仓份额"] = shares
    return rec


def n(title, snippet=""):
    return {"title": title, "snippet": snippet, "source": "测试"}


STANDARD = [
    h("景顺长城纳斯达克科技ETF联接A", "纳斯达克科技市值加权指数", "美股资产", mv=3000.0),
    h("博时标普500ETF联接A", "标普500", "美股资产", mv=2000.0),
    h("广发纳斯达克100ETF联接A", "纳斯达克100", "美股资产", mv=1000.0),
    h("华夏港股通互联网指数C", "中证港股通互联网指数", "港股资产", mv=2000.0),
    h("博时中证红利低波动100ETF联接A", "中证红利低波动100指数", "A股资产", mv=1000.0),
    h("建信上海金ETF联接C", "上海金（Au99.99）", "避险商品", mv=1000.0),
    h("某主动短债C", "无（主动短债）", "固收资产", mv=5000.0),
    h("长城全球新能源车C", "无（主动QDII·全球新能源车）", "美股资产", mv=1000.0),
]
# total_mv = 16000


class TestNewsMatching:
    def test_fed_news_hits_us_holdings(self):
        news = [n("美联储暗示12月降息 25个基点"), n("某无关新闻")]
        results = map_news_to_holdings(news, STANDARD)
        assert len(results) == 1
        r = results[0]
        assert r["name"] == "美联储/利率"
        assert r["hits"] == 1
        # 三只美股：纳科+标普+纳指100（新能源车指数名不含"纳斯达克/标普"）
        assert len(r["holding_names"]) == 3
        assert abs(r["mv"] - 6000.0) < 1e-6
        assert abs(r["mv_pct"] - 6000.0 / 16000.0) < 1e-6

    def test_ai_word_boundary(self):
        # 独立 "AI" / 中文紧贴命中；OPENAI、英文词内不命中
        assert map_news_to_holdings([n("AI芯片出口新规")], STANDARD)
        assert map_news_to_holdings([n("英伟达发布新算力平台")], STANDARD)
        assert not map_news_to_holdings([n("某某OPENAI公司更名")], STANDARD)

    def test_ai_regex_openai_still_hits(self):
        # "OpenAI" 是独立关键词（子串），应命中
        results = map_news_to_holdings([n("OpenAI 发布新模型")], STANDARD)
        assert any(r["name"] == "AI/半导体" for r in results)

    def test_bond_theme_gated_by_asset_class(self):
        news = [n("央行开展逆回购操作 净投放2000亿"), n("十年期国债收益率下行")]
        results = map_news_to_holdings(news, STANDARD)
        bond = [r for r in results if r["name"] == "债市"]
        assert len(bond) == 1
        assert bond[0]["holding_names"] == ["某主动短债C"]
        assert abs(bond[0]["mv"] - 5000.0) < 1e-6

    def test_gold_theme(self):
        results = map_news_to_holdings([n("国际金价创历史新高")], STANDARD)
        gold = [r for r in results if r["name"] == "黄金"]
        assert len(gold) == 1
        assert gold[0]["holding_names"] == ["建信上海金ETF联接C"]

    def test_ev_theme_matches_by_name(self):
        news = [n("特斯拉Q3交付量超预期 比亚迪出海提速")]
        results = map_news_to_holdings(news, STANDARD)
        ev = [r for r in results if r["name"] == "新能源车"]
        assert len(ev) == 1
        assert ev[0]["holding_names"] == ["长城全球新能源车C"]

    def test_snippet_counts(self):
        # 标题不命中、摘要命中也算
        news = [n("市场晚间速览", snippet="黄金价格盘中走高")]
        results = map_news_to_holdings(news, STANDARD)
        assert any(r["name"] == "黄金" for r in results)

    def test_no_news_no_match(self):
        assert map_news_to_holdings([], STANDARD) == []
        assert map_news_to_holdings([n("美联储降息")], []) == []

    def test_irrelevant_news_no_match(self):
        news = [n("某地举办马拉松比赛"), n("某明星演唱会官宣")]
        assert map_news_to_holdings(news, STANDARD) == []


class TestAggregation:
    def test_capped_at_three_sorted_by_hits(self):
        news = (
            [n(f"美联储官员讲话{i}") for i in range(3)]
            + [n(f"黄金ETF流入{i}") for i in range(2)]
            + [n("特斯拉涨价")]
            + [n("无关新闻") for _ in range(5)]
        )
        results = map_news_to_holdings(news, STANDARD)
        assert len(results) == 3
        assert results[0]["name"] == "美联储/利率"
        assert results[0]["hits"] == 3
        assert results[1]["name"] == "黄金"

    def test_one_theme_one_line(self):
        # 同主题 5 条命中 → 仍只有 1 行（主题级聚合防刷屏）
        news = [n(f"美联储相关{i}") for i in range(5)]
        text = render_news_links(map_news_to_holdings(news, STANDARD))
        assert text.count("\n") == 1
        assert "×5" in text


class TestRender:
    def test_format(self):
        results = map_news_to_holdings([n("美联储降息")], STANDARD)
        text = render_news_links(results)
        assert text.startswith("📡 **资讯·持仓关联**")
        assert "波及持仓 3 只" in text
        assert "占组合 37.5%" in text  # 6000/16000

    def test_empty_results_empty_string(self):
        assert render_news_links([]) == ""

    def test_mv_fallback_price_times_shares(self):
        rec = h("某新标的", "纳斯达克100", "美股资产", mv=0, price=2.0, shares=500.0)
        news = [n("美联储按兵不动")]
        results = map_news_to_holdings(news, [rec])
        assert abs(results[0]["mv"] - 1000.0) < 1e-6


class TestFailSilent:
    def test_no_client_returns_empty(self):
        with patch("src.feishu_client.get_feishu_client_or_none", return_value=None):
            assert build_news_links_block([n("美联储降息")]) == ""

    def test_exception_returns_empty(self):
        with patch("src.feishu_client.get_feishu_client_or_none",
                   side_effect=RuntimeError("boom")):
            assert build_news_links_block([n("美联储降息")]) == ""

    def test_no_news_skips_client(self):
        # 空新闻不该触碰客户端（本地模式零 API）
        assert build_news_links_block([]) == ""

    def test_themes_have_required_keys(self):
        for t in THEMES:
            assert t["name"] and isinstance(t["news_kw"], list)
            assert t["asset_class"] is None or t["asset_class"]
            if not t["asset_class"]:
                assert t["holding_kw"], f"{t['name']} 既无大类约束又无持仓关键词"
