from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

OutputModelT = TypeVar("OutputModelT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0

    def __add__(self, other: ProviderUsage) -> ProviderUsage:
        return ProviderUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_output_tokens=(self.reasoning_output_tokens + other.reasoning_output_tokens),
        )


@dataclass(frozen=True, slots=True)
class StructuredProviderResult(Generic[OutputModelT]):
    output: OutputModelT
    thread_id: str
    usage: ProviderUsage
    events: tuple[dict[str, Any], ...]
    duration_seconds: float
    stderr: str = ""
