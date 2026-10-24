#!/usr/bin/env bash
# Workeasy360 - restore a backup into an ISOLATED environment and verify it
# (Phase 12, Step 19). A backup is only "verified" once this has passed on it.
#
#   W360_BACKEND_IMAGE=workeasy360-backend:<release> W360_ENV_FILE=<env> \
#     deploy/scripts/restore_verify.sh <backup-dir>/<timestamp> [--keep]
#
# 1. A new PostgreSQL (own network, own volume, NEW role passwords) - never
#    the live cluster.
# 2. Roles from deploy/postgres/roles.sh, then pg_restore of db.dump.
# 3. Row counts of every authoritative table compared with counts.json.
# 4. The application image, pointed at the restored database, runs its own
#    deploy checks (RLS role, table ownership, timeouts, RLS on every tenant
#    table) and reports no unapplied migration.
# 5. The uploaded-files archive is restored into a new volume and listed.
# Prints timings and a PASS/FAIL line per check; exit status 0 only if all pass.
set -euo pipefail

SRC="${1:?usage: restore_verify.sh <backup-dir> [--keep]}"
KEEP="${2:-}"
IMAGE="${W360_BACKEND_IMAGE:?W360_BACKEND_IMAGE is required}"
ENV_FILE="${W360_ENV_FILE:?W360_ENV_FILE is required (non-DB settings for the app check)}"
hostpath() { (cd "$1" && (pwd -W 2>/dev/null || pwd)); }  # Git Bash vs Linux
ROOT="$(hostpath "$(dirname "$0")/../..")"
SRC_HOST="$(hostpath "$SRC")"
TAG="w360rst$$"
NET="$TAG-net"
PG="$TAG-pg"
fails=0
pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*"; fails=$((fails + 1)); }
rnd() { "${PYTHON:-python3}" -c "import secrets;print(secrets.token_urlsafe(24))" 2>/dev/null || openssl rand -base64 24 | tr -d '/+='; }

cleanup() {
  if [ "$KEEP" != "--keep" ]; then
    docker rm -f "$PG" "$TAG-app" >/dev/null 2>&1 || true
    docker volume rm "$TAG-pgdata" "$TAG-files" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

(cd "$SRC" && sha256sum -c SHA256SUMS --quiet) && pass "backup checksums verify" || fail "backup checksums"

SUPER_PW="$(rnd)"; APP_PW="$(rnd)"; MIG_PW="$(rnd)"
docker network create "$NET" >/dev/null
docker volume create "$TAG-pgdata" >/dev/null
docker run -d --name "$PG" --network "$NET" --network-alias restored-postgres \
  -e POSTGRES_PASSWORD="$SUPER_PW" -e POSTGRES_DB=workeasy360 \
  -e W360_DB_APP_PASSWORD="$APP_PW" -e W360_DB_MIGRATOR_PASSWORD="$MIG_PW" \
  -v "$TAG-pgdata:/var/lib/postgresql/data" \
  -v "$ROOT/deploy/postgres/roles.sh:/docker-entrypoint-initdb.d/10-roles.sh:ro" \
  pgvector/pgvector:pg16-trixie >/dev/null
for _ in $(seq 1 60); do
  docker exec "$PG" pg_isready -U postgres -d workeasy360 >/dev/null 2>&1 && break; sleep 1
done
sleep 3

t0=$(date +%s)
docker exec -i "$PG" pg_restore -U postgres -d workeasy360 --exit-on-error --single-transaction \
  < "$SRC/db.dump" && pass "pg_restore completed in one transaction" || fail "pg_restore"
t1=$(date +%s)
echo "[time] restore_seconds=$((t1 - t0))"

"${PYTHON:-python3}" - "$SRC/counts.json" "$PG" <<'PY' || fails=$((fails + 1))
import json, subprocess, sys
want = json.load(open(sys.argv[1]))
bad = 0
for table, n in want.items():
    got = subprocess.run(
        ["docker", "exec", sys.argv[2], "psql", "-U", "postgres", "-d", "workeasy360", "-At",
         "-c", f"SELECT count(*) FROM {table}"], capture_output=True, text=True).stdout.strip()
    ok = got == str(n)
    bad += not ok
    print(f"[{'PASS' if ok else 'FAIL'}] rows {table}: backup={n} restored={got}")
sys.exit(1 if bad else 0)
PY

echo "[app] the application's own gates against the restored database"
# Each check is judged by its exit status, never by grepping its output (the
# first version grepped a truncated tail and could not fail - final audit).
app() {
  docker run --rm --name "$TAG-app" --network "$NET" --env-file "$ENV_FILE" \
    -e DJANGO_SETTINGS_MODULE=config.settings.staging -e W360_ENVIRONMENT=staging \
    -e W360_DB_HOST=restored-postgres -e W360_DB_PORT=5432 \
    -e W360_DB_DIRECT_HOST=restored-postgres -e W360_DB_DIRECT_PORT=5432 \
    -e W360_DB_APP_PASSWORD="$APP_PW" -e W360_DB_MIGRATOR_PASSWORD="$MIG_PW" \
    -e W360_REDIS_URL="redis://:unused@127.0.0.1:1/0" \
    "$IMAGE" "$@"
}
if app python manage.py w360_startup_gate --wait 20 > "/tmp/$TAG-gate.txt" 2>&1; then
  pass "startup gate: database, migrations, registries, runtime role cannot bypass RLS and owns no table, timeouts"
else
  grep -v '^{' "/tmp/$TAG-gate.txt" | tail -5; fail "startup gate on the restored database"
fi
if app python manage.py check --deploy --fail-level ERROR > "/tmp/$TAG-check.txt" 2>&1; then
  pass "check --deploy (no error)"
else
  grep -v '^{' "/tmp/$TAG-check.txt" | tail -20; fail "check --deploy on the restored database"
fi
if app python manage.py migrate --check > "/tmp/$TAG-migrate.txt" 2>&1; then
  pass "no unapplied migration (migrate --check)"
else
  grep -v '^{' "/tmp/$TAG-migrate.txt" | tail -5; fail "migration state"
fi
# This environment connects directly, where the settings' libpq options would
# satisfy the timeout check by themselves; production goes through PgBouncer,
# which ignores them. So the role defaults themselves are checked too.
cfg="$(docker exec "$PG" psql -U postgres -d workeasy360 -At -c \
  "SELECT array_to_string(rolconfig, ',') FROM pg_roles WHERE rolname = 'w360_app'")"
missing=""
for setting in statement_timeout lock_timeout idle_in_transaction_session_timeout; do
  case ",$cfg," in *",$setting="*) ;; *) missing="$missing $setting" ;; esac
done
[ -z "$missing" ] && pass "the runtime role carries its timeouts as role defaults ($cfg)"   || fail "the runtime role lacks timeout defaults:$missing"

if [ -f "$SRC/files.tar.gz" ]; then
  docker volume create "$TAG-files" >/dev/null
  docker run --rm -v "$TAG-files:/data" -v "$SRC_HOST:/src:ro" alpine:3 sh -c \
    'tar xzf /src/files.tar.gz -C /data && find /data -type f | wc -l' > "/tmp/$TAG-files.txt"
  n=$(cat "/tmp/$TAG-files.txt")
  [ "${n:-0}" -gt 0 ] && pass "uploaded-files store restored ($n files)" || fail "files restore"
fi

echo "[time] total_seconds=$(( $(date +%s) - t0 ))"
[ "$fails" -eq 0 ] && echo "RESTORE VERIFIED" || { echo "RESTORE FAILED ($fails)"; exit 1; }
