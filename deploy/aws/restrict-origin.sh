#!/usr/bin/env bash
# Close the production origin to everything but Cloudflare (80, 443) and an admin network (22).
#
# Today the origin answers HTTPS for every name directly, bypassing Cloudflare's protections,
# and SSH is open to the internet (found 2026-11-18). This script changes ONLY the instance's
# security group, prints every change first, and makes none without --apply.
#
#   deploy/aws/restrict-origin.sh --region ap-southeast-1 --instance i-... --admin-cidr 203.0.113.7/32
#   deploy/aws/restrict-origin.sh ... --apply
#   deploy/aws/restrict-origin.sh ... --rollback <saved rules file>     # restore what it replaced
#
# Before SSH is closed to the internet it requires a second way in: the instance must be online
# in AWS Systems Manager (the instance role from ops-single-host.yml carries the SSM policy).
# Without that it leaves port 22 alone and says so. Caddy's certificates keep renewing: the
# HTTP-01 challenge reaches the origin through Cloudflare's own addresses, which stay allowed.
set -euo pipefail

REGION="" INSTANCE="" ADMIN="" APPLY=0 ROLLBACK=""
while [ $# -gt 0 ]; do
  case "$1" in
    --region) REGION="$2"; shift 2 ;;
    --instance) INSTANCE="$2"; shift 2 ;;
    --admin-cidr) ADMIN="$2"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    --rollback) ROLLBACK="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$REGION" ] && [ -n "$INSTANCE" ] || { echo "usage: --region R --instance I [--admin-cidr C] [--apply]" >&2; exit 2; }
aws() { command aws --region "$REGION" "$@"; }

SG="$(aws ec2 describe-instances --instance-ids "$INSTANCE" \
  --query 'Reservations[0].Instances[0].SecurityGroups[0].GroupId' --output text)"
echo "security group: $SG"

if [ -n "$ROLLBACK" ]; then
  echo "restoring the ingress rules saved in $ROLLBACK"
  aws ec2 authorize-security-group-ingress --group-id "$SG" --ip-permissions "file://$ROLLBACK"
  exit 0
fi

saved="restrict-origin-$SG-$(date -u +%Y%m%dT%H%M%SZ).json"
aws ec2 describe-security-groups --group-ids "$SG" --query 'SecurityGroups[0].IpPermissions' > "$saved"
echo "current ingress rules saved to $saved (use --rollback $saved to restore them)"

cf_v4="$(curl -fsS https://www.cloudflare.com/ips-v4)"
cf_v6="$(curl -fsS https://www.cloudflare.com/ips-v6)"
[ "$(echo "$cf_v4" | wc -l)" -ge 10 ] || { echo "Cloudflare's IPv4 list looks wrong; stopping" >&2; exit 1; }

open_world() {  # does the group allow this port from anywhere?
  aws ec2 describe-security-groups --group-ids "$SG" --query \
    "SecurityGroups[0].IpPermissions[?FromPort==\`$1\`].[IpRanges[?CidrIp=='0.0.0.0/0'] | length(@), Ipv6Ranges[?CidrIpv6=='::/0'] | length(@)]" \
    --output text | tr -s '\t ' '\n' | awk '{s += $1} END {print s + 0}'
}

plan=()
for port in 80 443; do
  ranges="$(echo "$cf_v4" | awk -v p="$port" '{printf "%s{\"IpProtocol\":\"tcp\",\"FromPort\":%s,\"ToPort\":%s,\"IpRanges\":[{\"CidrIp\":\"%s\",\"Description\":\"Cloudflare\"}]}", (NR>1?",":""), p, p, $1}')"
  ranges6="$(echo "$cf_v6" | awk -v p="$port" '{printf ",{\"IpProtocol\":\"tcp\",\"FromPort\":%s,\"ToPort\":%s,\"Ipv6Ranges\":[{\"CidrIpv6\":\"%s\",\"Description\":\"Cloudflare\"}]}", p, p, $1}')"
  plan+=("allow $port from Cloudflare|authorize|[$ranges$ranges6]")
  if [ "$(open_world "$port")" -gt 0 ]; then
    plan+=("stop allowing $port from anywhere|revoke|[{\"IpProtocol\":\"tcp\",\"FromPort\":$port,\"ToPort\":$port,\"IpRanges\":[{\"CidrIp\":\"0.0.0.0/0\"}],\"Ipv6Ranges\":[{\"CidrIpv6\":\"::/0\"}]}]")
  fi
done

if [ -n "$ADMIN" ]; then
  online="$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$INSTANCE" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || true)"
  if [ "$online" = "Online" ]; then
    plan+=("allow 22 from $ADMIN|authorize|[{\"IpProtocol\":\"tcp\",\"FromPort\":22,\"ToPort\":22,\"IpRanges\":[{\"CidrIp\":\"$ADMIN\",\"Description\":\"admin\"}]}]")
    if [ "$(open_world 22)" -gt 0 ]; then
      plan+=("stop allowing 22 from anywhere|revoke|[{\"IpProtocol\":\"tcp\",\"FromPort\":22,\"ToPort\":22,\"IpRanges\":[{\"CidrIp\":\"0.0.0.0/0\"}],\"Ipv6Ranges\":[{\"CidrIpv6\":\"::/0\"}]}]")
    fi
  else
    echo "SSH left as it is: the instance is not online in Systems Manager (status: ${online:-none}), so closing 22 could lock administrators out."
  fi
else
  echo "SSH left as it is: no --admin-cidr given."
fi

echo; echo "planned changes:"
for step in "${plan[@]}"; do echo "  - ${step%%|*}"; done
[ "$APPLY" = 1 ] || { echo; echo "dry run: nothing changed (add --apply)."; exit 0; }

for step in "${plan[@]}"; do
  what="${step%%|*}"; rest="${step#*|}"; verb="${rest%%|*}"; perms="${rest#*|}"
  echo "applying: $what"
  if [ "$verb" = authorize ]; then
    aws ec2 authorize-security-group-ingress --group-id "$SG" --ip-permissions "$perms" >/dev/null 2>&1 \
      || echo "  (already present)"
  else
    aws ec2 revoke-security-group-ingress --group-id "$SG" --ip-permissions "$perms" >/dev/null
  fi
done

echo; echo "checking through Cloudflare:"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://auth.workeasy360.com/.well-known/openid-configuration)"
echo "  auth discovery via Cloudflare: $code"
if [ "$code" != 200 ]; then
  echo "sign-in no longer answers through Cloudflare: rolling back" >&2
  aws ec2 authorize-security-group-ingress --group-id "$SG" --ip-permissions "file://$saved" >/dev/null 2>&1 || true
  exit 1
fi
echo "done. Direct access to the origin's address should now time out from outside Cloudflare."
