# =============================================================================
# Crypto (Binance) Data Pipeline
# =============================================================================

# --- Cloud Run Job: Crypto Bar Collector ---
resource "google_cloud_run_v2_job" "collector_crypto" {
  name     = "quant-collector-crypto-binance"
  location = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.collector.email
      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}/collector:latest"
        env {
          name  = "GCS_BUCKET"
          value = google_storage_bucket.quant_data.name
        }
        env {
          name  = "COLLECTOR_SOURCE"
          value = "cryptobinance"
        }
        env {
          name  = "SYMBOLS"
          value = "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT,DOGE/USDT,ADA/USDT,AVAX/USDT,DOT/USDT,LINK/USDT"
        }
        env {
          name  = "FREQUENCY"
          value = "1m"
        }
        env {
          name  = "LOOKBACK_MINUTES"
          value = "120"
        }
        resources {
          limits = {
            memory = "512Mi"
            cpu    = "1"
          }
        }
      }
      max_retries = 3
      timeout     = "600s"
    }
  }
}

# --- Cloud Scheduler: 24x7 cron trigger ---
resource "google_cloud_scheduler_job" "collect_crypto_bars" {
  name             = "quant-collect-crypto-bars"
  schedule         = "*/5 * * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "600s"

  retry_config {
    retry_count = 3
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.collector_crypto.name}:run"
    oauth_token {
      service_account_email = google_service_account.collector.email
    }
  }
}

# NOTE: crypto_bars BigQuery native table is defined in bigquery.tf
# (consistent with us_bars pattern — loaded daily by quant-bq-loader-crypto Job)
