"""OpenAI adapter: embeddings (``/v1/embeddings``) and chat generation
(``/v1/chat/completions``).

Embeddings are why this adapter exists - the default generation vendor has no
embedding endpoint. Generation is here so a second vendor exists for a
fallback an organization has explicitly permitted (Task 52); it is never used
otherwise.

Direct HTTP through ``integration/egress``, for the reason the Anthropic
adapter gives.
"""

from __future__ import annotations

import math
from typing import Any, Final

from workeasy360.intelligence.ai.providers.contract import (
    EmbeddingRequest,
    EmbeddingResult,
    GenerationRequest,
    GenerationResult,
    HttpCall,
    ProviderFailure,
    failure_for_status,
    json_body,
    parse_json,
    token_count,
)

_FINISH: Final[dict[str, str]] = {
    "stop": "end",
    "length": "max_tokens",
    "content_filter": "refusal",
}


class OpenAIAdapter:
    key = "openai"
    default_endpoint = "https://api.openai.com"
    operations = frozenset({"generation", "embedding"})

    def generation_call(self, request: GenerationRequest, api_key: str) -> HttpCall:
        payload: dict[str, Any] = {
            "model": request.model_id,
            "max_completion_tokens": request.max_output_tokens,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
        }
        if request.output_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "skill_output",
                    "schema": request.output_schema,
                    "strict": False,
                },
            }
        return HttpCall(
            path="/v1/chat/completions",
            headers=_headers(api_key),
            body=json_body(payload),
        )

    def parse_generation(
        self, status: int, headers: dict[str, str], body: bytes
    ) -> GenerationResult:
        if not 200 <= status < 300:
            raise failure_for_status(status, headers, body)
        data = parse_json(body)
        choices = data.get("choices")
        if (
            not isinstance(choices, list)
            or not choices
            or not isinstance(choices[0], dict)
        ):
            raise ProviderFailure("invalid_response")
        choice = choices[0]
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        stop = _FINISH.get(str(choice.get("finish_reason")), "other")
        if message.get("refusal"):
            stop = "refusal"
        content = message.get("content")
        return GenerationResult(
            text=content if isinstance(content, str) else "",
            input_tokens=token_count(usage.get("prompt_tokens")),
            output_tokens=token_count(usage.get("completion_tokens")),
            stop=stop,
        )

    def embedding_call(self, request: EmbeddingRequest, api_key: str) -> HttpCall:
        payload: dict[str, Any] = {
            "model": request.model_id,
            "input": list(request.texts),
            "encoding_format": "float",
        }
        if request.dimensions is not None:
            payload["dimensions"] = request.dimensions
        return HttpCall(
            path="/v1/embeddings", headers=_headers(api_key), body=json_body(payload)
        )

    def parse_embedding(
        self, status: int, headers: dict[str, str], body: bytes, expected: int
    ) -> EmbeddingResult:
        if not 200 <= status < 300:
            raise failure_for_status(status, headers, body)
        data = parse_json(body)
        rows = data.get("data")
        if not isinstance(rows, list) or len(rows) != expected:
            raise ProviderFailure("invalid_response")
        by_index: dict[int, tuple[float, ...]] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ProviderFailure("invalid_response")
            index = row.get("index")
            vector = row.get("embedding")
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or not isinstance(vector, list)
                or not vector
            ):
                raise ProviderFailure("invalid_response")
            by_index[index] = _vector(vector)
        if sorted(by_index) != list(range(expected)):
            raise ProviderFailure("invalid_response")
        vectors = tuple(by_index[i] for i in range(expected))
        if len({len(v) for v in vectors}) != 1:
            raise ProviderFailure("invalid_response")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return EmbeddingResult(
            vectors=vectors, input_tokens=token_count(usage.get("prompt_tokens"))
        )


def _headers(api_key: str) -> tuple[tuple[str, str], ...]:
    return (
        ("Content-Type", "application/json"),
        ("Authorization", f"Bearer {api_key}"),
    )


def _vector(values: list[Any]) -> tuple[float, ...]:
    out: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ProviderFailure("invalid_response")
        number = float(value)
        if not math.isfinite(number):
            raise ProviderFailure("invalid_response")
        out.append(number)
    return tuple(out)
