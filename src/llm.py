"""
共享 LLM 客户端工厂 —— 单一配置点。

所有模块共用此工厂获取 SiliconFlow 托管的 LLM 客户端，
避免各自散落 API Key / Base URL / Model 的重复读取。
"""

from __future__ import annotations

import os
from openai import OpenAI

__all__ = [
    "get_llm_client", "get_llm_model", "LLM_MODEL", "LLM_BASE_URL",
    "get_translation_client", "get_translation_model", "TRANSLATION_MODEL",
]

LLM_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
LLM_BASE_URL = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
LLM_MODEL = os.environ.get("SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V4-Flash")

# 🔥 2026-07-07 容灾改造：翻译用轻量模型 Qwen3-32B，不消耗 DeepSeek 代金券额度
# SiliconFlow 免费档 Qwen3-32B ≈ ¥0.7/M token，比 DeepSeek (¥1/M) 更便宜
TRANSLATION_MODEL = os.environ.get(
    "SILICONFLOW_TRANSLATION_MODEL", "Qwen/Qwen3-32B"
)


def _build_client(timeout: float, max_retries: int) -> OpenAI | None:
    """内部工厂：按参数创建 OpenAI 兼容客户端。"""
    if not LLM_API_KEY:
        return None
    return OpenAI(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        timeout=timeout,
        max_retries=max_retries,
    )


def get_llm_client() -> OpenAI | None:
    """主解读/雷达/RSS 匹配用的客户端。120s 超时 + 不重试。

    🔥 2026-07-13 修复：max_retries=0。每个 LLM 调用点都有 try/except
    降级逻辑（主解读→纯文本摘要、RSS→跳过国际快讯、雷达→跳过解读），
    重试只会让单次卡死从 2min 翻倍到 4min，毫无收益。
    120s 对 DeepSeek-V4-Flash 正常解读 (15-40s) 是 3-8 倍余量。
    """
    return _build_client(timeout=120.0, max_retries=0)


def get_llm_model() -> str:
    """返回主解读用的模型名称（默认 DeepSeek-V4-Flash）。"""
    return LLM_MODEL


def get_translation_client() -> OpenAI | None:
    """🔧 翻译专用客户端：60s 短超时 + 不重试。

    Qwen3-32B 翻译英文标题只需 5-15s，60s 是极端情况的上限。
    max_retries=0：翻译失败直接回退英文原标题，不浪费时间重试。
    """
    return _build_client(timeout=60.0, max_retries=0)


def get_translation_model() -> str:
    """返回翻译专用模型（默认 Qwen/Qwen3-32B）。"""
    return TRANSLATION_MODEL
