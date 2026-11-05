# -*- coding: utf-8 -*-
"""忙人降噪补丁单元测试（2026-10-31）。

覆盖四项核心改造：
  1. FeishuPusher.send_card 支持表头配色 template（向后兼容空串不发该字段）
  2. 锁屏摘要标题 + 14:30 红绿灯表头（_build_dynamic_header）
  3. 展示层「核心要闻」合并蒸馏（_distill_news_line / _build_digest_news_block），不污染 AI 全量输入
  4. 午休极简卡片与规则 1 仅周六注入接线
"""

from __future__ import annotations

import pytest

from src import briefing
from src.notify import FeishuPusher


# ═══════════════════════════════════════════════════════════════
# 1. FeishuPusher 表头 template
# ═══════════════════════════════════════════════════════════════

class TestFeishuCardTemplate:
    def test_send_card_omits_template_by_default(self, monkeypatch):
        captured = {}

        class _Resp:
            status_code = 200
            text = '{"code":0}'

            @staticmethod
            def json():
                return {"code": 0}

        def fake_post(url, json=None, timeout=30):
            captured["payload"] = json
            return _Resp()

        monkeypatch.setattr("src.notify.requests.post", fake_post)
        pusher = FeishuPusher(webhook_url="https://example.com/hook")
        assert pusher.send_card("测试标题", "正文") is True
        header = captured["payload"]["card"]["header"]
        assert header["title"]["content"] == "测试标题"
        assert "template" not in header

    def test_send_card_includes_template_when_provided(self, monkeypatch):
        captured = {}

        class _Resp:
            status_code = 200
            text = '{"code":0}'

            @staticmethod
            def json():
                return {"code": 0}

        def fake_post(url, json=None, timeout=30):
            captured["payload"] = json
            return _Resp()

        monkeypatch.setattr("src.notify.requests.post", fake_post)
        pusher = FeishuPusher(webhook_url="https://example.com/hook")
        assert pusher.send_card("🟢 14:30 无操作", "正文", template="green") is True
        header = captured["payload"]["card"]["header"]
        assert header["template"] == "green"
        assert header["title"]["content"] == "🟢 14:30 无操作"


# ═══════════════════════════════════════════════════════════════
# 2. 要闻一句话提炼与国内外合并
# ═══════════════════════════════════════════════════════════════

class TestNewsDistillation:
    def test_extracts_bracketed_headline_and_drops_wire_body(self):
        raw = (
            "【微软拟推Copilot最高五折企业折扣，同步上线AI“超级应用”】"
            "金十数据9月23日讯，据The Information报道，知情人士透露，微软高管本周告诉销售人员……"
        )
        got = briefing._distill_news_line(raw)
        assert got == "微软拟推Copilot最高五折企业折扣，同步上线AI“超级应用”"
        assert "金十数据" not in got

    def test_strips_translation_prefix_and_english_tail(self):
        raw = (
            "[译] 美联储Goolsbee称，多重通胀压力下，美联储更接近需要“采取行动”。"
            "（Fed’s Goolsbee says officials getting closer to needing to act）"
        )
        got = briefing._distill_news_line(raw)
        assert not got.startswith("[译]")
        assert "Fed" not in got
        assert "美联储Goolsbee" in got

    def test_strips_wire_prefix_and_keeps_first_sentence(self):
        raw = "金十数据10月31日讯，美国核心PCE同比上涨2.7%，符合市场预期。分析人士认为年内降息节奏不变。"
        got = briefing._distill_news_line(raw)
        assert got == "美国核心PCE同比上涨2.7%，符合市场预期"

    def test_digest_merges_domestic_and_global_without_mutating_input(self, monkeypatch):
        monkeypatch.setattr(briefing, "_translate_english_titles", lambda items: None)
        cn = [
            {"title": "【韩国本月前20天出口创历史新高 芯片出口飙升近260%】金十数据9月21日讯，韩国海关数据显示", "source": "金十数据"},
            {"title": "【银河证券：产业韧性凸显 美联储加息不改AI长期逻辑】金十数据9月21日讯，银河证券研报称", "source": "金十数据"},
            {"title": "境外机构看好中国资产，人民币债券全球配置价值提升", "source": "华尔街见闻"},
        ]
        cn_snapshot = [dict(x) for x in cn]
        global_brief = (
            "🌐 **国际快讯**\n"
            "· 台积电上调全年AI芯片营收增长指引。\n"
            "· 美债10年期收益率回落至4.15%。"
        )
        out = briefing._build_digest_news_block(cn, global_brief, max_bullets=4)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert len(lines) == 4
        assert any("韩国本月前20天出口" in ln for ln in lines)
        assert any("台积电" in ln for ln in lines)
        assert cn == cn_snapshot, "展示层精简不得原地修改入参（AI 层还需读取全量）"

    def test_digest_empty_returns_placeholder(self):
        assert briefing._build_digest_news_block([], "", max_bullets=3) == "（暂无）"


