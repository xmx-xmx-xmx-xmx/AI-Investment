"""
共享 LLM 客户端工厂 —— 单一配置点。

所有模块共用此工厂获取 SiliconFlow 托管的 LLM 客户端，
避免各自散落 API Key / Base URL / Model 的重复读取。

🔥 2026-08-25 重构：从三层降级（DeepSeek-V4-Flash / Qwen3.6-27B / Qwen3.5-9B）
简化为两层（主 DeepSeek-V3.2 / 备 Qwen3.5-9B）。
原因：DeepSeek-V4-Flash 与 Qwen3.6-27B 需付费，代金券只覆盖 Qwen3.5-9B，
账户欠费时前两层 401/限流，三层降级链实际只剩 9B 撑，质量不稳。
现主备两层均走代金券免费模型，彻底规避欠费中断。

充值升级入口（保留）：
  若后续认可充值用更好模型，只需改环境变量即可，无需改代码：
    SILICONFLOW_MODEL=deepseek-ai/DeepSeek-V4-Flash  （主，付费）
    SILICONFLOW_FALLBACK_MODEL=Qwen/Qwen3.5-9B       （备，免费）
  本地改 .env，生产改 GitHub Secrets。
"""

from __future__ import annotations

import os
from openai import OpenAI

__all__ = [
    "get_llm_client", "get_llm_model", "LLM_MODEL", "LLM_BASE_URL",
    "get_translation_client", "get_translation_model", "TRANSLATION_MODEL",
    "get_fallback_llm_client", "get_fallback_llm_model", "FALLBACK_LLM_MODEL",
]

LLM_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
LLM_BASE_URL = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")

# ── 主模型：DeepSeek-V3.2（代金券免费，最新 DeepSeek，中文+推理强）──
LLM_MODEL = os.environ.get("SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V3.2")

# ── 翻译模型：Qwen3-32B（代金券免费，翻译英文标题用，与主备解耦）──
TRANSLATION_MODEL = os.environ.get(
    "SILICONFLOW_TRANSLATION_MODEL", "Qwen/Qwen3-32B"
)

# ── 备模型：Qwen3.5-9B（代金券免费，主模型超时/异常时降级）──
# 9B 能力有限，降级时 briefing.py 用填空式短 prompt（见 _ai_insight 降级段）
FALLBACK_LLM_MODEL = os.environ.get(
    "SILICONFLOW_FALLBACK_MODEL", "Qwen/Qwen3.5-9B"
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

    max_retries=0：每个 LLM 调用点都有 try/except 降级逻辑
    （主解读→备模型→纯文本兜底），重试只会让单次卡死翻倍，毫无收益。
    120s 对 DeepSeek-V3.2 正常解读 (15-40s) 是 3-8 倍余量。
    """
    return _build_client(timeout=120.0, max_retries=0)


def get_llm_model() -> str:
    """返回主解读用的模型名称（默认 DeepSeek-V3.2，代金券免费）。"""
    return LLM_MODEL


def get_translation_client() -> OpenAI | None:
    """🔧 翻译专用客户端：60s 短超时 + 不重试。

    Qwen3-32B 翻译英文标题只需 5-15s，60s 是极端情况的上限。
    max_retries=0：翻译失败直接回退英文原标题，不浪费时间重试。
    """
    return _build_client(timeout=60.0, max_retries=0)


def get_translation_model() -> str:
    """返回翻译专用模型（默认 Qwen/Qwen3-32B，代金券免费）。"""
    return TRANSLATION_MODEL


def get_fallback_llm_client() -> OpenAI | None:
    """🔄 备模型客户端：60s 超时 + 不重试。

    Qwen3.5-9B 处理填空式短 prompt 只需 5-15s，60s 是极端上限。
    主模型（DeepSeek-V3.2）超时/异常时降级到此。
    """
    return _build_client(timeout=60.0, max_retries=0)


def get_fallback_llm_model() -> str:
    """返回备模型名称（默认 Qwen/Qwen3.5-9B，代金券免费）。"""
    return FALLBACK_LLM_MODEL
