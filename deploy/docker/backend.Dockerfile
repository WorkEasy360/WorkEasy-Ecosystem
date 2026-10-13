# syntax=docker/dockerfile:1
#
# Workeasy360 backend: ONE image, many entrypoints (13-deployment-evolution.md
# section 2.1). The same image runs as the API, every Celery worker, Beat and
# the pre-deploy migration task - only the command differs, so one build, one
# dependency set and one migration set are what reach production.
#
#   api        gunicorn -c /app/deploy/gunicorn.conf.py config.wsgi:application
#   worker     celery -A config worker -Q critical   (see deploy/compose/production.yml)
#   beat       celery -A config beat
#   migrate    DJANGO_SETTINGS_MODULE=config.settings.migration python manage.py migrate
#
# Build from the repository root (the .dockerignore there is an allow-list):
#   docker build -f deploy/docker/backend.Dockerfile -t workeasy360-backend:<release> .
#
# No secret is ever an ARG, ENV or file in this image: every credential is read
# from the process environment at start-up, populated by the platform's secret
# store (docs/phase-12/05-secrets-and-key-rotation.md).

# Debian 13 (trixie): the bookworm base carried unfixed HIGH/CRITICAL OS CVEs
# (perl-base, util-linux, sqlite, zlib/minizip) - Phase 12 trivy scan.
ARG PYTHON_IMAGE=python:3.12.14-slim-trixie
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production

# Debian security fixes published after the base image was built (openssl,
# libssl3t64, libpcre2-8-0 - trivy HIGH, fixed versions available).
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*

# An unprivileged, fixed uid: nothing in the container runs as root, and a
# fixed id makes volume ownership predictable across rebuilds.
RUN groupadd --system --gid 10001 w360 \
 && useradd --system --uid 10001 --gid w360 --home-dir /app --no-create-home \
            --shell /usr/sbin/nologin w360

WORKDIR /app

# Dependencies first, in their own layer, from the pinned requirements file.
# Every runtime wheel used here is a manylinux binary, so no compiler toolchain
# is installed into the image.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-compile -r /app/requirements.txt

COPY --chown=root:root backend/ /app/backend/
COPY --chown=root:root infra/ecosystem/products.json /app/infra/ecosystem/products.json
COPY --chown=root:root deploy/gunicorn.conf.py /app/deploy/gunicorn.conf.py

# Application code is owned by root and read-only to the runtime user: a
# compromised process cannot rewrite the code it is running. The one writable
# place Beat needs is its schedule file, kept in /tmp.
USER w360
WORKDIR /app/backend

ARG RELEASE=unset
LABEL org.opencontainers.image.title="workeasy360-backend" \
      org.opencontainers.image.revision="${RELEASE}"

EXPOSE 8000
CMD ["gunicorn", "-c", "/app/deploy/gunicorn.conf.py", "config.wsgi:application"]
