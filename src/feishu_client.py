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
