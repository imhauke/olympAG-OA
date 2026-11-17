"""Explicit provider configuration; constructing a client never runs a model."""

from typing import Literal, Self

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOCX_AGENT_", env_file=".env", extra="ignore")
    provider: Literal["google_genai", "anthropic"] = "google_genai"
    model: str | None = None
    effort: Literal["low", "medium", "high"] = "medium"
    max_retries: int = Field(default=2, ge=0, le=2)
    max_turns: int = Field(default=30, ge=1, le=100)
    gemini_api_key: SecretStr | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    gemini_models: str = Field(default="gemini-3.5-flash-lite", validation_alias="GEMINI_MODELS")

    def for_provider(self, provider: Literal["google_genai", "anthropic"]) -> Self:
        """Same settings for another provider; a configured model ID only applies to its own."""
        if provider == self.provider:
            return self
        return self.model_copy(update={"provider": provider, "model": None})

    def available_providers(self) -> list[Literal["google_genai", "anthropic"]]:
        keys: dict[Literal["google_genai", "anthropic"], SecretStr | None] = {
            "google_genai": self.gemini_api_key,
            "anthropic": self.anthropic_api_key,
        }
        return [p for p, key in keys.items() if key is not None and key.get_secret_value()]

    @property
    def model_name(self) -> str:
        if self.model and self.model.strip():
            return self.model.strip()
        if self.provider == "anthropic":
            return "claude-sonnet-5-5"
        candidates = [name.strip() for name in self.gemini_models.split(",") if name.strip()]
        if not candidates:
            raise ValueError("GEMINI_MODELS must contain a model ID, or set DOCX_AGENT_MODEL.")
        return candidates[0]


def create_model(settings: Settings) -> BaseChatModel:
    """Disable implicit SDK retries so the graph bounds billable calls explicitly."""
    if settings.provider == "anthropic":
        if not settings.anthropic_api_key or not settings.anthropic_api_key.get_secret_value():
            raise ValueError("Set ANTHROPIC_API_KEY for the anthropic provider.")
        return ChatAnthropic(
            model=settings.model_name,
            api_key=settings.anthropic_api_key,
            max_tokens=4096,
            timeout=60.0,
            max_retries=0,
            reasoning_effort=settings.effort,
        )
    if not settings.gemini_api_key or not settings.gemini_api_key.get_secret_value():
        raise ValueError("Set GEMINI_API_KEY for the google_genai provider.")
    return ChatGoogleGenerativeAI(
        model=settings.model_name,
        api_key=settings.gemini_api_key,
        vertexai=False,
        timeout=60.0,
        max_retries=0,
        thinking_level=settings.effort,
    )
