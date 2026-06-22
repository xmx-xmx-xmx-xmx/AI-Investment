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
