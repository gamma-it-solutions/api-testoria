# Database Backups — Runbook

> **Scope:** This documents the PostgreSQL backup setup for the **backend** (`api-testoria`)
> running on the production EC2 instance. The web frontend has no database of its own;
> this runbook lives here for operational convenience.

---

## Overview

The production stack runs as a `docker compose` project (`api-testoria`) on a single EC2
instance in **eu-central-1 (Frankfurt)**. The database is **PostgreSQL 16** in a container,
and its data lives in a Docker named volume on a dedicated EBS data volume.

Backups are **logical dumps** (`pg_dump`, compressed custom format) taken nightly by cron,
uploaded to an **S3 bucket**, and **auto-expired after 90 days** via an S3 lifecycle rule.

```
┌──────────────────────── EC2 instance (eu-central-1) ────────────────────────┐
│                                                                              │
│   cron 03:00  ──►  ~/backup-db.sh  ──►  pg_dump (container)  ──►  /tmp/*.dump │
│                                                  │                           │
│                                                  └──►  aws s3 cp  ───────────┼──►  s3://testoria-db-backups
│                                                                              │         (lifecycle: delete > 90d)
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Where things live

### On the EC2 instance

| What | Location |
|---|---|
| Compose project dir | `~/api-testoria/` |
| Backup script | `~/backup-db.sh` |
| Cron log | `~/backup.log` |
| Temp dump (transient) | `/tmp/testoria-<timestamp>.dump` (deleted after upload) |

### Storage volumes (`lsblk`)

| Device | Size | Mountpoint | Purpose |
|---|---|---|---|
| `nvme0n1` | 30G | `/` | Root — OS, app code |
| `nvme1n1` | 50G | `/var/lib/docker` | **All Docker data, including the DB** |

### The database

| Property | Value |
|---|---|
| Container | `api-testoria-postgres-1` |
| Image | `postgres:16-alpine` |
| DB user | `testoria` |
| DB name | `testoria` |
| Docker volume | `api-testoria_postgres_data` |
| Host data path | `/var/lib/docker/volumes/api-testoria_postgres_data/_data` |
| In-container data dir | `/var/lib/postgresql/data` |

Credentials come from the `env.prod` file referenced by the compose `env_file:`.
Confirm the live values with:

```bash
docker exec api-testoria-postgres-1 env | grep POSTGRES
```

### S3 backup bucket

| Property | Value |
|---|---|
| Bucket | `s3://testoria-db-backups` |
| Region | `eu-central-1` |
| Retention | Delete objects older than **90 days** (lifecycle rule `expire-old-db-backups`) |
| Object naming | `testoria-<YYYY-MM-DD-HHMM>.dump` |

### AWS authentication

The instance authenticates to S3 via an **IAM role** attached to the EC2 instance —
**no access keys are stored on the box**.

| Property | Value |
|---|---|
| IAM role | `testoria-ec2-backup-role` |
| Trust | `ec2.amazonaws.com` can `sts:AssumeRole` |
| Permissions | `s3:PutObject`, `s3:GetObject`, `s3:ListBucket` on the backup bucket (or `AmazonS3FullAccess`) |

Verify the instance has working credentials:

```bash
aws sts get-caller-identity      # should show the testoria-ec2-backup-role ARN
```

---

## How a backup runs

1. Cron fires `~/backup-db.sh` daily at **03:00** (instance timezone, typically UTC).
2. The script runs `pg_dump -Fc` inside the Postgres container → writes a compressed dump to `/tmp`.
3. It aborts if the dump is empty (sanity check).
4. It uploads the dump to S3.
5. It deletes the local temp file.
6. Output (success/failure) is appended to `~/backup.log`.
7. S3's lifecycle rule deletes any dump older than 90 days automatically.

### The backup script (`~/backup-db.sh`)

```bash
#!/usr/bin/env bash
set -euo pipefail

CONTAINER="api-testoria-postgres-1"
DB_USER="testoria"
DB_NAME="testoria"
BUCKET="s3://testoria-db-backups"
REGION="eu-central-1"

STAMP=$(date +%F-%H%M)
FILE="/tmp/testoria-${STAMP}.dump"

# 1. dump (compressed custom format)
docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc > "$FILE"

# 2. sanity check — fail loudly if the dump is empty
if [ ! -s "$FILE" ]; then
  echo "ERROR: dump is empty, aborting" >&2
  exit 1
fi

# 3. upload to S3
aws s3 cp "$FILE" "$BUCKET/" --region "$REGION"

# 4. clean up local temp file
rm -f "$FILE"

echo "OK: backed up ${DB_NAME} -> ${BUCKET}/testoria-${STAMP}.dump"
```

### The cron entry (`crontab -e`)

```cron
0 3 * * * /home/ubuntu/backup-db.sh >> /home/ubuntu/backup.log 2>&1
```

### The S3 lifecycle rule (`/tmp/lifecycle.json`)

