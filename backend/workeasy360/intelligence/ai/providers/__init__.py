"""AI provider adapters: the narrow contract, and the registry of adapters.

An adapter translates one of two platform requests - *generate text* or
*embed texts* - into one vendor's HTTP request, and that vendor's response or
error back. That is all it does (Task 3):

- it never opens a socket: ``transport`` sends what it builds through
  ``integration/egress`` (pinned DNS, TLS, byte caps, deadline);
- it never reads a setting, a credential store or the database: it is handed
  the API key for one call, as an argument (import-linter
  ``ai-providers-are-sandboxed``);
- it offers no tools and no function calling - the platform gives models no
  actions (Task 16), so there is nothing for a request to carry but text.

Two adapters exist because two are what prove the architecture: one vendor for
generation, one for embeddings (the first has no embedding API), and a second
generation vendor for an explicitly permitted fallback. "Build every
provider" is not the brief.
"""

from __future__ import annotations

from workeasy360.intelligence.ai.providers.anthropic import AnthropicMessagesAdapter
from workeasy360.intelligence.ai.providers.contract import Adapter
from workeasy360.intelligence.ai.providers.openai import OpenAIAdapter

_ADAPTERS: dict[str, Adapter] = {
    adapter.key: adapter for adapter in (AnthropicMessagesAdapter(), OpenAIAdapter())
}


def adapter_for(key: str) -> Adapter | None:
    return _ADAPTERS.get(key)
