from pathlib import Path

import pytest

from docx_agent.agent import models
from docx_agent.agent.models import Settings, create_model


def test_settings_dotenv_and_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "DOCX_AGENT_PROVIDER=anthropic\nDOCX_AGENT_MODEL=example-model\n"
        "ANTHROPIC_API_KEY=fake-key\nUNRELATED=ignored\n"
    )
    monkeypatch.setenv("DOCX_AGENT_MODEL", "env-override")
    settings = Settings(_env_file=dotenv)
    assert settings.model_name == "env-override"
    assert settings.provider == "anthropic"
    assert "fake-key" not in repr(settings)


@pytest.mark.parametrize("provider", ["google_genai", "anthropic"])
def test_create_model_config_without_api_calls(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def client(**kwargs):
        captured.update(kwargs)
        return "offline sentinel"

    monkeypatch.setattr(models, "ChatGoogleGenerativeAI", client)
    monkeypatch.setattr(models, "ChatAnthropic", client)
    settings = Settings(
        _env_file=None,
        provider=provider,
        model="example-model",
        GEMINI_API_KEY="fake-key",
        ANTHROPIC_API_KEY="fake-key",
    )
    assert create_model(settings) == "offline sentinel"
    assert captured["model"] == "example-model"
    assert captured["max_retries"] == 0
    assert captured["timeout"] == 60.0
    assert "tool_choice" not in captured
    assert "temperature" not in captured
    if provider == "google_genai":
        assert captured["vertexai"] is False
        assert captured["thinking_level"] == "medium"
    else:
        assert captured["reasoning_effort"] == "medium"


@pytest.mark.parametrize(
    ("provider", "key"), [("google_genai", "GEMINI"), ("anthropic", "ANTHROPIC")]
)
def test_missing_provider_key(provider: str, key: str) -> None:
    settings = Settings(_env_file=None, provider=provider, GEMINI_API_KEY="", ANTHROPIC_API_KEY="")
    with pytest.raises(ValueError, match=f"Set {key}_API_KEY"):
        create_model(settings)


def test_default_model_selection() -> None:
    assert (
        Settings(_env_file=None, provider="anthropic", model=None).model_name == "claude-sonnet-5-5"
    )
    settings = Settings(
        _env_file=None, provider="google_genai", model="", GEMINI_MODELS=" , first,second"
    )
    assert settings.model_name == "first"
    settings.gemini_models = " , "
    with pytest.raises(ValueError, match="must contain a model ID"):
        _ = settings.model_name
