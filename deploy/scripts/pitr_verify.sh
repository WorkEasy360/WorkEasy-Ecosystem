#!/usr/bin/env bash
# Workeasy360 - point-in-time recovery, proven (Phase 12, Steps 19-20).
#
#   W360_COMPOSE="docker compose -p <project> -f ..." deploy/scripts/pitr_verify.sh <work-dir>
#
# The single-host topology archives every WAL segment (archive_command in
# deploy/compose/production.yml, archive_timeout=300 s). This script proves the
# archive is usable, which is what the RPO depends on:
#   1. base backup (pg_basebackup, tar) of the running cluster;
#   2. a marker row written, the target instant T recorded, a second marker
#      written after T, and a WAL switch so both are archived;
#   3. a NEW, isolated PostgreSQL restored from the base backup, replaying the
#      archive up to T (recovery_target_time);
#   4. PASS only if the first marker is there and the second is not.
# Markers live in their own schema (ops_pitr), never in an application table.
set -euo pipefail

WORK="${1:?usage: pitr_verify.sh <work-dir>}"
COMPOSE="${W360_COMPOSE:?}"
mkdir -p "$WORK"
hostpath() { (cd "$1" && (pwd -W 2>/dev/null || pwd)); }
WORK_HOST="$(hostpath "$WORK")"
TAG="w360pitr$$"
psql_live() { $COMPOSE exec -T postgres psql -U postgres -d workeasy360 -At -v ON_ERROR_STOP=1 -c "$1"; }
cleanup() { docker rm -f "$TAG" >/dev/null 2>&1 || true; docker volume rm "$TAG-data" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "[pitr] base backup"
t0=$(date +%s)
$COMPOSE exec -T postgres sh -c 'rm -rf /tmp/pitr-base && pg_basebackup -U postgres -D /tmp/pitr-base -Ft -z -X none -c fast'
$COMPOSE cp postgres:/tmp/pitr-base "$WORK/base"
echo "[time] base_backup_seconds=$(( $(date +%s) - t0 ))"

psql_live "CREATE SCHEMA IF NOT EXISTS ops_pitr; CREATE TABLE IF NOT EXISTS ops_pitr.marker (label text, at timestamptz default now());"
psql_live "INSERT INTO ops_pitr.marker(label) VALUES ('before-target-$$');"
sleep 2
TARGET="$(psql_live "SELECT now()::text;")"
sleep 2
psql_live "INSERT INTO ops_pitr.marker(label) VALUES ('after-target-$$');"
psql_live "SELECT pg_switch_wal();" >/dev/null
sleep 3
echo "[pitr] target instant: $TARGET"

echo "[pitr] copy the WAL archive"
$COMPOSE cp postgres:/wal-archive "$WORK/wal"

# A recovering cluster must run with at least the primary's values for these
# (PostgreSQL refuses to replay otherwise) - read them from the primary.
MAXCONN="$(psql_live "SHOW max_connections;")"
MAXLOCKS="$(psql_live "SHOW max_locks_per_transaction;")"
MAXWORKERS="$(psql_live "SHOW max_worker_processes;")"
echo "[pitr] restore into an isolated cluster and replay to the target"
t1=$(date +%s)
docker volume create "$TAG-data" >/dev/null
docker run --rm -v "$TAG-data:/data" -v "$WORK_HOST:/work:ro" pgvector/pgvector:pg16-trixie sh -c '
  set -e
  tar xzf /work/base/base.tar.gz -C /data
  chown -R postgres:postgres /data && chmod 700 /data
  touch /data/recovery.signal
' >/dev/null
docker run -d --name "$TAG" -v "$TAG-data:/var/lib/postgresql/data" -v "$WORK_HOST/wal:/wal:ro" \
  -e POSTGRES_PASSWORD=unused pgvector/pgvector:pg16-trixie \
  postgres -c "restore_command=cp /wal/%f %p" -c "recovery_target_time=$TARGET" \
  -c recovery_target_action=promote -c archive_mode=off   -c "max_connections=$MAXCONN" -c "max_locks_per_transaction=$MAXLOCKS"   -c "max_worker_processes=$MAXWORKERS" >/dev/null
for _ in $(seq 1 90); do
  state=$(docker exec "$TAG" psql -U postgres -At -c "SELECT pg_is_in_recovery()" 2>/dev/null || true)
  [ "$state" = "f" ] && break
  sleep 1
done
echo "[time] restore_and_replay_seconds=$(( $(date +%s) - t1 ))"

labels="$(docker exec "$TAG" psql -U postgres -d workeasy360 -At -c "SELECT string_agg(label, ',' ORDER BY at) FROM ops_pitr.marker")"
users="$(docker exec "$TAG" psql -U postgres -d workeasy360 -At -c "SELECT count(*) FROM identity_user")"
echo "[pitr] markers in the restored cluster: $labels; users: $users"
psql_live "DROP SCHEMA ops_pitr CASCADE;" >/dev/null
case ",$labels," in
  *",before-target-$$,"*) ;;
  *) echo "[FAIL] the pre-target write is missing"; exit 1 ;;
esac
case ",$labels," in
  *",after-target-$$,"*) echo "[FAIL] a post-target write was replayed"; exit 1 ;;
esac
echo "[PASS] point-in-time recovery to $TARGET: the earlier write is there, the later one is not"
