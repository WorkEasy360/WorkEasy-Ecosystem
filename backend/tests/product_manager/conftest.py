"""Product Manager fixtures (ADR-051): signed-in staff, a customer for the
"not authorized" cases, and the images a listing is built from.

The earlier phases' fixtures are reused as they are - the Product Manager adds
screens and a few catalog fields, not a second catalog.
"""

from __future__ import annotations

import json
import struct
import zlib

import pytest

from tests.extension import conftest as _extension
from tests.extension.conftest import staff_scope  # noqa: F401 - helper, not a fixture

console_org = _extension.console_org
login = _extension.login
staff_login = _extension.staff_login

MANAGE = ("platform.staff_catalog.manage", "platform.staff_integration.manage")
SUPER = (*MANAGE, "platform.staff_audit.view")
API = "/api/v1/admin/products"


def png(width: int, height: int) -> bytes:
    """A real, minimal PNG of the given size (decodable by any viewer)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    rows = b"".join(b"\x00" + b"\x10\x80\x50" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def jpeg(width: int, height: int) -> bytes:
    """Enough of a baseline JPEG to carry its size in a start-of-frame."""
    app0 = (
        b"\xff\xe0"
        + struct.pack(">H", 16)
        + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    )
    sof = (
        b"\xff\xc0"
        + struct.pack(">HBHHB", 17, 8, height, width, 3)
        + b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    )
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def body(**overrides):
    """What the Add Product wizard sends for a complete product."""
    base = {
        "name": "Orchard Planner",
        "slug": "orchard-planner",
        "category": "Project Management",
        "tagline": "Plan seasons, crews and harvests.",
        "description": "**Orchard Planner** keeps every season on one board.",
        "store_title": "",
        "store_summary": "Seasons, crews and harvests on one board.",
        "marketing_url": "https://orchard.workeasy360.com/about",
        "documentation_url": "https://docs.workeasy360.com/orchard",
        "support_url": "https://support.workeasy360.com/orchard",
        "application_url": "https://orchard.workeasy360.com",
        "customer_store_enabled": True,
        "reseller_enabled": True,
        "affiliate_enabled": False,
        "white_label_enabled": False,
        "integration": {
            "launch_url": "https://orchard.workeasy360.com/",
            "sso_enabled": True,
            "redirect_uri": "https://orchard.workeasy360.com/api/auth/callback",
        },
        "trial": {"enabled": True, "duration_days": 7},
    }
    base.update(overrides)
    return base


def post(client, path, payload=None):
    return client.post(path, json.dumps(payload or {}), content_type="application/json")


def patch(client, path, payload):
    return client.patch(path, json.dumps(payload), content_type="application/json")


def upload(
    client, product_id, data, *, kind="logo", content_type="image/png", caption=""
):
    query = f"?kind={kind}" + (f"&caption={caption}" if caption else "")
    return client.post(
        f"{API}/{product_id}/media{query}", data, content_type=content_type
    )


@pytest.fixture
def manager(staff_login):
    """A Super Admin: catalog, integration and audit grants."""
    client, user = staff_login(*SUPER)
    return client
