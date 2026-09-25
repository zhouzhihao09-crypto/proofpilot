"""LLM provider abstraction.

The system talks to language models through a small, well-defined
interface so the provider can be swapped later (Ollama, OpenAI, etc.).
"""

from __future__ import annotations

import abc
from typing import Optional, Sequence

from pydantic import BaseModel, Field


class LLMRequest(BaseModel):
    """A request to a language model."""

    prompt: str
    system: Optional[str] = None
    temperature: float = 0.0
    max_tokens: Optional[int] = None


class LLMResponse(BaseModel):
    """A response from a language model."""

    content: str
    model: str = "unknown"
    usage: dict[str, int] = Field(default_factory=dict)


class LLMProvider(abc.ABC):
    """Interface for language model providers."""

    name: str = "base"

    @abc.abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse:
        """Run a completion request and return a structured response."""
        raise NotImplementedError

    def structured(self, request: LLMRequest, schema: type[BaseModel]) -> BaseModel:
        """Run a completion and parse the result into a Pydantic model.

        Default implementation: parse JSON from the text response. Providers
        with native structured-output support should override this.
        """
        import json

        response = self.complete(request)
        text = response.content.strip()
        # Strip markdown fences if present.
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM did not return valid JSON: {text[:200]}") from exc
        return schema.model_validate(data)


# Re-exported here so structured() can use re without a top-level import.
import re  # noqa: E402


class NoopLLMProvider(LLMProvider):
    """A deterministic provider used for tests and offline runs.

    It does not generate claims; it echoes the request so that the
    pipeline can be exercised without an external model.
    """

    name = "noop"

    def __init__(self, model: str = "noop-1.0") -> None:
        self.model = model

    def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            content=f"noop echo: {request.prompt[:200]}",
            model=self.model,
            usage={"prompt_tokens": 0, "completion_tokens": 0},
        )