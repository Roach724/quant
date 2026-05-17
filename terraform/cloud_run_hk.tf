# =============================================================================
# Hong Kong Data Pipeline
# =============================================================================

# --- Cloud Run Job: HK Daily Bar Collector ---
resource "google_cloud_run_v2_job" "collector_hk_daily" {
  name                = "quant-collector-hk-daily"
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
          value = "0700.HK,9988.HK,3690.HK,9618.HK,9999.HK,9888.HK,2015.HK,9868.HK,1810.HK,1024.HK,9626.HK,0005.HK,0388.HK,1299.HK,2318.HK,3968.HK,1398.HK,3988.HK,2628.HK,0011.HK,0001.HK,0002.HK,0003.HK,0016.HK,0027.HK,0175.HK,0267.HK,0291.HK,0669.HK,0823.HK,0883.HK,0941.HK,1044.HK,1093.HK,1177.HK,1928.HK,2269.HK"
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

# --- Cloud Run Job: HK Minute Bar Collector ---
resource "google_cloud_run_v2_job_iam_member" "collector_hk_daily_invoker" {
  name     = google_cloud_run_v2_job.collector_hk_daily.name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.collector.email}"
}

resource "google_cloud_run_v2_job" "collector_hk_minute" {
  name                = "quant-collector-hk-minute"
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

resource "google_cloud_run_v2_job_iam_member" "collector_hk_minute_invoker" {
  name     = google_cloud_run_v2_job.collector_hk_minute.name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.collector.email}"
}

# --- Cloud Scheduler: HK Daily (Mon-Fri, after market close HKT) ---
resource "google_cloud_scheduler_job" "collect_hk_daily" {
  name             = "quant-collect-hk-daily"
  schedule         = "0 9 * * 1-5"
  time_zone        = "Etc/UTC"
  attempt_deadline = "600s"

  retry_config {
    retry_count = 3
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.collector_hk_daily.name}:run"
    oauth_token {
      service_account_email = google_service_account.collector.email
    }
  }
}

# --- Cloud Scheduler: HK Minute (Mon-Fri, every 5 min during market) ---
resource "google_cloud_scheduler_job" "collect_hk_minute" {
  name             = "quant-collect-hk-minute"
  schedule         = "*/5 1-8 * * 1-5"
  time_zone        = "Etc/UTC"
  attempt_deadline = "600s"

  retry_config {
    retry_count = 3
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.collector_hk_minute.name}:run"
    oauth_token {
      service_account_email = google_service_account.collector.email
    }
  }
}
