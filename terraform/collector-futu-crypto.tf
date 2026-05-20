# =============================================================================
# Futu Crypto Collector — 5min, 7×24
# =============================================================================

resource "google_cloud_run_v2_job" "collector_futu_crypto" {
  name                = "quant-collector-futu-crypto"
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.collector.email
      containers {
        image = "gcr.io/${var.project_id}/collector-futu-crypto:latest"
        env {
          name  = "GCS_BUCKET"
          value = google_storage_bucket.quant_data.name
        }
        env {
          name  = "COLLECTOR_SOURCE"
          value = "futu_crypto"
        }
        env {
          name  = "OPEND_HOST"
          value = "127.0.0.1"
        }
        env {
          name  = "OPEND_PORT"
          value = "11111"
        }
        env {
          name  = "FUTU_LOGIN_ACCOUNT"
          value = var.futu_login_account
        }
        env {
          name  = "FUTU_LOGIN_PWD_MD5"
          value = var.futu_login_pwd_md5
        }
        resources {
          limits = {
            memory = "4Gi"
            cpu    = "2"
          }
        }
      }
      max_retries = 1
      timeout     = "600s"
    }
  }
}

resource "google_cloud_run_v2_job_iam_member" "collector_futu_crypto_invoker" {
  name     = google_cloud_run_v2_job.collector_futu_crypto.name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.collector.email}"
}

resource "google_cloud_scheduler_job" "collect_futu_crypto" {
  name             = "quant-collect-futu-crypto"
  schedule         = "*/5 * * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "600s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.collector_futu_crypto.name}:run"
    oauth_token {
      service_account_email = google_service_account.collector.email
    }
  }
}
