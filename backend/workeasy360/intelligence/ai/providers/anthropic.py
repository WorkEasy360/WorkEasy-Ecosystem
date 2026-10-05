"""Anthropic Messages API adapter (generation only).

``POST /v1/messages`` with ``x-api-key`` and ``anthropic-version``; one system
block (the trusted instructions) and one user turn (the delimited, untrusted
content). When the skill has an output contract and the model supports it,
the request asks for native JSON output (``output_config.format``) - which
reduces invalid outputs but is never trusted: the gateway validates the
result against the full contract regardless (Task 19).

Direct HTTP rather than the vendor SDK, deliberately: every outbound request
in this platform goes through ``integration/egress`` (resolve once, pin,
TLS, deadline, byte caps - D9), and an SDK brings its own HTTP stack that
would resolve and connect on its own terms. The request is small and
documented; the adapter is the whole integration.
"""

from __future__ import annotations

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

API_VERSION: Final = "2023-06-01"

_STOP: Final[dict[str, str]] = {
    "end_turn": "end",
    "stop_sequence": "end",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


class AnthropicMessagesAdapter:
    key = "anthropic_messages"
    default_endpoint = "https://api.anthropic.com"
    operations = frozenset({"generation"})

    def generation_call(self, request: GenerationRequest, api_key: str) -> HttpCall:
        payload: dict[str, Any] = {
            "model": request.model_id,
            "max_tokens": request.max_output_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
        }
        if request.output_schema is not None:
            payload["output_config"] = {
                "format": {"type": "json_schema", "schema": request.output_schema}
            }
        return HttpCall(
            path="/v1/messages",
            headers=(
                ("Content-Type", "application/json"),
                ("x-api-key", api_key),
                ("anthropic-version", API_VERSION),
            ),
            body=json_body(payload),
        )

    def parse_generation(
        self, status: int, headers: dict[str, str], body: bytes
    ) -> GenerationResult:
        if not 200 <= status < 300:
            raise failure_for_status(status, headers, body)
        data = parse_json(body)
        content = data.get("content")
        if not isinstance(content, list):
            raise ProviderFailure("invalid_response")
        # Only text blocks carry the answer; thinking blocks (adaptive
        # thinking is on by default for current models) are billed as output
        # tokens but are not part of it.
        text = "".join(
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return GenerationResult(
            text=text,
            input_tokens=token_count(usage.get("input_tokens")),
            output_tokens=token_count(usage.get("output_tokens")),
            stop=_STOP.get(str(data.get("stop_reason")), "other"),
        )

    def embedding_call(self, request: EmbeddingRequest, api_key: str) -> HttpCall:
        # Anthropic offers no embedding endpoint; the registry never marks one
        # of its models with the ``embeddings`` capability.
        raise ProviderFailure("bad_request")

    def parse_embedding(
        self, status: int, headers: dict[str, str], body: bytes, expected: int
    ) -> EmbeddingResult:
        raise ProviderFailure("bad_request")
