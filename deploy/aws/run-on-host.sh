#!/usr/bin/env bash
# Run commands on the production host through AWS Systems Manager (no SSH, no stored key) and wait.
#   deploy/aws/run-on-host.sh <region> <instance-id> <commands.json>   ({"commands": [...]})
# Prints the command's output and exits with its status.
set -euo pipefail
REGION="$1" INSTANCE="$2" FILE="$3"
id="$(aws ssm send-command --region "$REGION" --instance-ids "$INSTANCE" \
  --document-name AWS-RunShellScript --comment "workeasy360 ops (GitHub Actions)" \
  --parameters "file://$FILE" --timeout-seconds 1800 --query 'Command.CommandId' --output text)"
echo "command $id"
for _ in $(seq 1 180); do
  status="$(aws ssm get-command-invocation --region "$REGION" --command-id "$id" --instance-id "$INSTANCE" \
    --query Status --output text 2>/dev/null || echo Pending)"
  case "$status" in Pending|InProgress|Delayed) sleep 10 ;; *) break ;; esac
done
aws ssm get-command-invocation --region "$REGION" --command-id "$id" --instance-id "$INSTANCE" \
  --query '[StandardOutputContent, StandardErrorContent]' --output text
[ "$status" = Success ] || { echo "::error::host command ended $status"; exit 1; }
