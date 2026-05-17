# =============================================================================
# BigQuery Tables — one per (market, frequency)
# =============================================================================

resource "google_bigquery_dataset" "quant" {
  dataset_id = "quant"
  location   = var.region
}

resource "google_bigquery_table" "us_bars_5m" {
  dataset_id          = google_bigquery_dataset.quant.dataset_id
  table_id            = "us_bars_5m"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  clustering = ["symbol"]

  schema = jsonencode([
    { name = "symbol", type = "STRING" },
    { name = "timestamp", type = "TIMESTAMP" },
    { name = "open", type = "FLOAT64" },
    { name = "high", type = "FLOAT64" },
    { name = "low", type = "FLOAT64" },
    { name = "close", type = "FLOAT64" },
    { name = "volume", type = "INT64" },
    { name = "market", type = "STRING" },
    { name = "frequency", type = "STRING" },
    { name = "_ingest_time", type = "TIMESTAMP" },
  ])
}

resource "google_bigquery_table" "us_bars_1d" {
  dataset_id          = google_bigquery_dataset.quant.dataset_id
  table_id            = "us_bars_1d"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  clustering = ["symbol"]

  schema = jsonencode([
    { name = "symbol", type = "STRING" },
    { name = "timestamp", type = "TIMESTAMP" },
    { name = "open", type = "FLOAT64" },
    { name = "high", type = "FLOAT64" },
    { name = "low", type = "FLOAT64" },
    { name = "close", type = "FLOAT64" },
    { name = "volume", type = "INT64" },
    { name = "market", type = "STRING" },
    { name = "frequency", type = "STRING" },
    { name = "_ingest_time", type = "TIMESTAMP" },
  ])
}

resource "google_bigquery_table" "hk_bars_5m" {
  dataset_id          = google_bigquery_dataset.quant.dataset_id
  table_id            = "hk_bars_5m"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  clustering = ["symbol"]

  schema = jsonencode([
    { name = "symbol", type = "STRING" },
    { name = "timestamp", type = "TIMESTAMP" },
    { name = "open", type = "FLOAT64" },
    { name = "high", type = "FLOAT64" },
    { name = "low", type = "FLOAT64" },
    { name = "close", type = "FLOAT64" },
    { name = "volume", type = "INT64" },
    { name = "market", type = "STRING" },
    { name = "frequency", type = "STRING" },
    { name = "_ingest_time", type = "TIMESTAMP" },
  ])
}

resource "google_bigquery_table" "hk_bars_1d" {
  dataset_id          = google_bigquery_dataset.quant.dataset_id
  table_id            = "hk_bars_1d"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  clustering = ["symbol"]

  schema = jsonencode([
    { name = "symbol", type = "STRING" },
    { name = "timestamp", type = "TIMESTAMP" },
    { name = "open", type = "FLOAT64" },
    { name = "high", type = "FLOAT64" },
    { name = "low", type = "FLOAT64" },
    { name = "close", type = "FLOAT64" },
    { name = "volume", type = "INT64" },
    { name = "market", type = "STRING" },
    { name = "frequency", type = "STRING" },
    { name = "_ingest_time", type = "TIMESTAMP" },
  ])
}

resource "google_bigquery_table" "crypto_bars_5m" {
  dataset_id          = google_bigquery_dataset.quant.dataset_id
  table_id            = "crypto_bars_5m"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  clustering = ["symbol"]

  schema = jsonencode([
    { name = "symbol", type = "STRING" },
    { name = "timestamp", type = "TIMESTAMP" },
    { name = "open", type = "FLOAT64" },
    { name = "high", type = "FLOAT64" },
    { name = "low", type = "FLOAT64" },
    { name = "close", type = "FLOAT64" },
    { name = "volume", type = "INT64" },
    { name = "market", type = "STRING" },
    { name = "frequency", type = "STRING" },
    { name = "_ingest_time", type = "TIMESTAMP" },
  ])
}

resource "google_bigquery_table" "crypto_bars_1d" {
  dataset_id          = google_bigquery_dataset.quant.dataset_id
  table_id            = "crypto_bars_1d"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  clustering = ["symbol"]

  schema = jsonencode([
    { name = "symbol", type = "STRING" },
    { name = "timestamp", type = "TIMESTAMP" },
    { name = "open", type = "FLOAT64" },
    { name = "high", type = "FLOAT64" },
    { name = "low", type = "FLOAT64" },
    { name = "close", type = "FLOAT64" },
    { name = "volume", type = "INT64" },
    { name = "market", type = "STRING" },
    { name = "frequency", type = "STRING" },
    { name = "_ingest_time", type = "TIMESTAMP" },
  ])
}

# =============================================================================
# Service Account + IAM for BQ Loader
# =============================================================================

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

# =============================================================================
# BQ Loader Jobs — one per (market, frequency)
# =============================================================================

locals {
  bq_loaders = {
    "us-5m"     = { market = "us", frequency = "5m", table = "us_bars_5m", schedule = "0 6 * * 1-5", timezone = "America/New_York" }
    "us-1d"     = { market = "us", frequency = "1d", table = "us_bars_1d", schedule = "0 6 * * 1-5", timezone = "America/New_York" }
    "hk-5m"     = { market = "hk", frequency = "5m", table = "hk_bars_5m", schedule = "30 9 * * 1-5", timezone = "Etc/UTC" }
    "hk-1d"     = { market = "hk", frequency = "1d", table = "hk_bars_1d", schedule = "30 9 * * 1-5", timezone = "Etc/UTC" }
    "crypto-5m" = { market = "crypto", frequency = "5m", table = "crypto_bars_5m", schedule = "0 6 * * *", timezone = "Etc/UTC" }
    "crypto-1d" = { market = "crypto", frequency = "1d", table = "crypto_bars_1d", schedule = "0 1 * * *", timezone = "Etc/UTC" }
  }
}

resource "google_cloud_run_v2_job" "bq_loader" {
  for_each = local.bq_loaders

  name                = "quant-bq-loader-${each.key}"
  location            = var.region
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
          value = each.value.market
        }
        env {
          name  = "FREQUENCY"
          value = each.value.frequency
        }
        env {
          name  = "TABLE"
          value = each.value.table
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

resource "google_cloud_run_v2_job_iam_member" "bq_loader_invoker" {
  for_each = local.bq_loaders

  name     = google_cloud_run_v2_job.bq_loader[each.key].name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.bq_loader.email}"
}

resource "google_cloud_scheduler_job" "bq_load" {
  for_each = local.bq_loaders

  name             = "quant-bq-load-${each.key}"
  schedule         = each.value.schedule
  time_zone        = each.value.timezone
  attempt_deadline = "600s"

  retry_config {
    retry_count = 2
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.bq_loader[each.key].name}:run"

    oauth_token {
      service_account_email = google_service_account.bq_loader.email
    }
  }
}