# ═══════════════════════════════════════════════════════════════
# 3. 锁屏摘要标题 + 14:30 红绿灯表头
# ═══════════════════════════════════════════════════════════════

class TestDynamicHeader:
    def test_morning_header_extracts_us_indices_and_pnl(self):
        card = (
            "☀️ **2026-10-31 早间简报**　|　08:30\n\n"
            "**📊 全球市场**\n"
            "· 标普500: 5,810.25　🔺+0.52%\n"
            "· 纳斯达克: 18,420.10　🔻-0.38%\n\n"
            "**📰 核心要闻**\n"
            "· 某文章提到纳斯达克去年大涨+35%\n\n"
            "**💰 持仓速览**\n"
            "💵 昨日盈亏（实际）：🔺 ¥+65\n"
        )
        title, tpl = briefing._build_dynamic_header("morning", "☀️ 早间简报", card)
        assert title == "☀️ 早报｜纳指 -0.4% 标普 +0.5%｜昨 ¥+65"
        assert tpl == "blue"

    def test_midday_header_extracts_cn_hk_and_est_pnl(self):
        card = (
            "🌤️ **2026-10-31 午间快讯**　|　12:00\n"
            "**🌏 亚太午盘**\n"
            "· A股：上证 🔻-0.36% ｜ 深证 🔻-0.58% ｜ 创业板 🔻-2.12%\n"
            "· 港股：恒指 🔻-0.72% ｜ 恒科 🔻-1.86%\n"
            "**💰 持仓速览**\n"
            "💵 今日盈亏（盘中估算）：🔻 ¥-152\n"
        )
        title, tpl = briefing._build_dynamic_header("midday", "🌤️ 午间快讯", card)
        assert title == "🌤️ 午休速览｜沪-0.4% 创-2.1% 恒-0.7% 科-1.9%｜估 ¥-152"
        assert tpl == "wathet"

    def test_midday_header_hk_closed_handles_partial_indices(self):
        card = (
            "🌤️ **2026-10-31 午间快讯**　|　12:00\n"
            "**🌏 亚太午盘**\n"
            "· A股：上证 🔺+0.32% ｜ 深证 🔻-0.10% ｜ 创业板 🔺+1.55%\n"
            "· 港股：今日休市（无当日行情）\n"
            "**💰 持仓速览**\n"
            "💵 今日盈亏（盘中估算）：🔺 ¥+88\n"
        )
        title, tpl = briefing._build_dynamic_header("midday", "🌤️ 午间快讯", card)
        assert title == "🌤️ 午休速览｜沪+0.3% 创+1.6%｜估 ¥+88"
        assert tpl == "wathet"

    def test_closing_green_when_idle(self):
        card = (
            "⚡ **2026-10-31 收盘前指令**　|　14:30\n"
            "**💰 持仓速览**\n"
            "💵 今日盈亏（盘中估算）：🔻 ¥-120\n"
        )
        title, tpl = briefing._build_dynamic_header(
            "closing", "⚡ 收盘前指令", card, closing_signal=""
        )
        assert title == "🟢 14:30 无操作｜盘中估 ¥-120"
        assert tpl == "green"

    def test_closing_orange_on_rules_alert(self):
        card = (
            "⚡ **2026-10-31 收盘前指令**　|　14:30\n"
            "**💰 持仓速览**\n"
            "💵 今日盈亏（盘中估算）：🔺 ¥+50\n"
        )
        title, tpl = briefing._build_dynamic_header(
            "closing",
            "⚡ 收盘前指令",
            card,
            rules_alert="🎯 **决策参考** 👉 某基金回本在即",
            closing_signal="",
        )
        assert title == "🟠 14:30 有决策提醒｜盘中估 ¥+50"
        assert tpl == "orange"

    def test_closing_orange_on_right_side_signal_and_shows_settle_count(self):
        card = (
            "⚡ **2026-10-31 收盘前指令**　|　14:30\n"
            "**💰 持仓速览**\n"
            "💵 今日盈亏（盘中估算）：🔺 ¥+80\n"
        )
        receipt = "📌 **本时段自动入账 2 笔**\n· 博时红利低波 +92.02 份"
        title, tpl = briefing._build_dynamic_header(
            "closing",
            "⚡ 收盘前指令",
            card,
            receipt=receipt,
            closing_signal="美股",
        )
        assert title == "🟠 14:30 出现企稳信号｜盘中估 ¥+80｜入账2笔"
        assert tpl == "orange"

    def test_evening_header_extracts_confirmed_pnl_and_nq_futures(self):
        card = (
            "🌆 **2026-10-31 夜盘前瞻**　|　21:00\n"
            "**💰 持仓速览**\n"
            "💵 今日盈亏（净值确认）：🔻 ¥-284\n\n"
            "📡 **美股期货实时**\n"
            "· 纳指期货: 20,120.50　🔻-0.62% 20:59:12\n"
        )
        title, tpl = briefing._build_dynamic_header("evening", "🌆 夜盘前瞻", card)
        assert title == "🌆 夜盘｜今日确认 ¥-284｜纳指期 -0.6%"
        assert tpl == "indigo"

    def test_offline_empty_card_falls_back_gracefully(self):
        title, tpl = briefing._build_dynamic_header("morning", "☀️ 早间简报", "☀️ **早间简报**")
        assert title == "☀️ 早间简报"
        assert tpl == "blue"


