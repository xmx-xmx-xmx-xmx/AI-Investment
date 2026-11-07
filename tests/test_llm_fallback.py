"""
测试多源大模型三级降级机制与客户端工厂 (src/llm.py)。
覆盖：
1. 提供商优先级排序 (Custom Tier 0 -> Gemini Tier 1 -> DeepSeek Tier 2 -> SiliconFlow Tier 3)
2. chat_completion_with_fallback 阶梯级联降级 (Tier 1 失败 -> 尝试 Tier 2 -> 尝试 Tier 3 -> 全挂抛 RuntimeError)
3. 动态模型获取与翻译模型自适应
"""

from unittest.mock import MagicMock, patch
import pytest

from src.llm import (
    ProviderConfig,
    chat_completion_with_fallback,
    get_fallback_llm_client,
    get_fallback_llm_model,
    get_llm_client,
    get_llm_model,
    get_tier_providers,
    get_translation_model,
)


def test_get_tier_providers_empty(monkeypatch):
    """未设置任何 Key 时返回空列表。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)

    providers = get_tier_providers()
    assert providers == []


def test_get_tier_providers_ordering(monkeypatch):
    """测试多 Key 存在时的优先级排序：Custom -> Gemini -> DeepSeek -> SiliconFlow。"""
    monkeypatch.setenv("LLM_API_KEY", "custom-sk-123")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-sk-456")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-sk-789")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sf-sk-abc")

    providers = get_tier_providers()
    names = [p.name for p in providers]
    assert names == ["Custom-OpenAI", "Google-Gemini", "DeepSeek-Official", "SiliconFlow"]
    assert providers[1].model == "gemini-2.5-flash"
    assert providers[2].model == "deepseek-chat"
    assert providers[3].model == "deepseek-ai/DeepSeek-V3.2"


def test_get_tier_providers_google_key_alias(monkeypatch):
    """支持 GOOGLE_API_KEY 别名。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "g-sk-alias")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)

    providers = get_tier_providers()
    assert len(providers) == 1
    assert providers[0].name == "Google-Gemini"
    assert providers[0].api_key == "g-sk-alias"


def test_chat_completion_with_fallback_tier1_success(monkeypatch):
    """Tier 1 成功时直接返回，不触发后续降级。"""
    cfg1 = ProviderConfig(name="Google-Gemini", api_key="k1", base_url="https://gemini.com", model="gemini-2.5-flash")
    cfg2 = ProviderConfig(name="DeepSeek-Official", api_key="k2", base_url="https://deepseek.com", model="deepseek-chat")
    monkeypatch.setattr("src.llm.get_tier_providers", lambda: [cfg1, cfg2])

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="Gemini 定心丸"))]
    mock_client.chat.completions.create.return_value = mock_resp

    with patch("src.llm._build_client_from_config", return_value=mock_client):
        content, provider = chat_completion_with_fallback(messages=[{"role": "user", "content": "hi"}])
        assert content == "Gemini 定心丸"
        assert provider == "Google-Gemini"
        assert mock_client.chat.completions.create.call_count == 1


def test_chat_completion_with_fallback_tier1_fails_tier2_succeeds(monkeypatch):
    """Tier 1 失败，降级到 Tier 2 成功。"""
    cfg1 = ProviderConfig(name="Google-Gemini", api_key="k1", base_url="https://gemini.com", model="gemini-2.5-flash")
    cfg2 = ProviderConfig(name="DeepSeek-Official", api_key="k2", base_url="https://deepseek.com", model="deepseek-chat")
    monkeypatch.setattr("src.llm.get_tier_providers", lambda: [cfg1, cfg2])

    client1 = MagicMock()
    client1.chat.completions.create.side_effect = TimeoutError("Gemini timed out")

    client2 = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="DeepSeek 定心丸"))]
    client2.chat.completions.create.return_value = mock_resp

    def fake_build(cfg, max_retries=0):
        if cfg.name == "Google-Gemini":
            return client1
        return client2

    with patch("src.llm._build_client_from_config", side_effect=fake_build):
        content, provider = chat_completion_with_fallback(messages=[{"role": "user", "content": "hi"}])
        assert content == "DeepSeek 定心丸"
        assert provider == "DeepSeek-Official"
        assert client1.chat.completions.create.call_count == 1
        assert client2.chat.completions.create.call_count == 1


def test_chat_completion_with_fallback_all_fail_raises_runtime_error(monkeypatch):
    """所有提供商均失败时，抛出 RuntimeError，供业务层兜底。"""
    cfg1 = ProviderConfig(name="Google-Gemini", api_key="k1", base_url="https://gemini.com", model="gemini-2.5-flash")
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

    with pytest.raises(RuntimeError, match="未配置任何 LLM 提供商的 API Key"):
        chat_completion_with_fallback(messages=[{"role": "user", "content": "hi"}])


def test_llm_helpers_selection(monkeypatch):
    """测试 get_llm_model / get_fallback_llm_model / get_translation_model 的路由选择。"""
    cfg_gemini = ProviderConfig(name="Google-Gemini", api_key="k1", base_url="https://gemini.com", model="gemini-2.5-flash")
    cfg_deepseek = ProviderConfig(name="DeepSeek-Official", api_key="k2", base_url="https://deepseek.com", model="deepseek-chat")

    monkeypatch.setattr("src.llm.get_tier_providers", lambda: [cfg_gemini, cfg_deepseek])

    # 主模型优先走 Tier 1
    assert get_llm_model() == "gemini-2.5-flash"
    # 备模型走 Tier 2
    assert get_fallback_llm_model() == "deepseek-chat"
    # 翻译模型在 Gemini 存在时复用 Gemini Flash
    assert get_translation_model() == "gemini-2.5-flash"

    # 测试客户端构建
    with patch("src.llm._build_client_from_config") as mock_build:
        get_llm_client()
        assert mock_build.call_count == 1
        assert mock_build.call_args[0][0].name == "Google-Gemini"

    with patch("src.llm._build_client_from_config") as mock_build:
        get_fallback_llm_client()
        assert mock_build.call_count == 1
        assert mock_build.call_args[0][0].name == "DeepSeek-Official"
