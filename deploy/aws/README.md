# Operations on AWS for the single production host

Written for: the operator of the production host, and the owner who approves each step.

Three gaps were found on 18 November 2026:

1. **No monitoring or alerting.** `deploy/single-host/README.md` says so, and the Phase 12 Prometheus rules in `deploy/observability/` were never deployed.
2. **Backups stay on the instance's own disk.** `w360-backup` keeps seven days in `/opt/workeasy360/backups`, so losing the EBS volume loses them too.
3. **The origin answers directly.** It serves HTTPS for every name without Cloudflare, and SSH is open to the internet.

This folder closes all three. **Nothing here has been applied:** that needs AWS credentials for the account and an alert address that someone reads. Each step is reversible.

| What | File | Region |
|---|---|---|
| Alert topic; encrypted backup bucket and key; CloudWatch log group, metric filters and alarms; the host's IAM policy | `ops-single-host.yml` | the host's (`ap-southeast-1`) |
| Outside-in availability: Route 53 health checks of discovery and User Admin, and their alarms | `ops-uptime-us-east-1.yml` | `us-east-1` (where Route 53 publishes its metrics) |
| Origin restricted to Cloudflare, and SSH to an admin network | `restrict-origin.sh` | the host's |
| Host: copy each nightly backup to S3, a weekly restore drill, five-minute health metrics | `deploy/single-host/w360-backup-offsite`, `w360-restore-drill`, `w360-healthwatch`, `workeasy360-ops.cron` | - |
| Host: ship the api's and workers' JSON logs to CloudWatch | `deploy/single-host/compose.cloudwatch.yml`, enabled by the `w360` wrapper | - |

## 1. The stacks (owner: approve; operator: run)

```sh
aws cloudformation deploy --region ap-southeast-1 --stack-name workeasy360-ops \
  --template-file deploy/aws/ops-single-host.yml --capabilities CAPABILITY_IAM \
  --parameter-overrides AlertEmail=<a monitored address> InstanceId=<i-...> \
                        ExistingInstanceRoleName=<the host's role, or empty to create one>
aws cloudformation deploy --region us-east-1 --stack-name workeasy360-uptime \
  --template-file deploy/aws/ops-uptime-us-east-1.yml --parameter-overrides AlertEmail=<same address>
```

**Confirm both SNS subscription emails.** Until then no alert is delivered.

If the stack created a role, associate its instance profile (the `CreatedInstanceProfile` output) with the instance: `aws ec2 associate-iam-instance-profile`. If the host already has a profile, use `ExistingInstanceRoleName` instead; the stack attaches its policy to that role. **The SSM managed policy is what allows SSH to be closed in step 4.**

## 2. The host (operator)

```sh
sudo install -m 0755 /opt/workeasy360/current/deploy/single-host/w360-backup-offsite \
  /opt/workeasy360/current/deploy/single-host/w360-restore-drill \
  /opt/workeasy360/current/deploy/single-host/w360-healthwatch /opt/workeasy360/bin/
sudo install -m 0644 /opt/workeasy360/current/deploy/single-host/workeasy360-ops.cron /etc/cron.d/workeasy360-ops
sudo tee /opt/workeasy360/env/ops.env >/dev/null <<EOF
W360_AWS_REGION=ap-southeast-1
W360_BACKUP_BUCKET=<BackupBucketName output>
W360_BACKUP_KMS_KEY=<BackupKeyArn output>
W360_LOG_GROUP=/workeasy360/production
EOF
sudo /opt/workeasy360/bin/w360-backup && sudo /opt/workeasy360/bin/w360-backup-offsite   # first copy
sudo /opt/workeasy360/bin/w360-restore-drill                                            # first drill
sudo /opt/workeasy360/bin/w360-healthwatch
```

The AWS CLI v2 must be installed on the host. Then turn on CloudWatch Logs; that restarts the services' logging:

```sh
sudo touch /opt/workeasy360/env/cloudwatch.enabled
sudo /opt/workeasy360/bin/w360 up -d api worker-critical worker-default worker-heavy beat
```

**Rollback:** remove `cloudwatch.enabled` and run the same `up -d`; remove `/etc/cron.d/workeasy360-ops`. The bucket keeps its objects (`DeletionPolicy: Retain`).

## 3. What alarms, and what it means

| Alarm | Signal | Runbook (`docs/phase-12/10-runbooks.md`) |
|---|---|---|
| `w360-host-status-check-failed` | EC2 status checks | api-or-database-outage |
| `w360-auth-discovery-down` / `w360-accounts-discovery-down` / `w360-app-down` | Route 53, through Cloudflare | api-or-database-outage |
| `w360-api-5xx` | more than 20 server errors in 5 min (`http.request`, `http_status >= 500`) | api errors |
| `w360-sso-failures` | more than 25 failed product sign-ins in 5 min (`ecosystem.sso.failed`): issuer, callback or entitlement problems | sso |
| `w360-token-issuance-failed`, `w360-signing-key-problem` | the token endpoint could not sign; no usable signing key | key management |
| `w360-logout-delivery-failed` | a product refused or never acknowledged a back-channel logout | back-channel logout (ADR-069) |
| `w360-database-unavailable` | `pg_isready` from `w360-healthwatch` | database-outage |
| `w360-backup-stale`, `w360-restore-drill-failed` | no backup in S3 for 26 h; the weekly drill did not verify | backup and restore |
| `w360-origin-certificate-expiring` | an origin certificate expires in under 14 days | edge |
| `w360-host-cpu-high`, `w360-host-disk-high` | the host | capacity |

Also recorded, without an alarm:

- `LegacyIssuerHostServed`: traffic served by `auth.`, for retiring it with `w360_issuer_readiness`;
- `SsoSucceeded`;
- `LoginThrottled`;
- `ClientAssertionReplayed`.

## 4. The origin (owner: approve; operator: run)

```sh
deploy/aws/restrict-origin.sh --region ap-southeast-1 --instance <i-...> --admin-cidr <your admin IP>/32          # dry run
deploy/aws/restrict-origin.sh --region ap-southeast-1 --instance <i-...> --admin-cidr <your admin IP>/32 --apply
```

The script works like this:

1. It saves the current rules and prints every change. It changes nothing without `--apply`.
2. It allows 80 and 443 from Cloudflare's published ranges, then removes "anywhere" for those ports.
3. It closes SSH to the internet **only when the instance is online in Systems Manager**, so administrators keep Session Manager as a way in. Otherwise it leaves port 22 alone and says so.
4. After applying, it checks the sign-in through Cloudflare, and rolls back by itself if that fails.

To undo by hand: `--rollback <saved file>`.

Before running it, confirm that nothing legitimate reaches the origin by its address. A health checker or integration calling `18.143.87.44` directly would stop working; everything should use the public names. Also remove the origin's address from `docs/ecosystem-complete/status.md` in the public repository. It stays in history, which is one more reason for this step.