# ═══════════════════════════════════════════════════════════════
# 4. 午间瘦身与 main() 规则 1 周六门控接线
# ═══════════════════════════════════════════════════════════════

def test_midday_drops_afternoon_boilerplate(monkeypatch):
    monkeypatch.setattr(briefing, "_build_asia_pacific_market", lambda: "**🌏 亚太午盘**\n· A股：上证 🔺+0.30%")
    monkeypatch.setattr(briefing, "fetch_all_news", lambda **kw: [])
    monkeypatch.setattr(briefing, "load_portfolio", lambda: [])
    monkeypatch.setattr(briefing, "_filter_by_keywords", lambda a, p, top_n=6: [
        {"title": "【消息一：央行开展逆回购操作】金十数据讯……"},
        {"title": "【消息二：半导体板块早盘走强】金十数据讯……"},
        {"title": "【消息三：港股科技股反弹】金十数据讯……"},
        {"title": "【消息四：不应超过三条上限】金十数据讯……"},
    ])
    monkeypatch.setattr(briefing, "_news_link_block", lambda a: "")
    monkeypatch.setattr(briefing, "_build_sector_parts", lambda **kw: ("", ""))
    monkeypatch.setattr(briefing, "_portfolio_value_summary", lambda label="auto": "**💰 持仓速览**\n💵 今日盈亏（盘中估算）：🔺 ¥+35")
    monkeypatch.setattr(briefing, "_ai_insight", lambda *a, **kw: "盘面平稳，持仓不动。")

    card = briefing._build_midday()
    assert "💡 下午关注" not in card
    assert "**📰 核心要闻**" in card
    assert "消息四" not in card  # 午休严控最多 3 条


