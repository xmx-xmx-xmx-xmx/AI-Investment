# -*- coding: utf-8 -*-
"""
飞书多维表格客户端 —— 轻量 lark-oapi SDK 封装。

职责：
- 提供最简单的「读表」「写表」接口
- 自动处理 tenant_access_token
- advisor / market_brief / auto_bill_parser 都通过此模块访问飞书
- 本地和 GitHub Actions 通用（都用 SDK，不用 CLI）

用法：
    from src.feishu_client import FeishuClient

    client = FeishuClient()
    records = client.list_records("底仓表")
    client.update_record("底仓表", "rec_xxx", {"现价": 1.50})
"""

from __future__ import annotations

import os
import logging
import time
import json as _json
from json import JSONDecodeError
from typing import Any, Callable, Dict, List, Optional, TypeVar

from lark_oapi import Client
from lark_oapi.api.bitable.v1 import (
    AppTableRecord,
    BatchUpdateAppTableRecordRequest,
    BatchUpdateAppTableRecordRequestBody,
    ListAppTableRecordRequest,
    UpdateAppTableRecordRequest,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# Schema —— 表名到 ID 的映射（单点维护，改表结构时只改这里）
# ═══════════════════════════════════════════════════════════════

TABLE_MAP: Dict[str, str] = {
    "交易流水表": "tblbnD3uaEdohjji",
    "底仓表": "tblpiht8ex94bM6x",
    "雷达观测表": "tbloKn9F9TPf4wwO",
    "板块轮动配置表": "tblsR4WDQySkxiYP",
    # E 改造（变化感知）新增：持久化每个时段上次推送的签名+指标，用于 diff
    # 字段：时段(单选) / 时间戳(数字) / 签名(文本) / 数据载荷(多行文本)
    "简报快照表": "tblxJqf6BT5GfhGh",
}


def _call_with_retry(fn: Callable, *args, max_retries: int = 3, **kwargs) -> Any:
    """带重试的 API 调用（Render 俄勒冈→飞书中国 API 可能丢包导致 JSON 损坏）。"""
    last_exc = None
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except JSONDecodeError as e:
            last_exc = e
            if attempt < max_retries - 1:
                wait = 0.5 * (2 ** attempt)  # 0.5s → 1s → 2s
                logger.warning("JSON 解析失败（尝试 %d/%d），%0.1fs 后重试: %s",
                               attempt + 1, max_retries, wait, str(e)[:80])
                time.sleep(wait)
    raise last_exc  # type: ignore


class FeishuClient:
    """飞书多维表格读写客户端。"""

    def __init__(
        self,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        bitable_token: Optional[str] = None,
    ):
        self.app_id = app_id or os.environ.get("FEISHU_APP_ID", "")
        self.app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET", "")
        self.bitable_token = bitable_token or os.environ.get("FEISHU_BITABLE_TOKEN", "")

        self._client = (
            Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .build()
        )

        # 运行时解析表名 → 表 ID（避免硬编码 ID）
        self._table_name_to_id: Dict[str, str] = {}
        self._table_id_to_name: Dict[str, str] = {}

    # ── 表管理 ─────────────────────────────────────────────

    def _ensure_table_cache(self) -> None:
        """懒加载表名→ID 映射。"""
        if self._table_name_to_id:
            return
        # 通过 SDK 获取表列表
        # lark-oapi 目前没有直接列出表的 shortcut，用底层 API
        # 这里我们用已有的硬编码映射兜底，后续可以扩展
        self._table_name_to_id = TABLE_MAP.copy()
        self._table_id_to_name = {v: k for k, v in self._table_name_to_id.items()}

    def register_table(self, name: str, table_id: str) -> None:
        """注册表名到 ID 的映射。"""
        self._table_name_to_id[name] = table_id
        self._table_id_to_name[table_id] = name

    def resolve_table_id(self, name_or_id: str) -> str:
        """如果传的是表名，转换为表 ID；如果已经是 ID，直接返回。"""
        self._ensure_table_cache()
        return self._table_name_to_id.get(name_or_id, name_or_id)

    # ── 读取记录 ───────────────────────────────────────────

    def list_records(
        self,
        table: str,
        page_size: int = 200,
        page_token: Optional[str] = None,
    ) -> List[dict]:
        """列出表内所有记录（自动翻页）。使用 raw requests 替代
        lark-oapi SDK 以避免俄勒冈→中国网络导致的 JSON 解析损坏。"""
        import requests as _requests

        table_id = self.resolve_table_id(table)
        all_records: List[dict] = []
        token: Optional[str] = page_token
        max_retries = 3

        # 先拿 tenant access token
        token_url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        resp = _requests.post(token_url, json={
            "app_id": self.app_id, "app_secret": self.app_secret,
        }, timeout=10)
        access_token = resp.json().get("tenant_access_token", "") if resp.ok else ""
        logger.info(
            "获取 token: ok=%s app_id=%s.. token=%s..",
            resp.ok, self.app_id[:8] if self.app_id else "EMPTY",
            access_token[:12] if access_token else "EMPTY",
        )
        if not access_token:
            logger.error("access_token 为空！app_id=%s.. 是否配置了 FEISHU_APP_ID?", self.app_id[:8] if self.app_id else "EMPTY")
            return []

        while True:
            url = (
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/"
                f"{self.bitable_token}/tables/{table_id}/records"
                f"?page_size={page_size}"
            )
            if token:
                url += f"&page_token={token}"

            last_err = None
            for attempt in range(max_retries):
                try:
                    raw = _requests.get(
                        url,
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Content-Type": "application/json",
                        },
                        timeout=15,
                    )
                    logger.info(
                        "raw API 读取 %s | HTTP %d | 前200字: %s",
                        table_id, raw.status_code,
                        raw.text[:200].replace("\n", " "),
                    )
                    if raw.status_code != 200:
                        logger.error(
                            "读取表格 %s HTTP %d: %s",
                            table_id, raw.status_code, raw.text[:300],
                        )
                        return []

                    data = raw.json()
                    if data.get("code") != 0:
                        logger.error("读取表格 %s 失败: %s", table_id, data.get("msg", ""))
                        return []

                    page = data.get("data", {})
                    for item in page.get("items", []):
                        rec: dict = {"_record_id": item.get("record_id", "")}
                        rec.update(item.get("fields", {}))
                        all_records.append(rec)

                    if not page.get("has_more"):
                        return all_records
                    token = page.get("page_token", "")
                    if not token:
                        return all_records
                    break  # success, exit retry loop

                except Exception as e:
                    last_err = e
                    if attempt < max_retries - 1:
                        wait = 1.0 * (2 ** attempt)
                        logger.warning(
                            "raw API 读取重试 %d/%d (%0.1fs): %s",
                            attempt + 1, max_retries, wait, str(e)[:80],
                        )
                        time.sleep(wait)
            else:
                # all retries failed
                logger.error("raw API 读取 %s 全部失败: %s", table_id, last_err)
                break

        return all_records

    # ── 更新记录 ───────────────────────────────────────────

    def update_record(
        self,
        table: str,
        record_id: str,
        fields: Dict[str, Any],
    ) -> bool:
        """
        更新单条记录的指定字段。

        Args:
            table: 表名或表 ID
            record_id: 记录 ID（_record_id）
            fields: {字段名: 新值, ...}

        Returns:
            是否成功
        """
        table_id = self.resolve_table_id(table)

        req = (
            UpdateAppTableRecordRequest.builder()
            .app_token(self.bitable_token)
            .table_id(table_id)
            .record_id(record_id)
            .request_body(
                AppTableRecord.builder()
                .fields(fields)
                .build()
            )
            .build()
        )

        resp = _call_with_retry(self._client.bitable.v1.app_table_record.update, req)
        if not resp.success():
            logger.error("更新记录 %s 失败: %s - %s", record_id, resp.code, resp.msg)
            return False
        return True

    def batch_update_records(
        self,
        table: str,
        updates: List[Dict[str, Any]],  # [{'_record_id': 'rec_xxx', '现价': 1.50}, ...]
    ) -> int:
        """
        批量更新多条记录。

        Args:
            table: 表名或表 ID
            updates: [{'_record_id': ..., '字段名': 值, ...}, ...]

        Returns:
            成功更新的记录数
        """
        if not updates:
            return 0

        table_id = self.resolve_table_id(table)
        records = []

        for up in updates:
            rec_id = up.pop("_record_id", None)
            if not rec_id:
                logger.warning("跳过无 _record_id 的更新: %s", up)
                continue
            records.append(
                AppTableRecord.builder()
                .record_id(rec_id)
                .fields(up)
                .build()
            )

        if not records:
            return 0

        req = (
            BatchUpdateAppTableRecordRequest.builder()
            .app_token(self.bitable_token)
            .table_id(table_id)
            .request_body(
                BatchUpdateAppTableRecordRequestBody.builder()
                .records(records)
                .build()
            )
            .build()
        )

        resp = _call_with_retry(self._client.bitable.v1.app_table_record.batch_update, req)
        if not resp.success():
            logger.error("批量更新失败: %s - %s", resp.code, resp.msg)
            return 0

        return len(resp.data.records or [])

    def delete_record(self, table: str, record_id: str) -> bool:
        """删除一条记录。

        Args:
            table: 表名或表 ID
            record_id: 记录 ID

        Returns:
            True 如果删除成功，否则 False
        """
        from lark_oapi.api.bitable.v1 import DeleteAppTableRecordRequest

        table_id = self.resolve_table_id(table)

        req = (
            DeleteAppTableRecordRequest.builder()
            .app_token(self.bitable_token)
            .table_id(table_id)
            .record_id(record_id)
            .build()
        )

        resp = _call_with_retry(self._client.bitable.v1.app_table_record.delete, req)
        if not resp.success():
            logger.error("删除记录失败: %s - %s", resp.code, resp.msg)
            return False
        return True

    # ── 健康检查 ───────────────────────────────────────────

    def create_record(
        self,
        table: str,
        fields: Dict[str, Any],
    ) -> Optional[str]:
        """创建一条新记录。

        Args:
            table: 表名或表 ID
            fields: 字段名 → 值的字典

        Returns:
            新记录的 record_id，失败返回 None
        """
        from lark_oapi.api.bitable.v1 import (
            CreateAppTableRecordRequest,
            AppTableRecord,
        )

        table_id = self.resolve_table_id(table)

        record = AppTableRecord.builder().fields(fields).build()

        req = (
            CreateAppTableRecordRequest.builder()
            .app_token(self.bitable_token)
            .table_id(table_id)
            .request_body(record)
            .build()
        )

        resp = _call_with_retry(self._client.bitable.v1.app_table_record.create, req)
        if not resp.success():
            logger.error("创建记录失败: %s - %s", resp.code, resp.msg)
            return None

        return getattr(resp.data.record, "record_id", None)

    def is_configured(self) -> bool:
        """检查飞书三要素是否都配置了。"""
        return bool(self.app_id and self.app_secret and self.bitable_token)


