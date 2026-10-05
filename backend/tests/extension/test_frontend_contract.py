"""The Phase 8 extension's frontend clients call only routes the API serves.

The Phase 7 architecture suite pins ``controlPlane.ts``; the extension added two
more clients (``console.ts`` for the organization console and account pages,
``adminConsole.ts`` for the Super Admin plan builder, payments, users and
communication). A renamed or removed backend route must fail here, not in a
customer's browser.
"""

from __future__ import annotations

import pathlib
import re
import uuid

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
API = REPO_ROOT / "frontend" / "src" / "lib" / "api"
CLIENTS = ("console.ts", "adminConsole.ts")
_SAMPLE_ID = str(uuid.uuid4())
_SAMPLE_SLUG = "sample-org"


def _paths(client: str) -> list[str]:
    source = (API / client).read_text(encoding="utf-8")
    found: list[str] = []
    for match in re.finditer(r"`([^`]*)`|\"(/api/v1/[^\"]*)\"", source):
        raw = match.group(1) if match.group(1) is not None else match.group(2)
        # The ``org(slug)`` helper's own definition is a prefix, not a route.
        if re.fullmatch(r"/api/v1/orgs/\$\{[^}]*\}", raw):
            continue
        raw = re.sub(r"\$\{org\([^)]*\)\}", f"/api/v1/orgs/{_SAMPLE_SLUG}", raw)
        raw = re.sub(r"\$\{[^}]*\}", _SAMPLE_ID, raw)
        if raw.startswith("/api/v1/") and raw != f"/api/v1/orgs/{_SAMPLE_SLUG}":
            found.append(raw.split("?")[0])
    return found


@pytest.mark.skipif(not API.exists(), reason="no frontend in this checkout")
@pytest.mark.parametrize("client", CLIENTS)
def test_the_parser_finds_the_clients_paths(client):
    assert len(_paths(client)) > 20, client


@pytest.mark.skipif(not API.exists(), reason="no frontend in this checkout")
@pytest.mark.parametrize("client", CLIENTS)
def test_every_path_an_extension_client_requests_is_a_real_route(client):
    from django.urls import Resolver404, resolve

    missing = []
    for path in sorted(set(_paths(client))):
        try:
            resolve(path)
        except Resolver404:
            missing.append(path)
    assert not missing, f"{client} calls routes the API does not serve:\n" + "\n".join(
        missing
    )
