"""P0 - tenant context, scoping, RLS plumbing, task propagation.

Owns TenantContext and TenantScopedModel. Does NOT own the Organization
model itself - that belongs to platform/organizations (P1).
"""
