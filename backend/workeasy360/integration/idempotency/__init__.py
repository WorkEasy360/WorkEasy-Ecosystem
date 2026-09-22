"""Command-level idempotency for externally-triggerable operations.

Separate from ``integration/events``' ``ProcessedEvent``, which dedupes
*event deliveries* by (consumer, event_id). This dedupes *commands* by a
caller-supplied key and, unlike event dedupe, **returns the original result**
so a retried "activate subscription" answers with the subscription that was
already created rather than raising.
"""
