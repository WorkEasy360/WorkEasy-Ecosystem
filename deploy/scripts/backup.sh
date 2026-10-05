#!/usr/bin/env bash
# Workeasy360 - production backup (Phase 12, Step 18).
#
#   W360_COMPOSE="docker compose -p w360 -f deploy/compose/production.yml --env-file ..." \
#     deploy/scripts/backup.sh <backup-dir>
#
# Produces, in <backup-dir>/<UTC timestamp>/:
#   db.dump            pg_dump custom format (compressed) of the platform database
#   globals.sql        roles and their attributes, WITHOUT passwords
#   files.tar.gz       the uploaded-files store (S3-compatible volume in the
#                      single-host topology; on AWS: bucket versioning +
#                      cross-region replication instead - docs/phase-12/03-backup-and-restore.md)
#   counts.json        row counts of the authoritative tables, for restore verification
#   manifest.json      release, image digests, config hashes, variable NAMES (never values)
#   SHA256SUMS         checksums of all of the above
#
# The logical dump is the portable, verifiable backup. Point-in-time recovery
# comes from WAL archiving (archive_command in production.yml) plus periodic
# base backups; see the same document. A backup that has not been restored is
# not a backup: run deploy/scripts/restore_verify.sh against every new one.
set -euo pipefail

OUT_ROOT="${1:?usage: backup.sh <backup-dir>}"
COMPOSE="${W360_COMPOSE:?W360_COMPOSE must name the compose command for the stack}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$OUT_ROOT/$STAMP"
mkdir -p "$OUT"
started=$(date +%s)

echo "[backup] database (pg_dump -Fc)"
$COMPOSE exec -T postgres pg_dump -U postgres -d workeasy360 -Fc -Z 6 > "$OUT/db.dump"
$COMPOSE exec -T postgres pg_dumpall -U postgres --globals-only --no-role-passwords > "$OUT/globals.sql"

echo "[backup] row counts of authoritative tables"
$COMPOSE exec -T postgres psql -U postgres -d workeasy360 -At -F ' ' -c "
SELECT 'identity_user', count(*) FROM identity_user
UNION ALL SELECT 'organizations_organization', count(*) FROM organizations_organization
UNION ALL SELECT 'organizations_membership', count(*) FROM organizations_membership
UNION ALL SELECT 'subscriptions_subscription', count(*) FROM subscriptions_subscription
UNION ALL SELECT 'subscriptions_product_trial', count(*) FROM subscriptions_product_trial
UNION ALL SELECT 'entitlements_entitlement', count(*) FROM entitlements_entitlement
UNION ALL SELECT 'entitlements_snapshot', count(*) FROM entitlements_snapshot
UNION ALL SELECT 'catalog_product', count(*) FROM catalog_product
UNION ALL SELECT 'catalog_plan_version', count(*) FROM catalog_plan_version
UNION ALL SELECT 'payments_order', count(*) FROM payments_order
UNION ALL SELECT 'payments_invoice', count(*) FROM payments_invoice
UNION ALL SELECT 'partners_account', count(*) FROM partners_account
UNION ALL SELECT 'partners_product_grant', count(*) FROM partners_product_grant
UNION ALL SELECT 'partners_domain', count(*) FROM partners_domain
UNION ALL SELECT 'partners_organization_link', count(*) FROM partners_organization_link
UNION ALL SELECT 'commissions_accrual', count(*) FROM commissions_accrual
UNION ALL SELECT 'commissions_payout', count(*) FROM commissions_payout
UNION ALL SELECT 'entitlements_grant', count(*) FROM entitlements_grant
UNION ALL SELECT 'entitlements_seat_assignment', count(*) FROM entitlements_seat_assignment
UNION ALL SELECT 'catalog_plan', count(*) FROM catalog_plan
UNION ALL SELECT 'audit_event', count(*) FROM audit_event
UNION ALL SELECT 'ecosystem_product_integration', count(*) FROM ecosystem_product_integration
" | awk 'BEGIN{printf "{"} {printf "%s\"%s\": %s", (NR>1?", ":""), $1, $2} END{print "}"}' > "$OUT/counts.json"

echo "[backup] uploaded files"
FILES_VOLUME="${W360_FILES_VOLUME:-}"
if [ -n "$FILES_VOLUME" ]; then
  # `pwd -W` on Git Bash (a Windows rehearsal host), plain `pwd` elsewhere.
  host_out="$(cd "$OUT" && (pwd -W 2>/dev/null || pwd))"
  docker run --rm -v "$FILES_VOLUME:/data:ro" -v "$host_out:/out" alpine:3 \
    tar czf /out/files.tar.gz -C /data .
  [ -s "$OUT/files.tar.gz" ] || { echo "[backup] files archive missing" >&2; exit 1; }
else
  echo "  W360_FILES_VOLUME unset: files live in S3 (versioned + replicated); not copied here." \
    | tee "$OUT/files.NOTE"
fi

echo "[backup] manifest"
{
  echo "{"
  echo "  \"created_at\": \"$STAMP\","
  echo "  \"release\": \"${W360_RELEASE:-unknown}\","
  echo "  \"images\": {"
  for svc in api web; do
    img=$($COMPOSE images -q "$svc" 2>/dev/null | head -1 || true)
    echo "    \"$svc\": \"${img:-unknown}\","
  done
  echo "    \"_\": \"\""
  echo "  },"
  echo "  \"config_sha256\": {"
  for f in deploy/compose/production.yml deploy/edge/Caddyfile deploy/pgbouncer/pgbouncer.ini deploy/postgres/roles.sh; do
    [ -f "$f" ] && echo "    \"$f\": \"$(sha256sum "$f" | cut -d' ' -f1)\","
  done
  echo "    \"_\": \"\""
  echo "  },"
  if [ -n "${W360_ENV_FILE:-}" ] && [ -f "$W360_ENV_FILE" ]; then
    echo "  \"env_variable_names\": [$(cut -d= -f1 "$W360_ENV_FILE" | grep -v '^#' | grep -v '^$' | sed 's/.*/"&"/' | paste -sd, -)],"
  fi
  echo "  \"duration_s\": $(( $(date +%s) - started ))"
  echo "}"
} > "$OUT/manifest.json"

(cd "$OUT" && sha256sum ./* > SHA256SUMS)
echo "[backup] done: $OUT ($(du -sh "$OUT" | cut -f1)) in $(( $(date +%s) - started ))s"
echo "$OUT"
