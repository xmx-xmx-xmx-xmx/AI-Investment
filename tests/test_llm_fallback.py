"""
测试多源大模型二级降级机制、思考模式控制与进程级熔断 (src/llm.py)。
覆盖：
1. 提供商优先级排序 (Custom Tier 0 -> DeepSeek Tier 1 -> SiliconFlow Tier 2，已移除 Google Gemini)
2. chat_completion_with_fallback 阶梯级联降级 (Tier 1 失败/空正文 -> 尝试 Tier 2 -> 全挂抛 RuntimeError)
3. DeepSeek 双层思考模式参数控制 (thinking=False -> disabled, thinking=True -> enabled + reasoning_effort=low)
4. 401/402/403 进程级熔断 (触发后本进程内自动跳过该 provider)
5. 真实成功模型标识记录
"""

from unittest.mock import MagicMock, patch
import pytest

from src.llm import (
    ProviderConfig,
    chat_completion_with_fallback,
    get_fallback_llm_client,
    get_fallback_llm_model,
    get_last_completion_info,
    get_llm_client,
    get_llm_model,
    get_tier_providers,
    get_translation_model,
     get_tripped_providers,
    record_completion,
    reset_circuit_breaker,
)


@pytest.fixture(autouse=True)
def _clean_circuit_breaker():
    """每个测试前后重置熔断器状态。"""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


def test_get_tier_providers_empty(monkeypatch):
    """未设置任何 Key 时返回空列表。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)

    providers = get_tier_providers()
    assert providers == []


def test_get_tier_providers_ordering_without_gemini(monkeypatch):
    """测试多 Key 存在时的优先级排序：Custom -> DeepSeek -> SiliconFlow（忽略 Gemini Key）。"""
    monkeypatch.setenv("LLM_API_KEY", "custom-sk-123")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-sk-ignored")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-sk-ignored")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-sk-789")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sf-sk-abc")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("SILICONFLOW_MODEL", raising=False)

    providers = get_tier_providers()
    names = [p.name for p in providers]
    assert names == ["Custom-OpenAI", "DeepSeek-Official", "SiliconFlow"]
    assert providers[1].model == "deepseek-flash"
    assert providers[2].model == "deepseek-ai/DeepSeek-V3.2"


def test_get_tier_providers_env_model_override(monkeypatch):
    """测试通过环境变量自定义模型名字（DEEPSEEK_MODEL / SILICONFLOW_MODEL）。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-sk-789")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sf-sk-123")
    monkeypatch.setenv("SILICONFLOW_MODEL", "Qwen/Qwen3-32B")

    providers = get_tier_providers()
    assert [p.name for p in providers] == ["DeepSeek-Official", "SiliconFlow"]
    assert providers[0].model == "deepseek-chat"
    assert providers[1].model == "Qwen/Qwen3-32B"


