"""L1 - the shared kernel. Tenant-aware, product-agnostic.

Internally layered P0->P3; a module may depend only on strictly lower layers.
Siblings communicate by events, never by import (F-5).
"""