# ═══════════════════════════════════════════════════════════════
# 本地隔离工厂 (P0 改造, 2026-09-05)
# ═══════════════════════════════════════════════════════════════
#
# 所有 FeishuClient() 实例化必须通过本函数，禁止直接调用构造函数。
# 本地开发 (非 GitHub Actions) 一律返回 None，调用方负责本地 fallback。
#
# 违规热点（11 处）已在 2026-09-05 全部改造：
#   - strategy.py _fetch_radar_signals() / judge_from_feishu()
#   - radar.py scan_radar()
#   - market_data.py fetch_sector_deltas()
#   - briefing.py _build_trade_summary()
#   - advisor.py load_portfolio()
#   - pending_resolver.py resolve_pending()
#   - earnings_calendar.py _get_radar_us_tickers()
#   - global_news.py 雷达加载
#   - price_updater.py update_all_prices()
#   - bot_server.py _build_holdings_block()


def get_feishu_client_or_none() -> "FeishuClient | None":
    """工厂函数：生产环境返回真 FeishuClient，本地开发返回 None。

    用法：
        client = get_feishu_client_or_none()
        if client is None:
            return <本地 fallback>  # 比如 "", [], {}
        records = client.list_records("底仓表")
        ...

    本地开发（任何非 GITHUB_ACTIONS=true 环境）必须自己负责 fallback，
    禁止再裸调用 FeishuClient()。
    """
    from src.env import is_production
    if not is_production():
        return None
    return FeishuClient()