```json
{
  "Rules": [
    {
      "ID": "expire-old-db-backups",
      "Status": "Enabled",
      "Filter": { "Prefix": "" },
      "Expiration": { "Days": 90 }
    }
  ]
}
```

Applied with:

```bash
aws s3api put-bucket-lifecycle-configuration \
  --bucket testoria-db-backups \
  --lifecycle-configuration file:///tmp/lifecycle.json \
  --region eu-central-1
```

---

## Common operations

### Take a backup manually, right now

```bash
~/backup-db.sh
```

Or the raw command:

```bash
docker exec api-testoria-postgres-1 \
  pg_dump -U testoria -d testoria -Fc \
  > ~/testoria-$(date +%F-%H%M).dump
```

### List backups in S3

```bash
aws s3 ls s3://testoria-db-backups/ --human-readable
```

### Download a specific backup

```bash
aws s3 cp s3://testoria-db-backups/testoria-2026-06-02-0629.dump ./
```

### Inspect a dump without restoring it

```bash
docker exec -i api-testoria-postgres-1 pg_restore -l < testoria-2026-06-02-0629.dump | head
```

### Check the cron log

```bash
tail -n 20 ~/backup.log
```

### Check DB / volume disk usage

```bash
sudo du -sh /var/lib/docker/volumes/api-testoria_postgres_data/_data   # data on disk
df -hT /var/lib/docker                                                  # volume free space
docker exec -it api-testoria-postgres-1 psql -U testoria -c "\l+"       # logical DB sizes
```

---

## Restore

> ⚠️ Restoring overwrites data. Always test against a throwaway DB first.

### Safe test restore (into a temporary database)

```bash
docker exec api-testoria-postgres-1 createdb -U testoria testoria_restore_test
cat testoria-<timestamp>.dump | docker exec -i api-testoria-postgres-1 \
  pg_restore -U testoria -d testoria_restore_test
# ... verify ...
docker exec api-testoria-postgres-1 dropdb -U testoria testoria_restore_test
```

### Full restore into the live database

```bash
# pull the backup from S3 first if needed
aws s3 cp s3://testoria-db-backups/testoria-<timestamp>.dump ./

# restore, dropping/recreating existing objects
cat testoria-<timestamp>.dump | docker exec -i api-testoria-postgres-1 \
  pg_restore -U testoria -d testoria --clean --if-exists
```

For a totally clean rebuild, drop and recreate the DB before restoring:

```bash
docker exec api-testoria-postgres-1 dropdb -U testoria testoria
docker exec api-testoria-postgres-1 createdb -U testoria testoria
cat testoria-<timestamp>.dump | docker exec -i api-testoria-postgres-1 \
  pg_restore -U testoria -d testoria
```

---

## Maintenance & verification

| Cadence | Task |
|---|---|
| Daily (automatic) | Cron backup runs at 03:00 |
| Weekly | `tail ~/backup.log` — confirm recent "OK:" lines, no errors |
| Monthly | Do a **test restore** into a throwaway DB to prove backups are restorable |
| Monthly | `aws s3 ls s3://testoria-db-backups/` — confirm new dumps land and old ones expire |
| As needed | Review retention (`Expiration: Days`) and bucket size |

### Verify the lifecycle rule is active

```bash
aws s3api get-bucket-lifecycle-configuration \
  --bucket testoria-db-backups --region eu-central-1
```

### Change retention (e.g. to 30 or 180 days)

Edit `/tmp/lifecycle.json`, set `"Days"` to the desired value, re-run the `put-bucket-lifecycle-configuration`
command above. Note: `put` **replaces** the whole rule set — include every rule you want kept.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `role "postgres" does not exist` | Wrong user — use `-U testoria` (check `docker exec ... env \| grep POSTGRES`) |
| `Unable to locate credentials` | IAM role not attached to instance, or propagation delay. Re-attach via **EC2 → Actions → Security → Modify IAM role**; retry after ~30s |
| `AccessDenied` on upload | IAM policy missing `s3:PutObject` on the bucket |
| Dump is 0 bytes | Container down or wrong DB name — check `docker ps` and the user/db |
| `BucketAlreadyExists` (on create) | Bucket names are global; pick a unique name and update the script + lifecycle |
| Backup volume filling up | Lower retention days, or DB has grown — check `docker exec ... psql -c "\l+"` |

---

## Improvements / future work

- **Off-region / off-account copy** — current backups live in one region; consider S3
  cross-region replication or a copy to a separate AWS account for disaster resilience.
- **Tiered storage** — transition older dumps to `STANDARD_IA` / `GLACIER` before deletion
  to cut cost if dumps grow large.
- **Bucket hardening** — ensure *Block all public access* is ON; consider versioning +
  `NoncurrentVersionExpiration`.
- **Monitoring/alerting** — alert if no successful backup in 24h (e.g. CloudWatch, or a
  heartbeat ping from the script).
- **Encryption** — enable default SSE on the bucket (SSE-S3 or SSE-KMS).
