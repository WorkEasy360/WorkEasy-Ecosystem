#!/usr/bin/env bash
# Workeasy360 - nightly base backup and WAL retention (Phase 12, final audit).
#
#   W360_COMPOSE="docker compose -p w360 -f deploy/compose/production.yml --env-file ..." \
#     KEEP=7 deploy/scripts/wal_retention.sh
#
# The single-host topology archives every WAL segment into the `walarchive`
# volume (archive_command in production.yml). Without this job that archive
# only ever grows - up to ~288 x 16 MiB a day - until the disk fills and
# PostgreSQL stops, and there is no base backup to replay it onto.
#
# Each run:
#   1. takes a base backup (pg_basebackup, tar + gzip, WAL from the archive)
#      into /wal-archive/base/<UTC timestamp>/;
#   2. keeps the newest KEEP base backups (default 7), removing older ones;
#   3. removes archived WAL older than the oldest base backup kept
#      (pg_archivecleanup), so recovery stays possible to any point since it.
#
# Schedule it daily (04-deployment.md §7) and copy /wal-archive off-host
# (pgBackRest / WAL-G, or a sync to object storage): a copy on the same disk
# does not survive losing the host. Prints what it did; exits non-zero on any
# failure, so the scheduler (and the backup-failure alert) sees it.
set -euo pipefail
: "${W360_COMPOSE:?W360_COMPOSE is required}"
KEEP="${KEEP:-7}"
C="$W360_COMPOSE"
case "$KEEP" in ''|*[!0-9]*) echo "KEEP must be a positive integer" >&2; exit 2 ;; esac
[ "$KEEP" -ge 1 ] || { echo "KEEP must be at least 1" >&2; exit 2; }

ts="$(date -u +%Y%m%dT%H%M%SZ)"
t0=$(date +%s)
echo "[wal] base backup $ts"
$C exec -T postgres sh -c "mkdir -p /wal-archive/base && pg_basebackup -U postgres \
  -D /wal-archive/base/$ts -Ft -z -X none -c fast --label=w360-$ts"

echo "[wal] keep the newest $KEEP base backups"
$C exec -T postgres sh -c "cd /wal-archive/base && ls -1 | sort | head -n -$KEEP | \
  while read -r old; do echo \"  removing base backup \$old\"; rm -rf -- \"\$old\"; done"

oldest="$($C exec -T postgres sh -c 'ls -1 /wal-archive/base | sort | head -n 1' | tr -d '\r')"
[ -n "$oldest" ] || { echo "no base backup left - refusing to clean WAL" >&2; exit 1; }
start_wal="$($C exec -T postgres sh -c "tar -xzOf /wal-archive/base/$oldest/base.tar.gz backup_label" \
  | sed -n 's/^START WAL LOCATION: .*(file \([0-9A-F]\{24\}\))$/\1/p' | tr -d '\r')"
[ -n "$start_wal" ] || { echo "could not read the oldest base backup's start WAL" >&2; exit 1; }

before="$($C exec -T postgres sh -c 'ls -1 /wal-archive | grep -c "^[0-9A-F]\{24\}" || true' | tr -d '\r')"
echo "[wal] remove archived WAL older than $start_wal (base backup $oldest)"
$C exec -T postgres pg_archivecleanup /wal-archive "$start_wal"
after="$($C exec -T postgres sh -c 'ls -1 /wal-archive | grep -c "^[0-9A-F]\{24\}" || true' | tr -d '\r')"
echo "[wal] segments: $before -> $after; base backups kept: $($C exec -T postgres sh -c 'ls -1 /wal-archive/base | wc -l' | tr -d '\r')"
echo "[time] seconds=$(( $(date +%s) - t0 ))"
echo "WAL RETENTION DONE"