@pytest.mark.parametrize("slot,expected_flag", [
    ("morning", False),
    ("midday", False),
    ("closing", False),
    ("evening", False),
    ("sat_morning", True),
    ("sun_evening", False),
])
def test_main_wires_theme_gap_only_on_saturday(monkeypatch, slot, expected_flag):
    captured = {}

    monkeypatch.setattr("sys.argv", ["briefing", slot])
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setattr(briefing, "is_cn_market_open", lambda: True)
    monkeypatch.setattr("src.price_updater.update_all_prices", lambda dry_run=False: None)
    monkeypatch.setitem(briefing.BRIEFINGS, slot, ("测试标题", lambda: "标题行\n正文"))
    monkeypatch.setattr(briefing, "_build_settlement_receipt", lambda: "")
    monkeypatch.setattr("src.feishu_client.get_feishu_client_or_none", lambda: object())
    monkeypatch.setattr("src.reconcile.build_reconcile_alert", lambda c: "")

    def fake_rules_alert(client, *, include_theme_gap=True):
        captured["include_theme_gap"] = include_theme_gap
        return ""

    monkeypatch.setattr("src.rules_engine.build_rules_alert", fake_rules_alert)
    monkeypatch.setattr(briefing, "_push", lambda title, content, template="": True)

    briefing.main()
    assert captured["include_theme_gap"] is expected_flag


# ═══════════════════════════════════════════════════════════════
# 5. compact / full 切换开关（环境变量 + CLI 覆盖 + 全时段离线双模式）
# ═══════════════════════════════════════════════════════════════

