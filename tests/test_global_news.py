# -*- coding: utf-8 -*-
"""global_news.py 单元测试 —— D3 国际 RSS 信息流"""

from __future__ import annotations

import pytest


# ═══════════════════════════════════════════════════════════════
# RSS 抓取 + 预筛
# ═══════════════════════════════════════════════════════════════

class TestFetchRssFeeds:
    """fetch_rss_feeds() RSS 拉取 + 关键词预筛"""

    def test_all_feeds_fail_returns_empty(self, monkeypatch):
        """3 条 RSS 全部失败 → 返回空列表"""
        import feedparser

        def mock_parse_empty(url):
            return type("obj", (), {"entries": [], "status": 500})()

        monkeypatch.setattr(feedparser, "parse", mock_parse_empty)

        from src.global_news import fetch_rss_feeds
        result = fetch_rss_feeds()
        assert result == []

    def test_feeds_return_filtered_articles(self, monkeypatch):
        """RSS 返回混合内容 → 预筛保留财经相关"""
        import feedparser

        class FakeEntry:
            def __init__(self, title, link, published=""):
                self.title = title
                self.link = link
                self.published = published

        def mock_parse(url):
            entries = [
                FakeEntry("Fed signals rate cut delay amid inflation concerns",
                          "http://reuters.com/1", "2026-06-22T10:00:00Z"),
                FakeEntry("Local football team wins championship",
                          "http://sports.com/1", "2026-06-22T09:00:00Z"),
                FakeEntry("TSMC reports record Q2 revenue on AI chip boom",
                          "http://yahoo.com/1", "2026-06-22T08:00:00Z"),
                FakeEntry("Celebrity wedding draws massive crowds",
                          "http://entertainment.com/1", "2026-06-22T07:00:00Z"),
            ]
            return type("obj", (), {"entries": entries, "status": 200})()

        monkeypatch.setattr("feedparser.parse", mock_parse)

        from src.global_news import fetch_rss_feeds
        result = fetch_rss_feeds()
        # 应该只保留财经相关的 2 条
        assert len(result) == 2
        titles = [r["title"] for r in result]
        assert any("Fed" in t for t in titles)
        assert any("TSMC" in t for t in titles)
        # 体育和娱乐被去掉
        assert not any("football" in t.lower() for t in titles)
        assert not any("wedding" in t.lower() for t in titles)

    def test_dedup_by_title_prefix(self, monkeypatch):
        """相同标题前缀 → 去重只保留首次出现的"""
        import feedparser

        class FakeEntry:
            def __init__(self, title, link, published="", source="Reuters"):
                self.title = title
                self.link = link
                self.published = published
                self.source = source

        entries1 = [FakeEntry("Fed signals rate cut delay amid inflation concerns",
                             "http://a.com/1", "2026-06-22T10:00:00Z")]
        entries2 = [FakeEntry("Fed signals rate cut delay amid inflation concerns - Reuters",
                             "http://b.com/1", "2026-06-22T10:05:00Z")]
        entries3 = [FakeEntry("Oil prices drop on Iran deal hopes",
                             "http://c.com/1", "2026-06-22T09:00:00Z")]

        call_count = [0]

        def mock_parse(url):
            call_count[0] += 1
            if call_count[0] == 1:
                return type("obj", (), {"entries": entries1, "status": 200})()
            elif call_count[0] == 2:
                return type("obj", (), {"entries": entries2, "status": 200})()
            else:
                return type("obj", (), {"entries": entries3, "status": 200})()

        monkeypatch.setattr("feedparser.parse", mock_parse)

        from src.global_news import fetch_rss_feeds
        result = fetch_rss_feeds()
        # Fed 只在第一次出现时保留，第二次被去重
        assert len(result) == 2
        fed_articles = [r for r in result if "Fed" in r["title"]]
        assert len(fed_articles) == 1


# ═══════════════════════════════════════════════════════════════
# LLM 匹配 + 翻译 + 去重
# ═══════════════════════════════════════════════════════════════

