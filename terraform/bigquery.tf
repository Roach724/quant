resource "google_bigquery_dataset" "quant" {
  dataset_id = "quant"
  location   = var.region
}

resource "google_bigquery_table" "us_bars" {
  dataset_id = google_bigquery_dataset.quant.dataset_id
  table_id   = "us_bars"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  clustering = ["symbol"]

  schema = jsonencode([
    { name = "symbol",    type = "STRING" },
    { name = "timestamp", type = "TIMESTAMP" },
    { name = "open",      type = "FLOAT64" },
    { name = "high",      type = "FLOAT64" },
    { name = "low",       type = "FLOAT64" },
    { name = "close",     type = "FLOAT64" },
    { name = "volume",    type = "INT64" },
    { name = "market",    type = "STRING" },
    { name = "frequency", type = "STRING" },
  ])
}

resource "google_service_account" "bq_loader" {
  account_id   = "quant-bq-loader"
  display_name = "Quant BigQuery Data Loader"
}

resource "google_storage_bucket_iam_member" "bq_loader_read" {
  bucket = google_storage_bucket.quant_data.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.bq_loader.email}"
}

resource "google_bigquery_dataset_iam_member" "bq_loader_editor" {
  dataset_id = google_bigquery_dataset.quant.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.bq_loader.email}"
}

resource "google_project_iam_member" "bq_loader_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.bq_loader.email}"
}

resource "google_cloud_run_v2_job" "bq_loader" {
  name               = "quant-bq-loader"
  location           = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.bq_loader.email
      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}/bq-loader:latest"
        env {
          name  = "GCS_BUCKET"
          value = google_storage_bucket.quant_data.name
        }
        env {
          name  = "GCP_PROJECT"
          value = var.project_id
        }
        env {
          name  = "MARKET"
          value = "us"
        }
        env {
          name  = "TABLE"
          value = "us_bars"
        }
        env {
          name  = "LOAD_DAYS"
          value = "7"
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

resource "google_cloud_scheduler_job" "bq_load_daily" {
  name             = "quant-bq-load-daily"
  schedule         = "0 6 * * 1-5"
  time_zone        = "America/New_York"
  attempt_deadline = "600s"

  retry_config {
    retry_count = 2
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.bq_loader.name}:run"

    oauth_token {
      service_account_email = google_service_account.bq_loader.email
    }
  }
}

# =============================================================================
# Crypto BigQuery Native Table + Loader
# =============================================================================

resource "google_bigquery_table" "crypto_bars" {
  dataset_id = google_bigquery_dataset.quant.dataset_id
  table_id   = "crypto_bars"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  clustering = ["symbol"]

  schema = jsonencode([
    { name = "symbol",    type = "STRING" },
    { name = "timestamp", type = "TIMESTAMP" },
    { name = "open",      type = "FLOAT64" },
    { name = "high",      type = "FLOAT64" },
    { name = "low",       type = "FLOAT64" },
    { name = "close",     type = "FLOAT64" },
    { name = "volume",    type = "INT64" },
    { name = "market",    type = "STRING" },
    { name = "frequency", type = "STRING" },
  ])
}

resource "google_cloud_run_v2_job" "bq_loader_crypto" {
  name               = "quant-bq-loader-crypto"
  location           = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.bq_loader.email
      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}/bq-loader:latest"
        env {
          name  = "GCS_BUCKET"
          value = google_storage_bucket.quant_data.name
        }
        env {
          name  = "GCP_PROJECT"
          value = var.project_id
        }
        env {
          name  = "MARKET"
          value = "crypto"
        }
        env {
          name  = "TABLE"
          value = "crypto_bars"
        }
        env {
          name  = "LOAD_DAYS"
          value = "7"
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

resource "google_cloud_scheduler_job" "bq_load_crypto_daily" {
  name             = "quant-bq-load-crypto-daily"
  schedule         = "0 6 * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "600s"

  retry_config {
    retry_count = 2
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.bq_loader_crypto.name}:run"

    oauth_token {
      service_account_email = google_service_account.bq_loader.email
    }
  }
}

# =============================================================================
# Hong Kong BigQuery Native Table + Loader
# =============================================================================

resource "google_bigquery_table" "hk_bars" {
  dataset_id = google_bigquery_dataset.quant.dataset_id
  table_id   = "hk_bars"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  clustering = ["symbol"]

  schema = jsonencode([
    { name = "symbol",    type = "STRING" },
    { name = "timestamp", type = "TIMESTAMP" },
    { name = "open",      type = "FLOAT64" },
    { name = "high",      type = "FLOAT64" },
    { name = "low",       type = "FLOAT64" },
    { name = "close",     type = "FLOAT64" },
    { name = "volume",    type = "INT64" },
    { name = "market",    type = "STRING" },
    { name = "frequency", type = "STRING" },
  ])
}

resource "google_cloud_run_v2_job" "bq_loader_hk" {
  name     = "quant-bq-loader-hk"
  location = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.bq_loader.email
      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}/bq-loader:latest"
        env {
          name  = "GCS_BUCKET"
          value = google_storage_bucket.quant_data.name
        }
        env {
          name  = "GCP_PROJECT"
          value = var.project_id
        }
        env {
          name  = "MARKET"
          value = "hk"
        }
        env {
          name  = "TABLE"
          value = "hk_bars"
        }
        env {
          name  = "LOAD_DAYS"
          value = "7"
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

resource "google_cloud_scheduler_job" "bq_load_hk_daily" {
  name             = "quant-bq-load-hk-daily"
  schedule         = "30 9 * * 1-5"
  time_zone        = "Etc/UTC"
  attempt_deadline = "600s"

  retry_config {
    retry_count = 2
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.bq_loader_hk.name}:run"

    oauth_token {
      service_account_email = google_service_account.bq_loader.email
    }
  }
}
