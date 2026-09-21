"""Domain event envelope, schemas and the type registry.

Schemas live here rather than inside the publishing module so that a consumer
never imports a publisher. That single placement decision is what keeps the
event graph acyclic at the code level (02-dependency-rules.md section 5).
"""
