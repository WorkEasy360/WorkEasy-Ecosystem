"""Phase 8 request builders shared by the intelligence suites.

Plain dictionaries in the shape the integration guide documents - what a
product would send - so a test reads as a product's call, not as platform
internals.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.utils import timezone


def summary_skill(**overrides: Any) -> dict[str, Any]:
    """A registrable ``product.example.summary`` skill definition."""
    skill = {
        "key": "product.example.summary",
        "version": 1,
        "description": "Summarise a record using the organization's knowledge.",
        "instructions": "Summarise the record in the input in two sentences.",
        "input_schema": {
            "type": "object",
            "properties": {"record": {"type": "string", "maxLength": 2000}},
            "required": ["record"],
        },
        "output_schema": None,
        "capabilities": [],
        "max_output_tokens": 512,
        "retrieval": {"enabled": True, "top_k": 3},
        "shared_with": [],
    }
    skill.update(overrides)
    return skill


def document(**overrides: Any) -> dict[str, Any]:
    """A ``help_center`` document for the ``example`` product."""
    body = {
        "source": "help_center",
        "document_id": "article-1",
        "version": "1",
        "title": "Returns policy",
        "url": "https://help.example.com/articles/1",
        "content": (
            "Customers may return any item within thirty days of delivery. "
            "Refunds are issued to the original payment method within five days."
        ),
    }
    body.update(overrides)
    return body


def order_metric(**overrides: Any) -> dict[str, Any]:
    """A money metric over ``product.example.record.created`` v1."""
    body = {
        "key": "product.example.record_value",
        "version": 1,
        "description": "Value of records created",
        "unit": "currency",
        "aggregation": "sum",
        "value_type": "money",
        "event": {"type": "product.example.record.created", "version": 1},
        "measure": "amount_minor",
        "currency_field": "currency",
        "dimensions": [
            {"name": "status", "field": "status", "values": ["open", "won", "lost"]}
        ],
        "shared_with": [],
    }
    body.update(overrides)
    return body


def record_event(*, occurred_at: Any = None, **data: Any) -> dict[str, Any]:
    """A ``product.example.record.created`` v1 event carrying ``data``."""
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "product.example.record.created",
        "event_version": 1,
        "occurred_at": (occurred_at or timezone.now()).isoformat(),
        "data": {"amount_minor": 1000, "currency": "GBP", "status": "open", **data},
    }
