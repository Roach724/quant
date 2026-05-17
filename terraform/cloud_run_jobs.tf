resource "google_cloud_run_v2_job" "collector_yfinance" {
  name                = "quant-collector-yfinance"
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
          value = "yfinance"
        }
        env {
          name  = "SYMBOLS"
          value = "SPY,AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA"
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

resource "google_cloud_run_v2_job_iam_member" "collector_yfinance_invoker" {
  name     = google_cloud_run_v2_job.collector_yfinance.name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.collector.email}"
}
