# Operations & Maintenance Guide

How to deploy, monitor, and maintain the quant trading infrastructure.

## System Architecture

```
┌──────────────────────────────────────────────────────────┐
│ Google Cloud Platform                                    │
│                                                          │
│  Cloud Scheduler (cron)                                  │
│       │                                                  │
│       ▼                                                  │
│  Cloud Run Jobs ──→ GCS (Parquet) ──→ BigQuery          │
│  (collectors)        (data lake)       (analytics)       │
│                                                          │
│  Cloud Run Service   ←── queries GCS                     │
│  (Go query API)      ──→ JSON/Parquet                    │
│                                                          │
│  Cloud Run Jobs      ←── reads GCS                       │
│  (BQ loader)         ──→ BigQuery tables                 │
└──────────────────────────────────────────────────────────┘
```

**Key services:**

| Service | Type | Schedule | Region |
|---------|------|----------|--------|
| `quant-collector-yfinance` | Cloud Run Job | Every 5 min (Mon-Fri) | asia-east2 |
| `quant-query-api` | Cloud Run Service | On-demand (scales to zero) | asia-east2 |
| `quant-bq-loader` | Cloud Run Job | Daily 6am ET (Mon-Fri) | asia-east2 |
| `quant-bq-load-daily` | Cloud Scheduler | `0 6 * * 1-5` | asia-east2 |
| `quant-collect-minute-bars` | Cloud Scheduler | `*/5 * * * 1-5` | asia-east2 |

## Deployment

### Prerequisites

```bash
# Install tools
gcloud auth login                          # Authenticate
gcloud auth application-default login      # For Terraform + SDKs
gcloud config set project <PROJECT_ID>

# Verify
gcloud config get-value project
gcloud auth list
```

### First-time Deployment

```bash
cd terraform

# 1. Create state bucket (one-time)
gcloud storage buckets create gs://<PROJECT>-quant-terraform-state --location=asia-east2

# 2. Create terraform.tfvars
cat > terraform.tfvars << EOF
project_id  = "<YOUR_PROJECT_ID>"
region      = "asia-east2"
environment = "dev"
EOF

# 3. Initialize
terraform init

# 4. Review plan
terraform plan -var-file=terraform.tfvars

# 5. Apply
terraform apply -var-file=terraform.tfvars
```

### Building & Pushing Docker Images

```bash
# Authenticate Docker
gcloud auth configure-docker asia-east2-docker.pkg.dev --quiet

# Build all images
docker build -t asia-east2-docker.pkg.dev/<PROJECT>/quant/collector:latest -f collectors/Dockerfile collectors/
docker build -t asia-east2-docker.pkg.dev/<PROJECT>/quant/query-api:latest -f query-api/Dockerfile query-api/
docker build -t asia-east2-docker.pkg.dev/<PROJECT>/quant/bq-loader:latest -f bigquery_loader/Dockerfile bigquery_loader/

# Push
docker push asia-east2-docker.pkg.dev/<PROJECT>/quant/collector:latest
docker push asia-east2-docker.pkg.dev/<PROJECT>/quant/query-api:latest
docker push asia-east2-docker.pkg.dev/<PROJECT>/quant/bq-loader:latest
```

### Deploy Updated Images

```bash
# Update collector
gcloud run jobs update quant-collector-yfinance --region=asia-east2 \
  --image=asia-east2-docker.pkg.dev/<PROJECT>/quant/collector:latest

# Update query API
gcloud run services update quant-query-api --region=asia-east2 \
  --image=asia-east2-docker.pkg.dev/<PROJECT>/quant/query-api:latest

# Update BQ loader
gcloud run jobs update quant-bq-loader --region=asia-east2 \
  --image=asia-east2-docker.pkg.dev/<PROJECT>/quant/bq-loader:latest

# Verify
gcloud run services list --region=asia-east2
gcloud run jobs list --region=asia-east2
```

## Monitoring Data Collection

### Check collector status

```bash
# Last execution
gcloud run jobs executions list --job=quant-collector-yfinance --region=asia-east2 --limit=5

# View logs
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=quant-collector-yfinance" --limit=20

# Manually trigger
gcloud run jobs execute quant-collector-yfinance --region=asia-east2 --wait
```

### Verify data in GCS

```bash
# Today's data
gcloud storage ls "gs://<PROJECT>-quant-data/raw/us/bars/year=$(date +%Y)/month=$(date +%m)/day=$(date +%d)/" --recursive

# Count files
gcloud storage ls "gs://<PROJECT>-quant-data/raw/us/bars/" --recursive | wc -l
```

### Verify BigQuery

```bash
# Row counts
bq query --nouse_legacy_sql "
  SELECT DATE(timestamp) as dt, symbol, COUNT(*) as bars
  FROM quant.us_bars
  WHERE DATE(timestamp) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
  GROUP BY dt, symbol ORDER BY dt DESC, symbol
"

# Data freshness
bq query --nouse_legacy_sql "
  SELECT symbol, MAX(timestamp) as latest
  FROM quant.us_bars
  GROUP BY symbol ORDER BY latest DESC
"
```

