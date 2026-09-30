"""#7 bot 加固测试：事件去重 TTL 化 + Token 校验 + /version 暴露校验状态。

直接 import bot_server 模块（不启动服务器）；webhook 层用 TestClient。
"""

from __future__ import annotations

import bot_server
from fastapi.testclient import TestClient

client = TestClient(bot_server.app)


# ── _seen_event：TTL 去重 ──

def test_seen_event_first_false_second_true():
    bot_server._processed_events.clear()
    assert bot_server._seen_event("evt-1") is False
    assert bot_server._seen_event("evt-1") is True


def test_seen_event_expired_entry_reallowed():
    bot_server._processed_events.clear()
    bot_server._processed_events["evt-old"] = 0.0  # epoch → 早已超过 24h TTL
    # 未触发清理阈值时仍视为已见（TTL 清理是惰性的，只在超量时执行）
    assert bot_server._seen_event("evt-old") is True


def test_seen_event_cleanup_on_overflow_never_full_clear():
    bot_server._processed_events.clear()
    # 灌满阈值：一半是远古事件，一半是当前事件
    for i in range(bot_server._EVENT_DEDUP_MAX // 2):
        bot_server._processed_events[f"old-{i}"] = 0.0
    for i in range(bot_server._EVENT_DEDUP_MAX // 2):
        bot_server._processed_events[f"new-{i}"] = 1e18
    bot_server._seen_event("trigger-cleanup")
    ids = set(bot_server._processed_events)
    assert "trigger-cleanup" in ids
    # 全清回归锁：清完后远古事件不能还在占位（应被过期清理掉）
    assert "old-0" not in ids
    # 新事件不能被误删（绝不全清）
    assert "new-0" in ids


# ── _verify_callback_token ──

def test_verify_token_ok(monkeypatch):
    monkeypatch.setattr(bot_server, "FEISHU_VERIFY_TOKEN", "secret")
    ok, why = bot_server._verify_callback_token({"token": "secret"})
    assert ok and why == "token ok"


def test_verify_token_mismatch_denied(monkeypatch):
    monkeypatch.setattr(bot_server, "FEISHU_VERIFY_TOKEN", "secret")
    ok, why = bot_server._verify_callback_token({"token": "wrong"})
    assert not ok and "mismatch" in why


def test_verify_token_unconfigured_local_allows(monkeypatch):
    monkeypatch.setattr(bot_server, "FEISHU_VERIFY_TOKEN", "")
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    ok, _ = bot_server._verify_callback_token({})
    assert ok


def test_verify_token_unconfigured_production_allows_but_warns(monkeypatch, caplog):
    # 生产未配置：放行（防 bot 静默失聪）但必须打 ERROR —— 行为契约
    monkeypatch.setattr(bot_server, "FEISHU_VERIFY_TOKEN", "")
    monkeypatch.setenv("RENDER", "true")
    with caplog.at_level("ERROR", logger="bot_server"):
        ok, why = bot_server._verify_callback_token({})
    assert ok
    assert "未配置" in why
    assert any("FEISHU_VERIFY_TOKEN" in r.message for r in caplog.records)


# ── webhook 集成 ──

def _v2_event(token="tok", event_id="eid-1", event_type="im.message.receive_v1"):
    return {
        "header": {"token": token, "event_id": event_id, "event_type": event_type},
        "event": {
            "message": {
                "message_id": "om_x",
                "chat_type": "group",
                "chat_id": "oc_x",
                "content": "{\"text\":\"巡航\"}",
            }
        },
    }


def test_webhook_rejects_bad_token(monkeypatch):
    monkeypatch.setattr(bot_server, "FEISHU_VERIFY_TOKEN", "secret")
    r = client.post("/feishu/webhook", json=_v2_event(token="wrong"))
    assert r.status_code == 403


def test_webhook_duplicate_event_intercepted(monkeypatch):
    monkeypatch.setattr(bot_server, "FEISHU_VERIFY_TOKEN", "")
    # 放行的事件会起后台线程执行回复 —— 桩掉 token 获取，保证测试零真实网络
    monkeypatch.setattr(bot_server, "_get_tenant_access_token", lambda: "")
    bot_server._processed_events.clear()
    r1 = client.post("/feishu/webhook", json=_v2_event(event_id="dup-1"))
    assert r1.status_code == 200
    r2 = client.post("/feishu/webhook", json=_v2_event(event_id="dup-1"))
    assert r2.status_code == 200
    assert "duplicate" in r2.json()["msg"]


def test_version_exposes_verify_token_state(monkeypatch):
    monkeypatch.setattr(bot_server, "FEISHU_VERIFY_TOKEN", "secret")
    r = client.get("/version")
    assert r.json()["verify_token_enabled"] is True
    monkeypatch.setattr(bot_server, "FEISHU_VERIFY_TOKEN", "")
    assert client.get("/version").json()["verify_token_enabled"] is False


def test_mvp_reply_removed():
    """死代码回归锁：MVP 固定回复已删，不许再出现。"""
    assert not hasattr(bot_server, "MVP_REPLY")
