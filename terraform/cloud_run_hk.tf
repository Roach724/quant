# =============================================================================
# Hong Kong Data Pipeline — 5m + 1d
# =============================================================================

# --- HK 5m Collector ---
resource "google_cloud_run_v2_job" "collector_hk_5m" {
  name                = "quant-collector-hk-5m"
  location            = var.region
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
          value = "yfinancehk"
        }
        env {
          name  = "SYMBOLS"
          value = "0700.HK,9988.HK,3690.HK,0005.HK,0388.HK,1299.HK,2318.HK,0941.HK,0883.HK,1810.HK"
        }
        env {
          name  = "FREQUENCY"
          value = "5m"
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

resource "google_cloud_run_v2_job_iam_member" "collector_hk_5m_invoker" {
  name     = google_cloud_run_v2_job.collector_hk_5m.name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.collector.email}"
}

resource "google_cloud_scheduler_job" "collect_hk_5m" {
  name             = "quant-collect-hk-5m"
  schedule         = "*/5 1-8 * * 1-5"
  time_zone        = "Etc/UTC"
  attempt_deadline = "600s"

  retry_config {
    retry_count = 3
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.collector_hk_5m.name}:run"
    oauth_token {
      service_account_email = google_service_account.collector.email
    }
  }
}

# --- HK 1d Collector ---
# Symbols are auto-discovered at runtime via akshare stock_hk_ggt_components_em()
# with liquidity filters (price >= 1.0, turnover >= 1M HKD).
# YFinanceHKAdapter uses akshare as fallback when yfinance returns < 5 rows.
resource "google_cloud_run_v2_job" "collector_hk_1d" {
  name                = "quant-collector-hk-1d"
  location            = var.region
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
          value = "yfinancehk"
        }
        env {
          name  = "FREQUENCY"
          value = "1d"
        }
        env {
          name  = "LOOKBACK_MINUTES"
          value = "1440"
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

resource "google_cloud_run_v2_job_iam_member" "collector_hk_1d_invoker" {
  name     = google_cloud_run_v2_job.collector_hk_1d.name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.collector.email}"
}

resource "google_cloud_scheduler_job" "collect_hk_1d" {
  name             = "quant-collect-hk-1d"
  schedule         = "0 9 * * 1-5"
  time_zone        = "Etc/UTC"
  attempt_deadline = "600s"

  retry_config {
    retry_count = 3
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.collector_hk_1d.name}:run"
    oauth_token {
      service_account_email = google_service_account.collector.email
    }
  }
}
