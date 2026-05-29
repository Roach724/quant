# =============================================================================
# ⛔ DEPRECATED as of 2026-05-29
# All Cloud Run collector jobs have been retired.
# Data collection now runs exclusively via VM cron + ws_collector systemd daemon.
# These resources are kept as reference only — do NOT terraform apply.
# =============================================================================

# =============================================================================
# US Market Collectors — 5m + 1d
# =============================================================================

# BEGIN DEPRECATED
# # --- US 5m Collector ---
# resource "google_cloud_run_v2_job" "collector_us_5m" {
#   name                = "quant-collector-us-5m"
#   location            = var.region
#   deletion_protection = false
# 
#   template {
#     template {
#       service_account = google_service_account.collector.email
#       containers {
#         image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}/collector:latest"
#         env {
#           name  = "GCS_BUCKET"
#           value = google_storage_bucket.quant_data.name
#         }
#         env {
#           name  = "COLLECTOR_SOURCE"
#           value = "yfinance"
#         }
#         env {
#           name  = "SYMBOLS"
#           value = "SPY,AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA"
#         }
#         env {
#           name  = "FREQUENCY"
#           value = "5m"
#         }
#         env {
#           name  = "LOOKBACK_MINUTES"
#           value = "120"
#         }
#         resources {
#           limits = {
#             memory = "512Mi"
#             cpu    = "1"
#           }
#         }
#       }
#       max_retries = 3
#       timeout     = "600s"
#     }
#   }
# }
# 
# resource "google_cloud_run_v2_job_iam_member" "collector_us_5m_invoker" {
#   name     = google_cloud_run_v2_job.collector_us_5m.name
#   location = var.region
#   project  = var.project_id
#   role     = "roles/run.invoker"
#   member   = "serviceAccount:${google_service_account.collector.email}"
# }
# 
# resource "google_cloud_scheduler_job" "collect_us_5m" {
#   name             = "quant-collect-us-5m"
#   schedule         = "*/5 * * * 1-5"
#   time_zone        = "America/New_York"
#   attempt_deadline = "600s"
# 
#   retry_config {
#     retry_count = 2
#   }
# 
#   http_target {
#     http_method = "POST"
#     uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.collector_us_5m.name}:run"
# 
#     oauth_token {
#       service_account_email = google_service_account.collector.email
#     }
#   }
# }
# END DEPRECATED

# BEGIN DEPRECATED
# # --- US 1d Collector ---
# resource "google_cloud_run_v2_job" "collector_us_1d" {
#   name                = "quant-collector-us-1d"
#   location            = var.region
#   deletion_protection = false
# 
#   template {
#     template {
#       service_account = google_service_account.collector.email
#       containers {
#         image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}/collector:latest"
#         env {
#           name  = "GCS_BUCKET"
#           value = google_storage_bucket.quant_data.name
#         }
#         env {
#           name  = "COLLECTOR_SOURCE"
#           value = "yfinance"
#         }
#         env {
#           name  = "FREQUENCY"
#           value = "1d"
#         }
#         env {
#           name  = "LOOKBACK_MINUTES"
#           value = "1440"
#         }
#         resources {
#           limits = {
#             memory = "512Mi"
#             cpu    = "1"
#           }
#         }
#       }
#       max_retries = 3
#       timeout     = "600s"
#     }
#   }
# }
# 
# resource "google_cloud_run_v2_job_iam_member" "collector_us_1d_invoker" {
#   name     = google_cloud_run_v2_job.collector_us_1d.name
#   location = var.region
#   project  = var.project_id
#   role     = "roles/run.invoker"
#   member   = "serviceAccount:${google_service_account.collector.email}"
# }
# 
# resource "google_cloud_scheduler_job" "collect_us_1d" {
#   name             = "quant-collect-us-1d"
#   schedule         = "0 17 * * 1-5"
#   time_zone        = "America/New_York"
#   attempt_deadline = "600s"
# 
#   retry_config {
#     retry_count = 2
#   }
# 
#   http_target {
#     http_method = "POST"
#     uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.collector_us_1d.name}:run"
# 
#     oauth_token {
#       service_account_email = google_service_account.collector.email
#     }
#   }
# }
# END DEPRECATED
