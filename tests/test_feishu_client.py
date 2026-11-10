"""
单元测试：src.feishu_client 模块测试 (#16)
涵盖带指数退避的重试机制、表 ID 解析、分页读取与异常重试、增删改查 CRUD、
本地硬隔离工厂与快照/决策日志存储双路径。
"""

import os
from json import JSONDecodeError
from unittest.mock import MagicMock, patch
import pytest

from src.feishu_client import (
    FeishuClient,
    _call_with_retry,
    get_feishu_client_or_none,
    read_briefing_snapshot,
    write_briefing_snapshot,
    read_decision_logs,
    write_decision_log,
    TABLE_MAP,
)


# ═══════════════════════════════════════════════════════════════
# 1. 重试与配置检查测试
# ═══════════════════════════════════════════════════════════════

def test_call_with_retry_success():
    mock_fn = MagicMock(return_value="ok")
    assert _call_with_retry(mock_fn, "arg1", kw="val") == "ok"
    assert mock_fn.call_count == 1


def test_call_with_retry_recovers(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    attempts = [0]

    def failing_fn():
        attempts[0] += 1
        if attempts[0] < 2:
            raise JSONDecodeError("Expecting value", "doc", 0)
        return "success"

    res = _call_with_retry(failing_fn, max_retries=3)
    assert res == "success"
    assert attempts[0] == 2


def test_call_with_retry_all_fail(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)

    def always_fail():
        raise JSONDecodeError("Expecting value", "doc", 0)

    with pytest.raises(JSONDecodeError):
        _call_with_retry(always_fail, max_retries=3)


def test_feishu_client_is_configured(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "")
    monkeypatch.setenv("FEISHU_APP_SECRET", "")
    monkeypatch.setenv("FEISHU_BITABLE_TOKEN", "")

    client_empty = FeishuClient(app_id="", app_secret="", bitable_token="")
    assert client_empty.is_configured() is False

    client_configured = FeishuClient(app_id="cli_123", app_secret="sec_123", bitable_token="bas_123")
    assert client_configured.is_configured() is True


def test_feishu_client_table_resolution():
    client = FeishuClient(app_id="test", app_secret="test", bitable_token="test")
    # 内置表映射解析
    assert client.resolve_table_id("底仓表") == TABLE_MAP["底仓表"]
    assert client.resolve_table_id("决策日志表") == TABLE_MAP["决策日志表"]

    # 已经是 ID 则原样返回
    assert client.resolve_table_id("tblCustom123") == "tblCustom123"

    # 动态注册表
    client.register_table("新业务表", "tblNew999")
    assert client.resolve_table_id("新业务表") == "tblNew999"


# ═══════════════════════════════════════════════════════════════
# 2. list_records 原始 HTTP 分页读取测试
# ═══════════════════════════════════════════════════════════════

@patch("requests.post")
@patch("requests.get")
def test_list_records_pagination(mock_get, mock_post):
    client = FeishuClient(app_id="cli_test", app_secret="sec_test", bitable_token="bas_test")

    # 模拟获取 tenant access token
    mock_post_resp = MagicMock()
    mock_post_resp.ok = True
    mock_post_resp.json.return_value = {"tenant_access_token": "token_abc"}
    mock_post.return_value = mock_post_resp

    # 模拟两页分页数据
    page1 = MagicMock()
    page1.status_code = 200
    page1.json.return_value = {
        "code": 0,
        "data": {
            "has_more": True,
            "page_token": "tok_page2",
            "items": [
                {"record_id": "rec_1", "fields": {"标的名称": "基金A"}},
            ],
        },
    }

    page2 = MagicMock()
    page2.status_code = 200
    page2.json.return_value = {
        "code": 0,
        "data": {
            "has_more": False,
            "items": [
                {"record_id": "rec_2", "fields": {"标的名称": "基金B"}},
            ],
        },
    }
    mock_get.side_effect = [page1, page2]

    records = client.list_records("底仓表", page_size=100)
    assert len(records) == 2
    assert records[0]["_record_id"] == "rec_1"
    assert records[0]["标的名称"] == "基金A"
    assert records[1]["_record_id"] == "rec_2"
    assert records[1]["标的名称"] == "基金B"


@patch("requests.post")
def test_list_records_token_failure(mock_post):
    client = FeishuClient(app_id="cli_test", app_secret="sec_test", bitable_token="bas_test")
    mock_post_resp = MagicMock()
    mock_post_resp.ok = False
    mock_post_resp.json.return_value = {}
    mock_post.return_value = mock_post_resp

    records = client.list_records("底仓表")
    assert records == []


# ═══════════════════════════════════════════════════════════════
# 3. CRUD (创建/更新/批量更新/删除) 测试
# ═══════════════════════════════════════════════════════════════

def test_update_record():
    client = FeishuClient(app_id="test", app_secret="test", bitable_token="test")
    mock_resp = MagicMock()
    mock_resp.success.return_value = True
    client._client.bitable.v1.app_table_record.update = MagicMock(return_value=mock_resp)

    ok = client.update_record("底仓表", "rec_001", {"现价": 1.25})
    assert ok is True
    client._client.bitable.v1.app_table_record.update.assert_called_once()


def test_batch_update_records():
    client = FeishuClient(app_id="test", app_secret="test", bitable_token="test")
    mock_resp = MagicMock()
    mock_resp.success.return_value = True
    mock_resp.data.records = [MagicMock(), MagicMock()]
    client._client.bitable.v1.app_table_record.batch_update = MagicMock(return_value=mock_resp)

    # 空更新列表
    assert client.batch_update_records("底仓表", []) == 0

    updates = [
        {"_record_id": "rec_001", "现价": 1.25},
        {"_record_id": "rec_002", "现价": 2.30},
        {"现价": 3.0},  # 无 _record_id，应跳过
    ]
    count = client.batch_update_records("底仓表", updates)
    assert count == 2


def test_delete_record():
    client = FeishuClient(app_id="test", app_secret="test", bitable_token="test")
    mock_resp = MagicMock()
    mock_resp.success.return_value = True
    client._client.bitable.v1.app_table_record.delete = MagicMock(return_value=mock_resp)

    assert client.delete_record("底仓表", "rec_001") is True


def test_create_record():
    client = FeishuClient(app_id="test", app_secret="test", bitable_token="test")
    mock_resp = MagicMock()
    mock_resp.success.return_value = True
    mock_resp.data.record.record_id = "rec_new_999"
    client._client.bitable.v1.app_table_record.create = MagicMock(return_value=mock_resp)

    rid = client.create_record("底仓表", {"标的名称": "新基金"})
    assert rid == "rec_new_999"


# ═══════════════════════════════════════════════════════════════
# 4. 工厂函数与本地硬隔离测试
# ═══════════════════════════════════════════════════════════════

def test_get_feishu_client_or_none(monkeypatch):
    monkeypatch.setattr("src.env.is_production", lambda: False)
    assert get_feishu_client_or_none() is None

    monkeypatch.setattr("src.env.is_production", lambda: True)
    client = get_feishu_client_or_none()
    assert isinstance(client, FeishuClient)


# ═══════════════════════════════════════════════════════════════
# 5. 快照读写双路径测试
# ═══════════════════════════════════════════════════════════════

def test_snapshot_local_fixture_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("src.env.is_production", lambda: False)
    test_fixture = str(tmp_path / "test_snapshot.json")
    monkeypatch.setattr("src.feishu_client._SNAPSHOT_FIXTURE_PATH", test_fixture)

    # 首次读为空
    assert read_briefing_snapshot("morning") is None

    # 写快照
    res = write_briefing_snapshot("morning", {"total_value": 70000}, "sig_123")
    assert res == "fixture"

    # 读取快照
    snap = read_briefing_snapshot("morning")
    assert snap is not None
    assert snap["slot"] == "morning"
    assert snap["signature"] == "sig_123"
    assert snap["payload"]["total_value"] == 70000


def test_decision_log_local_fixture_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("src.env.is_production", lambda: False)
    test_fixture = str(tmp_path / "test_decision_log.json")
    monkeypatch.setattr("src.feishu_client._DECISION_LOG_FIXTURE_PATH", test_fixture)

    assert read_decision_logs() == []

    entry1 = {"日期": "2026-11-07", "时段": "morning", "决策结论": "早盘研判"}
    entry2 = {"日期": "2026-11-07", "时段": "closing", "决策结论": "建议买入"}

    assert write_decision_log(entry1) == "fixture"
    assert write_decision_log(entry2) == "fixture"

    logs = read_decision_logs(limit=10)
    assert len(logs) == 2
    assert logs[0]["决策结论"] == "早盘研判"
    assert logs[1]["决策结论"] == "建议买入"


@patch("requests.post")
@patch("requests.get")
def test_list_records_retries_on_http_502_and_caches_token(mock_get, mock_post, monkeypatch):
    """验证 list_records 在遇到 HTTP 502/429 时会自动重试，且实例级复用 tenant_access_token。"""
    monkeypatch.setattr("time.sleep", lambda s: None)
    client = FeishuClient(app_id="cli_test", app_secret="sec_test", bitable_token="bas_test")

    mock_post_resp = MagicMock()
    mock_post_resp.ok = True
    mock_post_resp.json.return_value = {"tenant_access_token": "tok_cached_123"}
    mock_post.return_value = mock_post_resp

    # 第一次 GET 返回 502 Bad Gateway，第二次 GET 返回 200 OK
    resp_502 = MagicMock()
    resp_502.status_code = 502
    resp_502.text = "Bad Gateway"

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.text = '{"code": 0}'
    resp_200.json.return_value = {
        "code": 0,
        "data": {
            "has_more": False,
            "items": [{"record_id": "rec_ok", "fields": {"标的名称": "纳指ETF"}}],
        },
    }
    mock_get.side_effect = [resp_502, resp_200, resp_200]

    records1 = client.list_records("底仓表")
    assert len(records1) == 1
    assert records1[0]["标的名称"] == "纳指ETF"
    assert mock_get.call_count == 2
    assert mock_post.call_count == 1

    # 同一 client 再次调用 list_records，直接复用缓存 token，不再请求 token 接口
    records2 = client.list_records("底仓表")
    assert len(records2) == 1
    assert mock_post.call_count == 1