def test_siliconflow_model_override_and_empty_fallback(monkeypatch):
    """测试 SiliconFlow 模型名与 Base URL 支持环境变量修改，且空字符串自动回退默认值。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sf-sk-test")

    # 1. 自定义修改模型名与端点
    monkeypatch.setenv("SILICONFLOW_MODEL", "Qwen/Qwen2.5-72B-Instruct")
    monkeypatch.setenv("SILICONFLOW_BASE_URL", "https://custom.siliconflow.cn/v1")
    providers = get_tier_providers()
    assert len(providers) == 1
    assert providers[0].name == "SiliconFlow"
    assert providers[0].model == "Qwen/Qwen2.5-72B-Instruct"
    assert providers[0].base_url == "https://custom.siliconflow.cn/v1"

    # 2. 空字符串（如 GitHub Actions 传递未设置的变量）自动回退默认
    monkeypatch.setenv("SILICONFLOW_MODEL", "   ")
    monkeypatch.setenv("SILICONFLOW_BASE_URL", "   ")
    providers_empty = get_tier_providers()
    assert providers_empty[0].model == "deepseek-ai/DeepSeek-V3.2"
    assert providers_empty[0].base_url == "https://api.siliconflow.cn/v1"


def test_chat_completion_thinking_disabled_and_enabled(monkeypatch):
    """验证 DeepSeek 官方端点在展示层 (thinking=False) 与分析层 (thinking=True) 传递正确的 extra_body。"""
    cfg_ds = ProviderConfig(
        name="DeepSeek-Official",
        api_key="k1",
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
    )
    monkeypatch.setattr("src.llm.get_tier_providers", lambda: [cfg_ds])

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="DeepSeek 输出"), finish_reason="stop")]
    mock_client.chat.completions.create.return_value = mock_resp

    with patch("src.llm._build_client_from_config", return_value=mock_client):
        # 1. 展示层 thinking=False -> thinking.type = disabled，保留原始 max_tokens
        content, provider = chat_completion_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=200,
            thinking=False,
        )
        assert content == "DeepSeek 输出"
        assert provider == "DeepSeek-Official"
        call_kwargs_off = mock_client.chat.completions.create.call_args_list[0].kwargs
        assert call_kwargs_off["extra_body"] == {"thinking": {"type": "disabled"}}
        assert call_kwargs_off["max_tokens"] == 200

        # 2. 简报分析层 thinking=True -> thinking.type = enabled + reasoning_effort = low，且 max_tokens >= 1500
        content2, provider2 = chat_completion_with_fallback(
            messages=[{"role": "user", "content": "analyze"}],
            max_tokens=800,
            thinking=True,
        )
        assert content2 == "DeepSeek 输出"
        assert provider2 == "DeepSeek-Official"
        call_kwargs_on = mock_client.chat.completions.create.call_args_list[1].kwargs
        assert call_kwargs_on["extra_body"] == {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "low",
        }
        assert call_kwargs_on["max_tokens"] == 1500


def test_chat_completion_empty_content_triggers_fallback_and_accurate_badge(monkeypatch):
    """当主模型返回 HTTP 200 但 content 为空时，必须降级到下一级，且模型标识记录实际成功的提供商。"""
    cfg1 = ProviderConfig(name="DeepSeek-Official", api_key="k1", base_url="https://api.deepseek.com", model="deepseek-flash")
    cfg2 = ProviderConfig(name="SiliconFlow", api_key="k2", base_url="https://api.siliconflow.cn/v1", model="deepseek-ai/DeepSeek-V3.2")
    monkeypatch.setattr("src.llm.get_tier_providers", lambda: [cfg1, cfg2])

    client1 = MagicMock()
    resp_empty = MagicMock()
    resp_empty.choices = [MagicMock(message=MagicMock(content="   "), finish_reason="length")]
    client1.chat.completions.create.return_value = resp_empty

    client2 = MagicMock()
    resp_ok = MagicMock()
    resp_ok.choices = [MagicMock(message=MagicMock(content="SiliconFlow 兜底定心丸"), finish_reason="stop")]
    client2.chat.completions.create.return_value = resp_ok

    with patch("src.llm._build_client_from_config", side_effect=lambda cfg, max_retries=0: client1 if cfg.name == "DeepSeek-Official" else client2):
        content, provider = chat_completion_with_fallback(messages=[{"role": "user", "content": "hi"}], thinking=False)
        assert content == "SiliconFlow 兜底定心丸"
        assert provider == "SiliconFlow"
        assert get_last_completion_info() == "SiliconFlow (deepseek-ai/DeepSeek-V3.2)"


def test_circuit_breaker_on_402_skips_provider_in_process(monkeypatch):
    """遇到 401/402/403 错误时，熔断器在本进程内记住并跳过该提供商。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sf-key")

    client_ds = MagicMock()
    client_ds.chat.completions.create.side_effect = RuntimeError("Error code: 402 - Payment Required")

    client_sf = MagicMock()
    resp_ok = MagicMock()
    resp_ok.choices = [MagicMock(message=MagicMock(content="SF 正常响应"), finish_reason="stop")]
    client_sf.chat.completions.create.return_value = resp_ok

    def fake_build(cfg, max_retries=0):
        return client_ds if cfg.name == "DeepSeek-Official" else client_sf

    with patch("src.llm._build_client_from_config", side_effect=fake_build):
        # 第一次调用：DeepSeek 报 402 -> 触发熔断 -> 降级 SiliconFlow 成功
        content1, prov1 = chat_completion_with_fallback(messages=[{"role": "user", "content": "call 1"}])
        assert content1 == "SF 正常响应"
        assert prov1 == "SiliconFlow"
        assert "DeepSeek-Official" in get_tripped_providers()
        assert client_ds.chat.completions.create.call_count == 1

        # 第二次调用：DeepSeek 已在熔断名单中，直接跳过不再发请求
        content2, prov2 = chat_completion_with_fallback(messages=[{"role": "user", "content": "call 2"}])
        assert content2 == "SF 正常响应"
        assert prov2 == "SiliconFlow"
        assert client_ds.chat.completions.create.call_count == 1  # 仍为 1 次，未再请求！
        assert client_sf.chat.completions.create.call_count == 2


