"""
Vendor-neutral LLM provider abstraction.

The app keeps provider selection in settings and routes all insight generation
through a common async interface so Prompt 11 stays provider-agnostic.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urljoin

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMResponse:
    """Normalized completion payload returned by an LLM provider."""

    content: str
    tokens_used: Optional[int] = None
    model: Optional[str] = None
    finish_reason: Optional[str] = None


class LLMProvider(ABC):
    """Abstract interface for all supported LLM providers."""

    @abstractmethod
    async def generate(
        self,
        *,
        user_prompt: str,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        """Return a completion for the supplied prompts."""

    @abstractmethod
    async def close(self) -> None:
        """Release any provider resources."""


class HTTPXLLMProvider(LLMProvider):
    """Base class for providers backed by an `httpx.AsyncClient`."""

    def __init__(self, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self, *, headers: dict[str, str]) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds),
                headers=headers,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class AnthropicProvider(HTTPXLLMProvider):
    """Anthropic Messages API implementation."""

    API_URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str | None, timeout_seconds: int) -> None:
        super().__init__(timeout_seconds)
        self.api_key = api_key
        self.model = model or "claude-3-5-sonnet-latest"

    async def generate(
        self,
        *,
        user_prompt: str,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        client = await self._get_client(
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        )
        response = await client.post(
            self.API_URL,
            json={
                "model": self.model,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        response.raise_for_status()
        payload = response.json()
        content = "".join(
            block.get("text", "")
            for block in payload.get("content", [])
            if block.get("type") == "text"
        )
        usage = payload.get("usage", {})
        tokens_used = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        return LLMResponse(
            content=content.strip(),
            tokens_used=tokens_used or None,
            model=payload.get("model", self.model),
            finish_reason=payload.get("stop_reason"),
        )


class OpenAIProvider(HTTPXLLMProvider):
    """OpenAI-compatible chat completions implementation."""

    DEFAULT_BASE_URL = "https://api.openai.com/v1/"

    def __init__(
        self,
        api_key: str,
        model: str | None,
        timeout_seconds: int,
        *,
        base_url: str | None = None,
    ) -> None:
        super().__init__(timeout_seconds)
        self.api_key = api_key
        self.model = model or "gpt-4o-mini"
        normalized_base = (base_url or self.DEFAULT_BASE_URL).rstrip("/") + "/"
        self.api_url = urljoin(normalized_base, "chat/completions")

    async def generate(
        self,
        *,
        user_prompt: str,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        client = await self._get_client(
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "content-type": "application/json",
            }
        )
        response = await client.post(
            self.api_url,
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        response.raise_for_status()
        payload = response.json()
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            content = "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        usage = payload.get("usage", {})
        return LLMResponse(
            content=str(content).strip(),
            tokens_used=usage.get("total_tokens"),
            model=payload.get("model", self.model),
            finish_reason=choice.get("finish_reason"),
        )


class GeminiProvider(OpenAIProvider):
    """Google Gemini via the OpenAI-compatible endpoint."""

    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
    DEFAULT_MODEL = "gemini-2.5-flash"

    def __init__(
        self,
        api_key: str,
        model: str | None,
        timeout_seconds: int,
        *,
        base_url: str | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model or self.DEFAULT_MODEL,
            timeout_seconds=timeout_seconds,
            base_url=base_url or self.DEFAULT_BASE_URL,
        )


class AzureOpenAIProvider(HTTPXLLMProvider):
    """Azure OpenAI chat completions implementation."""

    API_VERSION = "2024-02-15-preview"

    def __init__(
        self,
        api_key: str,
        deployment: str | None,
        timeout_seconds: int,
        *,
        endpoint: str | None,
    ) -> None:
        super().__init__(timeout_seconds)
        self.api_key = api_key
        self.deployment = deployment or ""
        self.endpoint = (endpoint or "").rstrip("/")

    async def generate(
        self,
        *,
        user_prompt: str,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        if not self.endpoint or not self.deployment:
            raise ValueError("Azure OpenAI requires both llm_azure_endpoint and llm_model")

        client = await self._get_client(
            headers={
                "api-key": self.api_key,
                "content-type": "application/json",
            }
        )
        api_url = (
            f"{self.endpoint}/openai/deployments/{self.deployment}/chat/completions"
            f"?api-version={self.API_VERSION}"
        )
        response = await client.post(
            api_url,
            json={
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        response.raise_for_status()
        payload = response.json()
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return LLMResponse(
            content=str(message.get("content", "")).strip(),
            tokens_used=(payload.get("usage") or {}).get("total_tokens"),
            model=self.deployment,
            finish_reason=choice.get("finish_reason"),
        )


def get_llm_provider(settings: Settings | None = None) -> LLMProvider | None:
    """
    Return the configured provider instance or None when LLM is unavailable.

    This keeps LLM access opt-in and allows the AI insight layer to fall back
    to rule-based summaries when no provider is configured.
    """

    resolved_settings = settings or get_settings()

    if not resolved_settings.llm_enabled:
        logger.info("LLM support is disabled via settings.")
        return None

    if not resolved_settings.llm_api_key:
        logger.info("LLM provider not configured because no API key is available.")
        return None

    provider_name = (resolved_settings.llm_provider or "openai").strip().lower()
    common_args: dict[str, Any] = {
        "api_key": resolved_settings.llm_api_key,
        "model": resolved_settings.llm_model,
        "timeout_seconds": resolved_settings.llm_timeout_seconds,
    }

    if provider_name == "anthropic":
        return AnthropicProvider(**common_args)
    if provider_name in {"openai", "openai-compatible"}:
        return OpenAIProvider(
            **common_args,
            base_url=resolved_settings.llm_base_url,
        )
    if provider_name in {"gemini", "google"}:
        return GeminiProvider(
            **common_args,
            base_url=resolved_settings.llm_base_url,
        )
    if provider_name in {"deepseek"}:
        return OpenAIProvider(
            **common_args,
            base_url="https://api.deepseek.com/v1/",
        )
    if provider_name in {"azure", "azure-openai"}:
        return AzureOpenAIProvider(
            api_key=resolved_settings.llm_api_key,
            deployment=resolved_settings.llm_model,
            timeout_seconds=resolved_settings.llm_timeout_seconds,
            endpoint=resolved_settings.llm_azure_endpoint or resolved_settings.llm_base_url,
        )

    logger.warning("Unsupported llm_provider=%s. Falling back to rule-based insights.", provider_name)
    return None
