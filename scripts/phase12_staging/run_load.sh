#!/usr/bin/env bash
# Phase 12 load test driver (Steps 25-27, 56). Runs k6 (scripts/phase12_staging/load.js)
# against the staging edge at each concurrency level, and samples alongside it:
#   container CPU/memory (docker stats), PostgreSQL connections and commit rate
#   (pg_stat_activity / pg_stat_database), broker queue depth (/healthz/metrics).
#
#   W360_STG_COMPOSE=<compose.sh> OUT=<dir> LEVELS="50 100 250 500 spike" DURATION=90s \
#     LOAD_PASSWORD=... STAFF_PASSWORD=... STAFF_TOTP=... bash scripts/phase12_staging/run_load.sh
set -euo pipefail
: "${W360_STG_COMPOSE:?}" "${OUT:?}" "${LOAD_PASSWORD:?}"
# Staff credentials are optional: without them load.js leaves the staff VU out.
STAFF_PASSWORD="${STAFF_PASSWORD:-}" STAFF_TOTP="${STAFF_TOTP:-}"
LEVELS="${LEVELS:-50 100 250 500}"
DURATION="${DURATION:-90s}"
HERE="$(cd "$(dirname "$0")" && (pwd -W 2>/dev/null || pwd))"
OUTH="$(cd "$OUT" && (pwd -W 2>/dev/null || pwd))"
C="bash $W360_STG_COMPOSE"
EDGE_IP="$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{if eq $k "w360stg_public"}}{{$v.IPAddress}}{{end}}{{end}}' w360stg-edge-1)"
METRICS_TOKEN="$(grep '^W360_METRICS_TOKEN=' "${W360_ENV_FILE:?}" | cut -d= -f2)"

db() { $C exec -T postgres psql -U postgres -d workeasy360 -At -F ' ' -c "$1"; }
metrics() { $C exec -T api python -c "import urllib.request as u;r=u.Request('http://127.0.0.1:8000/healthz/metrics',headers={'Authorization':'Bearer $METRICS_TOKEN'});print(u.urlopen(r).read().decode())"; }

for level in $LEVELS; do
  echo "== level $level"
  commits_before=$(db "SELECT xact_commit FROM pg_stat_database WHERE datname='workeasy360'")
  ( for i in $(seq 1 1000); do
      docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' \
        $(docker ps --format '{{.Names}}' | grep '^w360stg-') >> "$OUT/stats-$level.csv" 2>/dev/null
      db "SELECT now(), count(*), count(*) FILTER (WHERE state='active') FROM pg_stat_activity WHERE datname='workeasy360'" >> "$OUT/db-$level.txt" 2>/dev/null
      metrics 2>/dev/null | grep -E '^w360_queue_messages' | tr '\n' ' ' >> "$OUT/queues-$level.txt"; echo >> "$OUT/queues-$level.txt"
      sleep 5
    done ) &
  sampler=$!
  started=$(date +%s)
  docker run --rm --network w360stg_public --add-host "app.staging.workeasy360.com:$EDGE_IP" \
    -e SSL_CERT_FILE=/data/caddy-root.crt -e LEVEL="$level" -e DURATION="$DURATION" \
    -e LOAD_PASSWORD="$LOAD_PASSWORD" -e STAFF_EMAIL=superadmin@staging.workeasy360.com \
    -e STAFF_PASSWORD="$STAFF_PASSWORD" -e STAFF_TOTP="$STAFF_TOTP" \
    -v "$HERE:/scripts:ro" -v "$OUTH:/data" grafana/k6:latest run --quiet /scripts/load.js \
    > "$OUT/k6-$level.log" 2>&1 || echo "k6 exit $? at level $level" >> "$OUT/k6-$level.log"
  kill "$sampler" 2>/dev/null || true
  elapsed=$(( $(date +%s) - started ))
  commits_after=$(db "SELECT xact_commit FROM pg_stat_database WHERE datname='workeasy360'")
  echo "level=$level elapsed=${elapsed}s db_commits=$((commits_after - commits_before))" | tee -a "$OUT/summary.txt"
  sleep 20   # let queues and connections settle between levels
done
