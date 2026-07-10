"""
共享 LLM 客户端工厂 —— 单一配置点。

所有模块共用此工厂获取 SiliconFlow 托管的 LLM 客户端，
避免各自散落 API Key / Base URL / Model 的重复读取。
"""

from __future__ import annotations

import os
from openai import OpenAI

__all__ = ["get_llm_client", "get_llm_model", "LLM_MODEL", "LLM_BASE_URL"]

LLM_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
LLM_BASE_URL = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
LLM_MODEL = os.environ.get("SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V4-Flash")


def get_llm_client() -> OpenAI | None:
    """获取已配置的 OpenAI 兼容客户端。

    Returns:
        OpenAI 客户端实例，如果 API Key 未配置则返回 None。
    """
    if not LLM_API_KEY:
        return None
    # 🔥 2026-07-07 容灾改造：加硬超时 180s + 最多重试 1 次
    # SiliconFlow API 正常 RTT 2-10s，3 分钟已是极端情况。
    # GitHub Actions 总时限 15min，单次 LLM 调用不能吃掉 >3min。
    # max_retries=1 防止 SDK 自动重试把超时翻倍到 6min。
    return OpenAI(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        timeout=180.0,
        max_retries=1,
    )


def get_llm_model() -> str:
    """返回当前配置的模型名称。"""
    return LLM_MODEL