# ═══════════════════════════════════════════════════════════════
# 简报快照读写 (E 改造, 2026-09-05)
# ═══════════════════════════════════════════════════════════════
#
# 用途：每个时段的简报推送末尾写一条快照（含本时段核心数据 + signature）。
# 下次推送开头读上一条做 diff → 决定是否调 LLM。
#
# 存储双路径：
#   - 生产 (GITHUB_ACTIONS=true)：飞书表 "简报快照表"
#   - 本地开发：test/fixtures/briefing_snapshots_mock.json
#
# 飞书表 schema（用户需在飞书手动创建）：
#   - 时段 (单选)：morning / asia_pacific / midday / closing / evening / sat_morning / sun_evening
#   - 时间戳 (数字，毫秒)
#   - 签名 (文本，diff 用的 hash)
#   - 数据载荷 (多行文本，JSON 字符串)

_SNAPSHOT_TABLE_NAME = "简报快照表"
_SNAPSHOT_FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "tests", "fixtures", "briefing_snapshots_mock.json",
)


def _read_snapshot_from_fixture(slot: str) -> dict | None:
    """本地开发：读取 fixture 文件中某 slot 上次快照。"""
    if not os.path.exists(_SNAPSHOT_FIXTURE_PATH):
        return None
    try:
        with open(_SNAPSHOT_FIXTURE_PATH, "r", encoding="utf-8") as f:
            data = _json.load(f)
        entry = data.get(slot)
        if entry is None:
            return None
        return {
            "slot": slot,
            "timestamp": entry.get("timestamp", 0),
            "signature": entry.get("signature", ""),
            "payload": entry.get("payload", {}),
        }
    except Exception as e:
        logger.warning("[fixture] 读快照失败 %s: %s", slot, e)
        return None


