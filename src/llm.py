"""
共享 LLM 客户端工厂 —— 支持多源防出错阶梯降级、进程内 401/402/403 熔断与双层思考控制。

阶梯降级机制 (Multi-Tier Fallback Mechanism):
1. Tier 1: DeepSeek 官方 API (主力优先)
   - 环境变量: DEEPSEEK_API_KEY
   - 端点: https://api.deepseek.com (支持 DEEPSEEK_BASE_URL 覆盖)
   - 模型: deepseek-flash (支持 DEEPSEEK_MODEL 覆盖)
   - 双层思考控制:
     * 展示/轻量层 (thinking=False): 传 extra_body={"thinking": {"type": "disabled"}}，极速响应、零思考 Token 浪费，杜绝思考过程耗尽 max_tokens 导致空响应。
     * 简报分析层 (thinking=True): 传 extra_body={"thinking": {"type": "enabled"}, "reasoning_effort": "low"}，兼顾跨资产因果推演深度与响应速度。
2. Tier 2: 硅基流动 SiliconFlow (基底兜底)
   - 环境变量: SILICONFLOW_API_KEY
   - 端点: https://api.siliconflow.cn/v1 (支持 SILICONFLOW_BASE_URL 覆盖)
   - 主模型: deepseek-ai/DeepSeek-V3.2 (支持 SILICONFLOW_MODEL 覆盖) / 备模型: Qwen/Qwen3.5-9B
3. Tier 3: 本地纯文本确定性脱水 (最终安全网)
   - 当全部网络提供商均超时或异常时，由上层无缝切回本地确定性模板，100% 保障推送不中断。

通用自定义覆盖:
   - 若设置了通用的 LLM_API_KEY，且可选 LLM_BASE_URL、LLM_MODEL，将作为 Tier 0 优先使用。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple
from openai import OpenAI

logger = logging.getLogger(__name__)

__all__ = [
    "get_llm_client", "get_llm_model", "LLM_MODEL", "LLM_BASE_URL",
    "DEEPSEEK_MODEL", "SILICONFLOW_MODEL",
    "get_translation_client", "get_translation_model", "TRANSLATION_MODEL",
    "get_fallback_llm_client", "get_fallback_llm_model", "FALLBACK_LLM_MODEL",
    "ProviderConfig", "get_tier_providers", "chat_completion_with_fallback",
    "record_completion", "get_last_completion_info",
    "trip_provider", "reset_circuit_breaker", "get_tripped_providers",
]


# ── 最近一次成功调用的大模型信息（供飞书卡片展示智囊标识，严格以实际成功返回为准）──
_LAST_COMPLETION_INFO = {
    "provider": "",
    "model": "",
}

# ── 进程内熔断集合：遇到 401/402/403 鉴权或欠费错误后，本进程后续调用直接跳过 ──
_TRIPPED_PROVIDERS: Set[str] = set()


def record_completion(provider: str, model: str) -> None:
    """记录实际成功返回非空内容的模型信息。"""
    _LAST_COMPLETION_INFO["provider"] = provider
    _LAST_COMPLETION_INFO["model"] = model


def get_last_completion_info() -> str:
    """返回最近一次实际成功调用的模型描述，例如 'DeepSeek-Official (deepseek-flash)'。"""
    if _LAST_COMPLETION_INFO["model"]:
        return f"{_LAST_COMPLETION_INFO['provider']} ({_LAST_COMPLETION_INFO['model']})"
    providers = get_tier_providers()
    if providers:
        return f"{providers[0].name} ({providers[0].model})"
    return "本地确定性模板 (Local-Template)"


def trip_provider(provider_name: str, reason: str = "") -> None:
    """将指定提供商加入本进程熔断名单（如遇到 401/402/403）。"""
    if provider_name not in _TRIPPED_PROVIDERS:
        _TRIPPED_PROVIDERS.add(provider_name)
        logger.warning("⚡ [%s] 触发进程内熔断 (%s)，后续调用直接跳过", provider_name, reason[:100])


def reset_circuit_breaker() -> None:
    """重置进程内熔断状态（主要用于单元测试隔离）。"""
    _TRIPPED_PROVIDERS.clear()


def get_tripped_providers() -> Set[str]:
    """返回当前已熔断的提供商名称集合（只读副本）。"""
    return set(_TRIPPED_PROVIDERS)


def _is_circuit_trip_error(exc: Exception) -> bool:
    """判定异常是否属于不可恢复的鉴权/欠费/权限错误（401 / 402 / 403）。"""
    status_code = getattr(exc, "status_code", None)
    if status_code in (401, 402, 403):
        return True
    msg = str(exc)
    fatal_tokens = (
        "Error code: 401", "Error code: 402", "Error code: 403",
        "401 Unauthorized", "402 Payment Required", "403 Forbidden",
        "Insufficient Balance", "Authentication Fails", "invalid_api_key",
    )
    return any(tok in msg for tok in fatal_tokens)


@dataclass
class ProviderConfig:
    name: str
    api_key: str
    base_url: str
    model: str
    timeout: float = 60.0


# ── 模型与端点配置（支持环境变量动态覆盖，空字符串自动回退默认值）──
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "").strip() or "deepseek-flash"
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "").strip() or "https://api.deepseek.com"

# ── SiliconFlow 兼容默认项（支持环境变量自定义切换任意模型）──
LLM_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "").strip()
LLM_BASE_URL = os.environ.get("SILICONFLOW_BASE_URL", "").strip() or "https://api.siliconflow.cn/v1"
LLM_MODEL = os.environ.get("SILICONFLOW_MODEL", "").strip() or "deepseek-ai/DeepSeek-V3.2"
SILICONFLOW_MODEL = LLM_MODEL
TRANSLATION_MODEL = os.environ.get("SILICONFLOW_TRANSLATION_MODEL", "").strip() or "Qwen/Qwen3-32B"
FALLBACK_LLM_MODEL = os.environ.get("SILICONFLOW_FALLBACK_MODEL", "").strip() or "Qwen/Qwen3.5-9B"


def get_tier_providers() -> List[ProviderConfig]:
    """按优先级返回所有已配置 API Key 且未熔断的提供商链（DeepSeek -> SiliconFlow）。"""
    providers: List[ProviderConfig] = []

    # ── Tier 0: 通用自定义覆盖（如自建 One-API / 中转代理）──
    custom_key = os.environ.get("LLM_API_KEY", "").strip()
    if custom_key and "Custom-OpenAI" not in _TRIPPED_PROVIDERS:
        providers.append(ProviderConfig(
            name="Custom-OpenAI",
            api_key=custom_key,
            base_url=os.environ.get("LLM_BASE_URL", "").strip() or "https://api.openai.com/v1",
            model=os.environ.get("LLM_MODEL", "").strip() or "gpt-4o-mini",
            timeout=float(os.environ.get("LLM_TIMEOUT", "").strip() or "60.0"),
        ))

    # ── Tier 1: DeepSeek 官方 API (主力优先) ──
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if deepseek_key and "DeepSeek-Official" not in _TRIPPED_PROVIDERS:
        curr_deepseek_model = os.environ.get("DEEPSEEK_MODEL", "").strip() or DEEPSEEK_MODEL
        curr_deepseek_url = os.environ.get("DEEPSEEK_BASE_URL", "").strip() or DEEPSEEK_BASE_URL
        providers.append(ProviderConfig(
            name="DeepSeek-Official",
            api_key=deepseek_key,
            base_url=curr_deepseek_url,
            model=curr_deepseek_model,
            timeout=float(os.environ.get("DEEPSEEK_TIMEOUT", "").strip() or "60.0"),
        ))

    # ── Tier 2: 硅基流动 SiliconFlow (基底兜底) ──
    sf_key = os.environ.get("SILICONFLOW_API_KEY", "").strip()
    if sf_key and "SiliconFlow" not in _TRIPPED_PROVIDERS:
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
    """内部工厂：默认尝试链条中最高优先级的可用客户端。"""
    providers = get_tier_providers()
    if providers:
        cfg = providers[0]
        cfg.timeout = timeout
        return _build_client_from_config(cfg, max_retries=max_retries)
    if LLM_API_KEY and "SiliconFlow" not in _TRIPPED_PROVIDERS:
        return OpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            timeout=timeout,
            max_retries=max_retries,
        )
    return None


def get_llm_client() -> OpenAI | None:
    """返回当前最高优先级的 LLM 客户端（DeepSeek -> SiliconFlow）。"""
    return _build_client(timeout=120.0, max_retries=0)


_DEFAULT_GET_LLM_CLIENT = get_llm_client


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
        if "DeepSeek" in providers[0].name:
            return providers[0].model
    return TRANSLATION_MODEL


def get_fallback_llm_client() -> OpenAI | None:
    """🔄 备模型客户端：防出错次级节点。"""
    providers = get_tier_providers()
    if len(providers) > 1:
        return _build_client_from_config(providers[1], max_retries=0)
    return _build_client(timeout=60.0, max_retries=0)


def get_fallback_llm_model() -> str:
    """返回备模型名称。"""
    providers = get_tier_providers()
    if len(providers) > 1:
        return providers[1].model
    return FALLBACK_LLM_MODEL


def _build_deepseek_extra_body(provider: ProviderConfig, thinking: bool) -> Optional[Dict[str, Any]]:
    """针对 DeepSeek 官方端点构造思考模式控制参数。

    依据 DeepSeek 官方文档：
    - 省略 thinking 且省略 reasoning_effort 时，deepseek-flash 默认开启 high 思考模式；
    - 展示/轻量层 (thinking=False): 显式传 {"thinking": {"type": "disabled"}} 关闭思考，
      防止思考过程吃光小额 max_tokens 导致正文为空；
    - 简报分析层 (thinking=True): 显式传 {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}，
      以轻量思考兼顾逻辑推演深度与响应速度。
    """
    is_deepseek_official = (
        provider.name == "DeepSeek-Official"
        or "api.deepseek.com" in provider.base_url.lower()
    )
    if not is_deepseek_official:
        return None
    if thinking:
        return {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "low",
        }
    return {
        "thinking": {"type": "disabled"},
    }


def chat_completion_with_fallback(
    messages: List[Dict[str, str]],
    max_tokens: int = 1024,
    temperature: float = 0.3,
    thinking: bool = False,
) -> Tuple[str, str]:
    """统一聊天补全接口 —— 阶梯降级 + 401/402/403 熔断 + 双层思考控制。

    执行逻辑：
    1. 优先尝试 Tier 1（DeepSeek 官方，按 thinking 参数控制思考/非思考模式）；
    2. 若遇到 401/402/403 鉴权或余额错误，立即将该提供商加入进程熔断名单，后续不再重试；
    3. 若异常/超时/空正文，自动无缝降级尝试 Tier 2（SiliconFlow）；
    4. 若仍失败或未配置任何 Key，抛出 RuntimeError，供上层回退纯本地脱水。

    Returns:
        (content, provider_name)
    """
    # 兼容单元测试对 src.llm.get_llm_client 的 monkeypatch
    if get_llm_client is not _DEFAULT_GET_LLM_CLIENT:
        client = get_llm_client()
        if client is None:
            raise RuntimeError("LLM 不可用")
        model_name = get_llm_model()
        resp = client.chat.completions.create(
            model=model_name,
            messages=messages,  # type: ignore
            max_tokens=max_tokens,
            temperature=temperature,
        )
        choice = resp.choices[0]
        content = (choice.message.content or "").strip()
        if not content:
            raise RuntimeError("LLM 返回内容为空")
        record_completion("Custom-Mock", model_name)
        return content, "Custom-Mock"

    providers = get_tier_providers()

    # 如果没有任何 providers，但存在旧版 LLM_API_KEY 且未熔断
    if not providers and LLM_API_KEY and "SiliconFlow" not in _TRIPPED_PROVIDERS:
        providers.append(ProviderConfig(
            name="SiliconFlow",
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            model=LLM_MODEL,
            timeout=90.0,
        ))

    if not providers:
        raise RuntimeError("未配置任何可用 LLM 提供商的 API Key（或已全部熔断）")

    last_error: Optional[Exception] = None
    for p in providers:
        if p.name in _TRIPPED_PROVIDERS:
            continue
        try:
            extra_body = _build_deepseek_extra_body(p, thinking=thinking)
            effective_max_tokens = max(max_tokens, 1500) if (extra_body and thinking) else max_tokens
            mode_tag = "thinking=on(low)" if (extra_body and thinking) else ("thinking=off" if extra_body else "default")
            logger.info("尝试大模型调用 [%s] (model=%s, %s)", p.name, p.model, mode_tag)
            client = _build_client_from_config(p, max_retries=0)
            create_kwargs: Dict[str, Any] = {
                "model": p.model,
                "messages": messages,
                "max_tokens": effective_max_tokens,
                "temperature": temperature,
            }
            if extra_body is not None:
                create_kwargs["extra_body"] = extra_body

            resp = client.chat.completions.create(**create_kwargs)
            choice = resp.choices[0]
            content = (choice.message.content or "").strip()
            finish_reason = getattr(choice, "finish_reason", None)
            if content:
                record_completion(p.name, p.model)
                logger.info("大模型调用成功 [%s] (model=%s, 返回 %d 字符)", p.name, p.model, len(content))
                return content, p.name
            logger.warning(
                "[%s] 返回 HTTP 200 但正文为空 (finish_reason=%s)，立即降级下一级",
                p.name, finish_reason,
            )
            last_error = RuntimeError(f"{p.name} 返回正文为空 (finish_reason={finish_reason})")
        except Exception as e:
            last_error = e
            if _is_circuit_trip_error(e):
                trip_provider(p.name, str(e))
            else:
                logger.warning("[%s] 调用异常或超时: %s，立即降级下一级", p.name, str(e)[:100])
            continue

    raise RuntimeError(f"所有配置的 LLM 提供商均调用失败，最后错误: {last_error}")
