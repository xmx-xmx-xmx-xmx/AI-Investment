"""
共享 LLM 客户端工厂 —— 单一配置点。

所有模块共用此工厂获取 SiliconFlow 托管的 Qwen 模型客户端，
避免各自散落 API Key / Base URL / Model 的重复读取。
"""

from __future__ import annotations

import os
from openai import OpenAI

__all__ = ["get_llm_client", "get_llm_model", "LLM_MODEL", "LLM_BASE_URL"]

LLM_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
LLM_BASE_URL = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
LLM_MODEL = os.environ.get("SILICONFLOW_MODEL", "Qwen/Qwen3-30B-A3B-Instruct-2507")


def get_llm_client() -> OpenAI | None:
    """获取已配置的 OpenAI 兼容客户端。

    Returns:
        OpenAI 客户端实例，如果 API Key 未配置则返回 None。
    """
    if not LLM_API_KEY:
        return None
    return OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)


def get_llm_model() -> str:
    """返回当前配置的模型名称。"""
    return LLM_MODEL
