"""
共享 LLM 客户端工厂 —— 支持三级防出错阶梯降级 (#31 智能化升级)。

三级阶梯降级机制 (Three-Tier Fallback Mechanism):
1. Tier 1: Google Gemini (优先度最高)
   - 环境变量: GEMINI_API_KEY (或 GOOGLE_API_KEY)
   - 端点: https://generativelanguage.googleapis.com/v1beta/openai/
   - 模型: gemini-2.5-flash (极速响应 ~0.5s，大上下文，消耗 Google Cloud 赠费)
2. Tier 2: DeepSeek 官方 API (次优先)
   - 环境变量: DEEPSEEK_API_KEY
   - 端点: https://api.deepseek.com
   - 模型: deepseek-chat (直连官方端点，消耗 DeepSeek 官方余额)
3. Tier 3: 硅基流动 SiliconFlow (基底兜底)
   - 环境变量: SILICONFLOW_API_KEY
   - 端点: https://api.siliconflow.cn/v1
   - 主模型: deepseek-ai/DeepSeek-V3.2 / 备模型: Qwen/Qwen3.5-9B (代金券免费)
4. Tier 4: 本地纯文本确定性脱水 (最终安全网)
   - 当全部网络提供商均超时或异常时，由上层无缝切回本地确定性模板，100% 保障推送不中断。

通用自定义覆盖:
   - 若设置了通用的 LLM_API_KEY，且可选 LLM_BASE_URL、LLM_MODEL，将作为 Tier 0 优先使用。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from openai import OpenAI

logger = logging.getLogger(__name__)

__all__ = [
    "get_llm_client", "get_llm_model", "LLM_MODEL", "LLM_BASE_URL",
    "GEMINI_MODEL", "DEEPSEEK_MODEL", "SILICONFLOW_MODEL",
    "get_translation_client", "get_translation_model", "TRANSLATION_MODEL",
    "get_fallback_llm_client", "get_fallback_llm_model", "FALLBACK_LLM_MODEL",
    "ProviderConfig", "get_tier_providers", "chat_completion_with_fallback",
    "record_completion", "get_last_completion_info",
]


# ── 最近一次成功调用的大模型信息（供飞书卡片展示智囊标识）──
_LAST_COMPLETION_INFO = {
    "provider": "",
    "model": "",
}


def record_completion(provider: str, model: str) -> None:
    """记录成功调用的模型信息。"""
    _LAST_COMPLETION_INFO["provider"] = provider
    _LAST_COMPLETION_INFO["model"] = model


def get_last_completion_info() -> str:
    """返回最近一次成功调用的模型描述，例如 'Google-Gemini (gemini-3.8-flash)'。"""
    if _LAST_COMPLETION_INFO["model"]:
        return f"{_LAST_COMPLETION_INFO['provider']} ({_LAST_COMPLETION_INFO['model']})"
    providers = get_tier_providers()
    if providers:
        return f"{providers[0].name} ({providers[0].model})"
    return "本地确定性模板 (Local-Template)"


@dataclass
class ProviderConfig:
    name: str
    api_key: str
    base_url: str
    model: str
    timeout: float = 60.0


# ── 模型与端点配置（支持环境变量动态覆盖，空字符串自动回退默认值）──
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-3.8-flash"
GEMINI_BASE_URL = os.environ.get("GEMINI_BASE_URL", "").strip() or "https://generativelanguage.googleapis.com/v1beta/openai/"

DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "").strip() or "deepseek-4.1-flash"
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "").strip() or "https://api.deepseek.com"

# ── SiliconFlow 兼容默认项（支持环境变量自定义切换任意模型）──
LLM_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "").strip()
LLM_BASE_URL = os.environ.get("SILICONFLOW_BASE_URL", "").strip() or "https://api.siliconflow.cn/v1"
LLM_MODEL = os.environ.get("SILICONFLOW_MODEL", "").strip() or "deepseek-ai/DeepSeek-V3.2"
SILICONFLOW_MODEL = LLM_MODEL
TRANSLATION_MODEL = os.environ.get("SILICONFLOW_TRANSLATION_MODEL", "").strip() or "Qwen/Qwen3-32B"
FALLBACK_LLM_MODEL = os.environ.get("SILICONFLOW_FALLBACK_MODEL", "").strip() or "Qwen/Qwen3.5-9B"


def get_tier_providers() -> List[ProviderConfig]:
    """按优先级返回所有已配置 API Key 的提供商链（三级防出错）。"""
    providers: List[ProviderConfig] = []

    # ── Tier 0: 通用自定义覆盖（如自建 One-API / 中转代理）──
    custom_key = os.environ.get("LLM_API_KEY", "").strip()
    if custom_key:
        providers.append(ProviderConfig(
            name="Custom-OpenAI",
            api_key=custom_key,
            base_url=os.environ.get("LLM_BASE_URL", "").strip() or "https://api.openai.com/v1",
            model=os.environ.get("LLM_MODEL", "").strip() or "gpt-4o-mini",
            timeout=float(os.environ.get("LLM_TIMEOUT", "").strip() or "60.0"),
        ))

    # ── Tier 1: Google Gemini (Google Cloud / Google AI Studio) ──
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()
    if gemini_key:
        curr_gemini_model = os.environ.get("GEMINI_MODEL", "").strip() or GEMINI_MODEL
        curr_gemini_url = os.environ.get("GEMINI_BASE_URL", "").strip() or GEMINI_BASE_URL
        providers.append(ProviderConfig(
            name="Google-Gemini",
            api_key=gemini_key,
            base_url=curr_gemini_url,
            model=curr_gemini_model,
            timeout=float(os.environ.get("GEMINI_TIMEOUT", "").strip() or "45.0"),
        ))

    # ── Tier 2: DeepSeek 官方 API ──
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if deepseek_key:
        curr_deepseek_model = os.environ.get("DEEPSEEK_MODEL", "").strip() or DEEPSEEK_MODEL
        curr_deepseek_url = os.environ.get("DEEPSEEK_BASE_URL", "").strip() or DEEPSEEK_BASE_URL
        providers.append(ProviderConfig(
            name="DeepSeek-Official",
            api_key=deepseek_key,
            base_url=curr_deepseek_url,
            model=curr_deepseek_model,
            timeout=float(os.environ.get("DEEPSEEK_TIMEOUT", "").strip() or "60.0"),
        ))

    # ── Tier 3: 硅基流动 SiliconFlow ──
    sf_key = os.environ.get("SILICONFLOW_API_KEY", "").strip()
    if sf_key:
        curr_sf_model = os.environ.get("SILICONFLOW_MODEL", "").strip() or LLM_MODEL or "deepseek-ai/DeepSeek-V3.2"
        curr_sf_url = os.environ.get("SILICONFLOW_BASE_URL", "").strip() or LLM_BASE_URL or "https://api.siliconflow.cn/v1"
        providers.append(ProviderConfig(
            name="SiliconFlow",
            api_key=sf_key,
            base_url=curr_sf_url,
            model=curr_sf_model,
            timeout=float(os.environ.get("SILICONFLOW_TIMEOUT", "").strip() or "90.0"),
        ))

    return providers


def _build_client_from_config(cfg: ProviderConfig, max_retries: int = 0) -> OpenAI:
    """按提供商配置创建 OpenAI 兼容客户端。"""
    return OpenAI(
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        timeout=cfg.timeout,
        max_retries=max_retries,
    )


def _build_client(timeout: float, max_retries: int) -> OpenAI | None:
    """内部工厂：默认尝试三级链条中最高优先级的客户端。"""
    providers = get_tier_providers()
    if providers:
        cfg = providers[0]
        cfg.timeout = timeout
        return _build_client_from_config(cfg, max_retries=max_retries)
    if LLM_API_KEY:
        return OpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            timeout=timeout,
            max_retries=max_retries,
        )
    return None


def get_llm_client() -> OpenAI | None:
    """返回当前最高优先级的 LLM 客户端（Gemini -> DeepSeek -> SiliconFlow）。"""
    return _build_client(timeout=120.0, max_retries=0)


def get_llm_model() -> str:
    """返回当前最高优先级的模型名称。"""
    providers = get_tier_providers()
    if providers:
        return providers[0].model
    return LLM_MODEL


def get_translation_client() -> OpenAI | None:
    """🔧 翻译专用客户端：60s 短超时 + 不重试。"""
    providers = get_tier_providers()
    if providers:
        return _build_client_from_config(providers[0], max_retries=0)
    return _build_client(timeout=60.0, max_retries=0)


def get_translation_model() -> str:
    """返回翻译专用模型。"""
    providers = get_tier_providers()
    if providers:
        # 如果是 Gemini，直接用 Gemini Flash 翻译极快
        if "Gemini" in providers[0].name:
            return providers[0].model
        if "DeepSeek" in providers[0].name:
            return providers[0].model
    return TRANSLATION_MODEL


def get_fallback_llm_client() -> OpenAI | None:
    """🔄 备模型客户端：三级防出错次级节点。"""
    providers = get_tier_providers()
    if len(providers) > 1:
        # 如果配置了多个提供商，备模型走第二优先级
        return _build_client_from_config(providers[1], max_retries=0)
    # 单提供商时，若为 SiliconFlow 则走其轻量 Qwen3.5-9B
    return _build_client(timeout=60.0, max_retries=0)


def get_fallback_llm_model() -> str:
    """返回备模型名称。"""
    providers = get_tier_providers()
    if len(providers) > 1:
        return providers[1].model
    return FALLBACK_LLM_MODEL


def chat_completion_with_fallback(
    messages: List[Dict[str, str]],
    max_tokens: int = 1024,
    temperature: float = 0.3,
) -> Tuple[str, str]:
    """统一聊天补全接口 —— 三级阶梯降级防出错。

    执行逻辑：
    1. 优先尝试 Tier 1（如 Google Gemini）；
    2. 若异常/超时，捕获并自动降级尝试 Tier 2（如 DeepSeek 官方）；
    3. 若仍异常，捕获并降级尝试 Tier 3（如 SiliconFlow）；
    4. 若仍失败或未配置任何 Key，抛出 RuntimeError，供上层回退纯本地脱水。

    Returns:
        (content, provider_name)
    """
    providers = get_tier_providers()

    # 如果没有任何 providers，但存在旧版 LLM_API_KEY
    if not providers and LLM_API_KEY:
        providers.append(ProviderConfig(
            name="SiliconFlow",
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            model=LLM_MODEL,
            timeout=90.0,
        ))

    if not providers:
        raise RuntimeError("未配置任何 LLM 提供商的 API Key")

    last_error: Optional[Exception] = None
    for p in providers:
        try:
            logger.info("尝试大模型调用 [%s] (model=%s)", p.name, p.model)
            client = _build_client_from_config(p, max_retries=0)
            resp = client.chat.completions.create(
                model=p.model,
                messages=messages,  # type: ignore
                max_tokens=max_tokens,
                temperature=temperature,
            )
            choice = resp.choices[0]
            content = choice.message.content or ""
            content = content.strip()
            if content:
                record_completion(p.name, p.model)
                logger.info("大模型调用成功 [%s] (返回 %d 字符)", p.name, len(content))
                return content, p.name
        except Exception as e:
            last_error = e
            logger.warning("[%s] 调用异常或超时: %s，立即降级下一级", p.name, str(e)[:100])
            continue

    raise RuntimeError(f"所有配置的 LLM 提供商均调用失败，最后错误: {last_error}")