def _write_snapshot_to_fixture(slot: str, payload: dict, signature: str) -> str:
    """本地开发：写 fixture 文件。"""
    data = {}
    if os.path.exists(_SNAPSHOT_FIXTURE_PATH):
        try:
            with open(_SNAPSHOT_FIXTURE_PATH, "r", encoding="utf-8") as f:
                data = _json.load(f)
        except Exception:
            data = {}
    data[slot] = {
        "timestamp": int(time.time() * 1000),
        "signature": signature,
        "payload": payload,
    }
    os.makedirs(os.path.dirname(_SNAPSHOT_FIXTURE_PATH), exist_ok=True)
    with open(_SNAPSHOT_FIXTURE_PATH, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=2)
    return "fixture"


def _read_snapshot_from_feishu(slot: str, client: "FeishuClient") -> dict | None:
    """生产：飞书表读取某 slot 最新一条快照。"""
    records = client.list_records(_SNAPSHOT_TABLE_NAME)
    matched = [r for r in records if r.get("时段") == slot]
    if not matched:
        return None
    try:
        matched.sort(key=lambda r: float(r.get("时间戳", 0) or 0), reverse=True)
    except Exception:
        pass
    latest = matched[0]
    payload_text = latest.get("数据载荷", "{}")
    try:
        payload = _json.loads(payload_text) if isinstance(payload_text, str) else payload_text
    except Exception:
        payload = {}
    return {
        "slot": slot,
        "timestamp": float(latest.get("时间戳", 0) or 0),
        "signature": latest.get("签名", ""),
        "payload": payload,
    }


def _write_snapshot_to_feishu(slot: str, payload: dict, signature: str, client: "FeishuClient") -> str | None:
    """生产：飞书表写入快照（先删旧记录，再写新）。"""
    old_records = client.list_records(_SNAPSHOT_TABLE_NAME)
    for r in old_records:
        if r.get("时段") == slot:
            try:
                client.delete_record(_SNAPSHOT_TABLE_NAME, r["_record_id"])
            except Exception as e:
                logger.warning("[飞书] 删旧快照失败 %s: %s", r.get("_record_id"), e)
    return client.create_record(_SNAPSHOT_TABLE_NAME, {
        "时段": slot,
        "时间戳": int(time.time() * 1000),
        "签名": signature,
        "数据载荷": _json.dumps(payload, ensure_ascii=False),
    })


def read_briefing_snapshot(slot: str) -> dict | None:
    """读某时段上次推送的快照。

    Returns:
        {"slot", "timestamp", "signature", "payload": {...}} 或 None（无快照时）
    """
    from src.env import is_production
    if not is_production():
        return _read_snapshot_from_fixture(slot)
    client = get_feishu_client_or_none()
    if client is None:
        return None
    return _read_snapshot_from_feishu(slot, client)


def write_briefing_snapshot(slot: str, payload: dict, signature: str) -> str | None:
    """写快照（覆盖该 slot 的所有旧记录）。

    Returns:
        record_id (飞书) 或 "fixture" (本地) 或 None (失败)
    """
    from src.env import is_production
    if not is_production():
        return _write_snapshot_to_fixture(slot, payload, signature)
    client = get_feishu_client_or_none()
    if client is None:
        return None
    return _write_snapshot_to_feishu(slot, payload, signature, client)