def test_chat_completion_with_fallback_all_fail_raises_runtime_error(monkeypatch):
    """所有提供商均失败时，抛出 RuntimeError，供业务层兜底。"""
    cfg1 = ProviderConfig(name="DeepSeek-Official", api_key="k1", base_url="https://api.deepseek.com", model="deepseek-flash")
    monkeypatch.setattr("src.llm.get_tier_providers", lambda: [cfg1])

    client1 = MagicMock()
    client1.chat.completions.create.side_effect = ConnectionError("Network down")

    with patch("src.llm._build_client_from_config", return_value=client1):
        with pytest.raises(RuntimeError, match="所有配置的 LLM 提供商均调用失败"):
            chat_completion_with_fallback(messages=[{"role": "user", "content": "hi"}])


def test_chat_completion_no_keys_raises_runtime_error(monkeypatch):
    """未配置任何 Key 时抛出明确的 RuntimeError。"""
    monkeypatch.setattr("src.llm.get_tier_providers", lambda: [])
    monkeypatch.setattr("src.llm.LLM_API_KEY", "")

    with pytest.raises(RuntimeError, match="未配置任何可用 LLM 提供商的 API Key"):
        chat_completion_with_fallback(messages=[{"role": "user", "content": "hi"}])


def test_llm_helpers_selection(monkeypatch):
    """测试 get_llm_model / get_fallback_llm_model / get_translation_model 的路由选择。"""
    cfg_deepseek = ProviderConfig(name="DeepSeek-Official", api_key="k1", base_url="https://api.deepseek.com", model="deepseek-flash")
    cfg_sf = ProviderConfig(name="SiliconFlow", api_key="k2", base_url="https://api.siliconflow.cn/v1", model="deepseek-ai/DeepSeek-V3.2")

    monkeypatch.setattr("src.llm.get_tier_providers", lambda: [cfg_deepseek, cfg_sf])

    # 主模型优先走 Tier 1 (DeepSeek)
    assert get_llm_model() == "deepseek-flash"
    # 备模型走 Tier 2 (SiliconFlow)
    assert get_fallback_llm_model() == "deepseek-ai/DeepSeek-V3.2"
    # 翻译模型在 DeepSeek 存在时复用 DeepSeek Flash
    assert get_translation_model() == "deepseek-flash"

    # 测试客户端构建
    with patch("src.llm._build_client_from_config") as mock_build:
        get_llm_client()
        assert mock_build.call_count == 1
        assert mock_build.call_args[0][0].name == "DeepSeek-Official"

    with patch("src.llm._build_client_from_config") as mock_build:
        get_fallback_llm_client()
        assert mock_build.call_count == 1
        assert mock_build.call_args[0][0].name == "SiliconFlow"


def test_record_and_get_last_completion_info():
    """测试智囊模型标识记录与读取。"""
    record_completion("DeepSeek-Official", "deepseek-flash")
    assert get_last_completion_info() == "DeepSeek-Official (deepseek-flash)"
