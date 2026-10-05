"""Gunicorn configuration for the Workeasy360 API container (Phase 12).

Synchronous WSGI with threads (``gthread``): every view is synchronous and the
ORM is synchronous (``config/asgi.py`` explains why ASGI buys nothing here), so
a thread per in-flight request is the honest model. Every value that depends on
the host's size is an environment variable with a conservative default - the
right worker count is a measurement (docs/phase-12/07-load-test.md), not a
guess baked into the image.

Database connections: ``CONN_MAX_AGE=0`` behind PgBouncer, so a request holds a
server connection only for its transaction. Concurrent requests per container
= workers x threads, and that product - not the worker count - is what must fit
inside PgBouncer's ``max_client_conn``.
"""

from __future__ import annotations

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
worker_class = "gthread"
workers = int(os.environ.get("GUNICORN_WORKERS", "3"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))

# Longer than the database statement timeout (10 s) and the slowest bounded
# outbound call on a request path (egress total 15 s), so a request is ended by
# the layer that knows why, not killed mid-transaction by the arbiter.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "30"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "5"))

# Recycle workers periodically: a bounded defence against slow memory growth in
# any dependency, at the cost of one fork per few thousand requests.
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", "5000"))
max_requests_jitter = int(os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", "500"))

# The request log is RequestLogMiddleware's structured ``http.request`` line
# (request id, status, duration, no query string, no headers). Gunicorn's own
# access log would duplicate it and print raw request lines - including query
# strings - so it stays off.
accesslog = None
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")

# Heartbeat files in memory, not on the container's overlay filesystem, which
# can stall under I/O pressure and get healthy workers killed.
worker_tmp_dir = "/dev/shm"

# Header and request-line limits: gunicorn's defaults, stated so a change is a
# reviewed diff rather than a library upgrade.
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190

# Scheme and client address come from Django (SECURE_PROXY_SSL_HEADER and
# platform/observability/client_ip.py, both driven by W360_TRUSTED_PROXY_COUNT),
# so gunicorn is told to trust no forwarding header itself: one place decides
# which hops are believed, not two that could disagree.
forwarded_allow_ips = ""

# Gunicorn 24+ opens a local control socket (under $HOME by default). The code
# tree is read-only in the image, and a runtime control channel is not needed:
# workers are scaled and reloaded by the orchestrator, not from inside the
# container.
control_socket_disable = True
