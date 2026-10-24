# syntax=docker/dockerfile:1
#
# Workeasy360 frontend: the Next.js platform shell, production build.
#
# Build from the repository root (the .dockerignore there is an allow-list):
#   docker build -f deploy/docker/frontend.Dockerfile \
#     --build-arg BACKEND_ORIGIN=http://api:8000 -t workeasy360-frontend:<release> .
#
# BACKEND_ORIGIN is the target of the `/api` rewrite and is BAKED IN at build
# time (next.config.ts `rewrites()`). In the production topology the edge
# routes `/api/*` straight to the API, so the rewrite is only a fallback - but
# it must still name the internal API, never a public host.
#
# Node 24 (Active LTS). Node 20, which the repository's CI still named, reached
# end of life in April 2026 (Phase 12 finding); the image is what production
# runs, so it is what the frontend gates were re-run on.

# Debian 13 (trixie) for the same reason as the backend image (Phase 12).
ARG NODE_IMAGE=node:24-trixie-slim

FROM ${NODE_IMAGE} AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY frontend/package.json frontend/package-lock.json ./
# `ci`: the lockfile is the dependency decision.
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
ARG BACKEND_ORIGIN=http://api:8000
ENV BACKEND_ORIGIN=${BACKEND_ORIGIN}
RUN npm run build \
 && node scripts/assert-dynamic-rendering.mjs \
 && npm prune --omit=dev --no-audit --no-fund

FROM ${NODE_IMAGE}
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
# Debian security fixes published after the base image was built (openssl,
# libssl3t64, libpcre2-8-0 - trivy HIGH, fixed versions available).
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
# The runtime needs node and the app's own node_modules, nothing else. The npm
# and corepack CLIs bundled with the base image carried every Node.js CVE the
# Phase 12 trivy scan found (brace-expansion, undici, ip-address, tar), none of
# them ever run in production - so they are removed, not patched.
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack       /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack
WORKDIR /app
COPY --from=build --chown=root:root /app/package.json /app/package.json
COPY --from=build --chown=root:root /app/node_modules /app/node_modules
COPY --from=build --chown=root:root /app/public /app/public
COPY --from=build --chown=root:root /app/next.config.ts /app/next.config.ts
# next.config.ts imports the partner menu's LEGACY_REDIRECTS and the workspaces'
# SHORT_ADDRESSES at start-up; without these files `next start` fails with
# MODULE_NOT_FOUND (release 2026.10.19). Every relative import of next.config.ts
# must be copied here - backend/tests/architecture/test_frontend_image_runtime.py.
COPY --from=build --chown=root:root /app/src/features/partnerpanel/menu.ts /app/src/features/partnerpanel/menu.ts
COPY --from=build --chown=root:root /app/src/features/partnerpanel/experiences.ts /app/src/features/partnerpanel/experiences.ts
COPY --from=build --chown=node:node /app/.next /app/.next
# The image's built-in unprivileged user. .next is the only tree it owns (Next
# writes its image/fetch cache there); code and dependencies stay root-owned.
USER node
ARG RELEASE=unset
LABEL org.opencontainers.image.title="workeasy360-frontend" \
      org.opencontainers.image.revision="${RELEASE}"
EXPOSE 3000
CMD ["node_modules/.bin/next", "start", "-H", "0.0.0.0", "-p", "3000"]
