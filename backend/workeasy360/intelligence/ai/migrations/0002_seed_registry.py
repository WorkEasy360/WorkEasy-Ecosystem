"""Seed the model registry: two providers, their models, first prices.

Reference data a fresh deployment needs for AI to work once an operator puts
the API keys in the secret manager (``W360_AI_CREDENTIAL_ANTHROPIC``,
``W360_AI_CREDENTIAL_OPENAI``). Nothing here is a secret: a provider row
holds only the *name* of its credential.

- generation: ``anthropic.claude-opus-5`` active; ``claude-sonnet-5`` and
  ``claude-haiku-4-5`` registered **disabled** - switching a cheaper model on
  is a staff decision, taken in the Super Admin registry, not a default;
- embeddings: ``openai.text-embedding-3-small`` (1,536 dimensions) active -
  the generation vendor offers no embedding endpoint.

Prices are the vendors' published list prices, in integer micro-USD per
million tokens, as version 1. They are estimates for spend control, never an
invoice; a price change is a new version published by staff.

**The spec is frozen here** (the Phase 5 lesson ``authz.0004`` records): the
rows are written from literals, not from code that may later change.
Idempotent - a row that exists is left exactly as it is - and the reverse
removes only seeded rows nothing refers to.
"""

from __future__ import annotations

import uuid

from django.db import migrations
from django.utils import timezone

PROVIDERS = [
    {
        "key": "anthropic",
        "adapter": "anthropic_messages",
        "display_name": "Anthropic",
        "credential_ref": "anthropic",
        "data_region": "",
        "trains_on_inputs": False,
        "data_policy_note": "Commercial API terms: inputs are not used for training.",
    },
    {
        "key": "openai",
        "adapter": "openai",
        "display_name": "OpenAI",
        "credential_ref": "openai",
        "data_region": "",
        "trains_on_inputs": False,
        "data_policy_note": "API terms: inputs are not used for training by default.",
    },
]

#: (key, provider, model_id, name, status, capabilities, context, max output,
#:  embedding dimensions, priority, input micros/MTok, output micros/MTok)
MODELS = [
    (
        "anthropic.claude-opus-5",
        "anthropic",
        "claude-opus-5",
        "Claude Opus 5",
        "active",
        ["long_context", "structured_output", "text"],
        1_000_000,
        16_000,
        None,
        10,
        5_000_000,
        25_000_000,
    ),
    (
        "anthropic.claude-sonnet-5",
        "anthropic",
        "claude-sonnet-5",
        "Claude Sonnet 5",
        "disabled",
        ["long_context", "structured_output", "text"],
        1_000_000,
        16_000,
        None,
        20,
        2_000_000,
        10_000_000,
    ),
    (
        "anthropic.claude-haiku-4-5",
        "anthropic",
        "claude-haiku-4-5",
        "Claude Haiku 4.5",
        "disabled",
        ["structured_output", "text"],
        200_000,
        8_000,
        None,
        30,
        1_000_000,
        5_000_000,
    ),
    (
        "openai.text-embedding-3-small",
        "openai",
        "text-embedding-3-small",
        "OpenAI text-embedding-3-small",
        "active",
        ["embeddings"],
        8_191,
        1,
        1_536,
        10,
        20_000,
        0,
    ),
]


def seed(apps, schema_editor):
    AIProvider = apps.get_model("ai", "AIProvider")
    AIModel = apps.get_model("ai", "AIModel")
    AIModelPrice = apps.get_model("ai", "AIModelPrice")
    now = timezone.now()

    providers = {}
    for spec in PROVIDERS:
        provider, _ = AIProvider.objects.get_or_create(
            key=spec["key"], defaults={"id": uuid.uuid4(), "status": "active", **spec}
        )
        providers[spec["key"]] = provider

    for (
        key,
        provider,
        model_id,
        name,
        status,
        capabilities,
        context,
        max_output,
        dimensions,
        priority,
        input_price,
        output_price,
    ) in MODELS:
        model, created = AIModel.objects.get_or_create(
            key=key,
            defaults={
                "id": uuid.uuid4(),
                "provider": providers[provider],
                "model_id": model_id,
                "display_name": name,
                "status": status,
                "tier": "standard",
                "capabilities": capabilities,
                "context_window_tokens": context,
                "max_output_tokens": max_output,
                "embedding_dimensions": dimensions,
                "priority": priority,
            },
        )
        if created:
            AIModelPrice.objects.create(
                id=uuid.uuid4(),
                model=model,
                version=1,
                currency="USD",
                input_micros_per_mtok=input_price,
                output_micros_per_mtok=output_price,
                effective_from=now,
            )


def unseed(apps, schema_editor):
    AIModel = apps.get_model("ai", "AIModel")
    AIModelPrice = apps.get_model("ai", "AIModelPrice")
    AIProvider = apps.get_model("ai", "AIProvider")
    keys = [spec[0] for spec in MODELS]
    # Price rows are immutable (a trigger refuses UPDATE, not DELETE); usage
    # lines PROTECT a price that was used, so a used model stays.
    for model in AIModel.objects.filter(key__in=keys):
        try:
            AIModelPrice.objects.filter(model=model).delete()
            model.delete()
        except Exception:  # noqa: S112 - a model with recorded usage is kept
            continue
    AIProvider.objects.filter(key__in=[p["key"] for p in PROVIDERS]).exclude(
        id__in=AIModel.objects.values("provider_id")
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("ai", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed, elidable=False)]
