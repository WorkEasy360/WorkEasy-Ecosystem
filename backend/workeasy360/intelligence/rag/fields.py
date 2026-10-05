"""A pgvector column, without a pgvector client library.

The column is the untyped ``vector``: dimensions are recorded per row
(``KnowledgeChunk.embedding_dimensions``) beside the model that produced them,
so the schema is not welded to one provider's model (Task 29). Similarity is
only ever computed between vectors of the *same* model - the retrieval query
filters on the model key before it orders by distance - so rows of two
dimensions never meet in one comparison.

Values travel in pgvector's text form (``[0.1,0.2,...]``); the database does
the arithmetic. The application almost never reads a vector back: retrieval
orders by distance in SQL and returns text and provenance.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from django.db import models


def to_literal(values: Iterable[float]) -> str:
    """pgvector's text form. Refuses non-finite values: a NaN in an index
    poisons every distance it is part of."""
    parts = []
    for value in values:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("A vector component must be finite.")
        parts.append(repr(number))
    if not parts:
        raise ValueError("A vector must have at least one component.")
    return "[" + ",".join(parts) + "]"


class VectorField(models.Field):
    description = "pgvector vector (dimension recorded alongside)"

    def db_type(self, connection: Any) -> str:
        return "vector"

    def get_prep_value(self, value: Any) -> Any:
        if value is None or isinstance(value, str):
            return value
        return to_literal(value)

    def from_db_value(self, value: Any, expression: Any, connection: Any) -> Any:
        if value is None:
            return None
        body = str(value).strip().strip("[]")
        return tuple(float(part) for part in body.split(",")) if body else ()