class TestMatchAndTranslate:
    """match_and_translate() LLM 匹配翻译去重"""

    def test_empty_articles_returns_empty(self, monkeypatch):
        """无预筛文章 → 空列表，不调 LLM"""
        from src.global_news import match_and_translate
        result = match_and_translate([], [], [], [])
        assert result == []

    def test_no_holdings_or_radar(self, monkeypatch):
        """无持仓无雷达 → 空列表（没有匹配对象）"""
        from src.global_news import match_and_translate
        articles = [{"title": "Some stock news", "link": "", "source": "Reuters"}]
        result = match_and_translate(articles, [], [], [])
        assert result == []

    def test_llm_returns_valid_result(self, monkeypatch):
        """LLM 正常返回 → 解析为结构化列表"""
        articles = [
            {"title": "TSMC reports record Q2 revenue on AI chip boom",
             "link": "http://reuters.com/1", "source": "Reuters"},
            {"title": "Fed signals rate cut delay",
             "link": "http://reuters.com/2", "source": "Reuters"},
            {"title": "Oil prices drop on Iran deal hopes",
             "link": "http://reuters.com/3", "source": "Reuters"},
        ]
        holdings = [{"name": "纳指100ETF", "code": "019442", "asset_class": "美股资产"}]
        radar_items = [{"name": "国投瑞银新能源", "code": "007690"}]
        cn_titles = [
            "美联储官员暗示降息时间表可能推迟",
            "伊朗核谈判进展中，油价回落至68美元/桶",
        ]

        # Mock LLM 返回 JSON
        mock_response = """[
  {"title_idx": 0, "match_target": "半导体行业(雷达)", "cn_summary": "台积电Q2营收创新高，AI芯片需求持续强劲", "skip": false, "skip_reason": ""},
  {"title_idx": 1, "match_target": "美股资产", "cn_summary": "", "skip": true, "skip_reason": "中文快讯#1已覆盖同一事件"},
  {"title_idx": 2, "match_target": "避险商品", "cn_summary": "伊朗核谈判取得进展，油价走低至68美元", "skip": false, "skip_reason": ""}
]"""

        class FakeResp:
            pass

        fake_choice = FakeResp()
        fake_choice.message = FakeResp()
        fake_choice.message.content = mock_response

        fake_client = FakeResp()
        fake_client.chat = FakeResp()
        fake_client.chat.completions = FakeResp()
        fake_client.chat.completions.create = lambda model, max_tokens, temperature, messages: type(
            "obj", (), {"choices": [fake_choice]}
        )()

        monkeypatch.setattr("src.llm.get_llm_client", lambda: fake_client)
        monkeypatch.setattr("src.llm.get_llm_model", lambda: "test-model")

        from src.global_news import match_and_translate
        result = match_and_translate(articles, holdings, radar_items, cn_titles)

        # 应该保留 skip=false 的 2 条
        assert len(result) == 2
        assert result[0]["cn_summary"] != ""
        assert result[0]["title"] == "TSMC reports record Q2 revenue on AI chip boom"
        assert result[1]["title"] == "Oil prices drop on Iran deal hopes"

    def test_llm_unavailable_returns_empty(self, monkeypatch):
        """LLM 不可用 → 返回空列表"""
        monkeypatch.setattr("src.llm.get_llm_client", lambda: None)

        from src.global_news import match_and_translate
        articles = [{"title": "Some stock news", "link": "", "source": "Reuters"}]
        result = match_and_translate(articles, [{"name": "test"}], [], [])
        assert result == []

    def test_llm_returns_malformed_json(self, monkeypatch):
        """LLM 返回非标准 JSON → 优雅降级，返回空列表"""
        class FakeResp:
            pass

        fake_choice = FakeResp()
        fake_choice.message = FakeResp()
        fake_choice.message.content = "some garbage not json"

        fake_client = FakeResp()
        fake_client.chat = FakeResp()
        fake_client.chat.completions = FakeResp()
        fake_client.chat.completions.create = lambda model, max_tokens, temperature, messages: type(
            "obj", (), {"choices": [fake_choice]}
        )()

        monkeypatch.setattr("src.llm.get_llm_client", lambda: fake_client)
        monkeypatch.setattr("src.llm.get_llm_model", lambda: "test-model")

        from src.global_news import match_and_translate
        articles = [{"title": "Some news", "link": "", "source": "Reuters"}]
        result = match_and_translate(articles, [{"name": "test"}], [], [])
        assert result == []