class TestBriefingStyleToggle:
    def test_default_style_is_compact(self, monkeypatch):
        monkeypatch.delenv("BRIEFING_STYLE", raising=False)
        monkeypatch.setattr(briefing, "_STYLE_OVERRIDE", None)
        assert briefing.get_briefing_style() == "compact"
        assert briefing._is_compact_style() is True

    def test_env_full_restores_full_midday_layout(self, monkeypatch):
        monkeypatch.setenv("BRIEFING_STYLE", "full")
        monkeypatch.setattr(briefing, "_STYLE_OVERRIDE", None)
        monkeypatch.setattr(briefing, "_build_asia_pacific_market", lambda: "**🌏 亚太午盘**\n· 上证指数: 3,900.00　🔺+0.30%")
        monkeypatch.setattr(briefing, "fetch_all_news", lambda **kw: [])
        monkeypatch.setattr(briefing, "load_portfolio", lambda: [])
        monkeypatch.setattr(briefing, "_filter_by_keywords", lambda a, p, top_n=6: [
            {"title": "【消息一】金十数据10月31日讯，完整版保留导语详情内容不压缩", "source": "金十数据"},
            {"title": "【消息四：完整版最多六条不被三条截断】金十数据讯", "source": "金十数据"},
        ])
        monkeypatch.setattr(briefing, "_news_link_block", lambda a: "")
        monkeypatch.setattr(briefing, "_build_sector_parts", lambda **kw: ("", ""))
        monkeypatch.setattr(briefing, "_portfolio_value_summary", lambda label="auto": "**💰 持仓速览**")
        monkeypatch.setattr(briefing, "_ai_insight", lambda *a, **kw: "完整午间快评。")

        card = briefing._build_midday()
        assert "**📰 上午要闻**" in card
        assert "完整版保留导语详情内容不压缩" in card
        assert "💡 下午关注" in card

    def test_cli_full_flag_overrides_default_and_enables_weekday_rule1(self, monkeypatch):
        captured = {}
        monkeypatch.delenv("BRIEFING_STYLE", raising=False)
        monkeypatch.setattr("sys.argv", ["briefing", "morning", "--full"])
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)
        monkeypatch.setitem(briefing.BRIEFINGS, "morning", ("☀️ 早间简报", lambda: "标题\n正文"))
        monkeypatch.setattr("src.price_updater.update_all_prices", lambda dry_run=False: None)
        monkeypatch.setattr(briefing, "_build_settlement_receipt", lambda: "")
        monkeypatch.setattr("src.feishu_client.get_feishu_client_or_none", lambda: object())
        monkeypatch.setattr("src.reconcile.build_reconcile_alert", lambda c: "")

        def fake_rules_alert(client, *, include_theme_gap=True):
            captured["style_during_run"] = briefing.get_briefing_style()
            captured["include_theme_gap"] = include_theme_gap
            return ""

        monkeypatch.setattr("src.rules_engine.build_rules_alert", fake_rules_alert)
        monkeypatch.setattr(briefing, "_push", lambda title, content, template="": True)

        briefing.main()
        assert captured["style_during_run"] == "full"
        assert captured["include_theme_gap"] is True

    def test_cli_dry_run_skips_push(self, monkeypatch):
        called = {"pushed": False}
        monkeypatch.delenv("BRIEFING_STYLE", raising=False)
        monkeypatch.setattr("sys.argv", ["briefing", "morning", "--dry-run"])
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)
        monkeypatch.setitem(briefing.BRIEFINGS, "morning", ("☀️ 早间简报", lambda: "标题\n正文"))
        monkeypatch.setattr("src.price_updater.update_all_prices", lambda dry_run=False: None)
        monkeypatch.setattr(briefing, "_build_settlement_receipt", lambda: "")
        monkeypatch.setattr("src.feishu_client.get_feishu_client_or_none", lambda: object())
        monkeypatch.setattr("src.reconcile.build_reconcile_alert", lambda c: "")
        monkeypatch.setattr("src.rules_engine.build_rules_alert", lambda *a, **kw: "")
        monkeypatch.setattr(briefing, "_push", lambda *a, **kw: called.update(pushed=True))

        briefing.main()
        assert called["pushed"] is False


# ═══════════════════════════════════════════════════════════════
# 6. 美股夏令时 / 冬令时双向换季防忘提醒
# ═══════════════════════════════════════════════════════════════

class TestUSDSTReminder:
    def test_winter_matched_2200_is_silent(self):
        from zoneinfo import ZoneInfo
        from datetime import datetime
        # 2026-11-05 美股冬令时，22:00 正常运行 → 不打扰
        now = datetime(2026, 11, 5, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        msg = briefing._get_us_dst_reminder(now)
        assert msg == ""

    def test_winter_forgot_at_2100_triggers_reminder(self):
        from zoneinfo import ZoneInfo
        from datetime import datetime
        # 2026-11-05 美股冬令时，用户依然留在夏令时的 21:00 触发 → 提醒调至 22:00
        now = datetime(2026, 11, 5, 21, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        msg = briefing._get_us_dst_reminder(now)
        assert "美股已切换为冬令时（22:30 开盘）" in msg
        assert "22:00" in msg

    def test_summer_matched_2100_is_silent(self):
        from zoneinfo import ZoneInfo
        from datetime import datetime
        # 2027-07-05 美股夏令时，21:00 正常运行 → 不打扰
        now = datetime(2027, 7, 5, 21, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        msg = briefing._get_us_dst_reminder(now)
        assert msg == ""

    def test_summer_forgot_at_2200_triggers_reminder(self):
        from zoneinfo import ZoneInfo
        from datetime import datetime
        # 2027-07-05 美股夏令时，用户依然留在冬令时的 22:00 触发 → 提醒调回 21:00
        now = datetime(2027, 7, 5, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        msg = briefing._get_us_dst_reminder(now)
        assert "美股已切换为夏令时（21:30 开盘）" in msg
        assert "21:00" in msg