### Query API health

```bash
TOKEN=$(gcloud auth print-identity-token)
API_URL=$(gcloud run services describe quant-query-api --region=asia-east2 --format="value(status.url)")

# Health check
curl -sH "Authorization: Bearer $TOKEN" "$API_URL/health"

# Available symbols
curl -sH "Authorization: Bearer $TOKEN" "$API_URL/api/v1/symbols?market=us"

# Query bars
curl -sH "Authorization: Bearer $TOKEN" \
  "$API_URL/api/v1/bars?market=us&symbols=AAPL,SPY&start=2026-05-15T00:00:00Z&end=2026-05-16T00:00:00Z"
```

## Changing Configuration

### Adding new symbols

Edit `terraform/cloud_run_jobs.tf`, update the `SYMBOLS` env var:

```hcl
env {
  name  = "SYMBOLS"
  value = "SPY,AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA,QQQ,IWM"  # Added QQQ, IWM
}
```

Then apply:

```bash
cd terraform && terraform apply -var-file=terraform.tfvars -auto-approve
```

### Changing collection frequency

Edit `terraform/scheduler.tf`:

```hcl
# Every 2 minutes (was every 5)
schedule = "*/2 * * * 1-5"
```

### Adjusting BigQuery load window

Edit `terraform/cloud_run_jobs.tf`, change `LOAD_DAYS`:

```hcl
env {
  name  = "LOAD_DAYS"
  value = "30"  # Load 30 days instead of 7
}
```

## Troubleshooting

### Collector returns no data

```bash
# Check execution logs for errors
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=quant-collector-yfinance AND severity>=ERROR" --limit=10

# Common causes:
# 1. Yahoo Finance API rate limit → wait, it auto-retries
# 2. Outside market hours → no data is normal on weekends
# 3. GCS permission denied → check service account IAM
```

### Query API returns 403

```bash
# Get a fresh token
gcloud auth print-identity-token

# The token expires after ~1 hour. Regenerate or use:
TOKEN=$(gcloud auth print-identity-token)
```

### BigQuery table empty after load

```bash
# Check BQ loader logs
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=quant-bq-loader" --limit=20

# Manually trigger
gcloud run jobs execute quant-bq-loader --region=asia-east2 --wait

# Common causes:
# 1. No Parquet files for the load window → check GCS
# 2. Incompatible schema → check LOAD_DAYS timeframe
```

### Terraform state locked

```bash
# If terraform apply fails with "state locked":
terraform force-unlock <LOCK_ID>
```

### Docker push fails with 403

```bash
# Re-authenticate
gcloud auth configure-docker asia-east2-docker.pkg.dev --quiet

# If using multiple accounts, ensure the right one is active:
gcloud config set account admin@your-project.com
```

## Cost Management

Approximate monthly costs (dev environment):

| Resource | Cost |
|----------|------|
| Cloud Storage | ~$2 (100 GB) |
| Cloud Run (API) | ~$0 (free tier) |
| Cloud Run Jobs | ~$3-5 |
| Cloud Scheduler | ~$0 (3 free jobs) |
| BigQuery | ~$1-3 |
| Artifact Registry | ~$1 |
| **Total** | **~$8-12/month** |

**Cost optimization tips:**
- GCS lifecycle rule auto-transitions files to NEARLINE after 90 days
- Cloud Run scales to zero when idle (no cost for query API outside queries)
- BigQuery queries on small tables: use `LIMIT` and date filters
- Docker images: delete old versions periodically

## Backup & Recovery

### Terraform state (critical)

State is stored in GCS bucket `*-quant-terraform-state` with automatic versioning. To recover:

```bash
# List versions
gcloud storage ls -a gs://<PROJECT>-quant-terraform-state/terraform/state/default.tfstate

# Download a specific version
gcloud storage cp gs://<PROJECT>-quant-terraform-state/terraform/state/default.tfstate#<GENERATION> .
```

### GCS data

Bucket versioning is enabled. Previous object versions are preserved.

### Re-deploy from scratch

```bash
# 1. Apply terraform (recreates all infrastructure)
cd terraform && terraform apply -var-file=terraform.tfvars

# 2. Build and push images (see above)

# 3. Data collection resumes automatically (Cloud Scheduler already configured)
```

## Dashboard for Operations

Start the operations dashboard:

```bash
pip install fastapi uvicorn
python -c "
from dashboard.api import configure, app
import uvicorn
# For ops monitoring, just check broker connectivity
configure()  # No broker → shows offline status
uvicorn.run(app, port=8090)
"
```

The dashboard's status indicator shows whether the broker is connected. Use this as a health check during live trading sessions.

## Run All Tests

Before any deployment:

```bash
cd D:/quant
python -m pytest oms/tests/ engine/tests/ collectors/tests/ sdk/tests/ quality/tests/ -v -k "not vcr"
```

Expected: 104 passed, 2 deselected (VCR tests require network).
