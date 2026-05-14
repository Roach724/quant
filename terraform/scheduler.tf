resource "google_cloud_scheduler_job" "collect_minute_bars" {
  name             = "quant-collect-minute-bars"
  schedule         = "*/5 * * * 1-5"
  time_zone        = "America/New_York"
  attempt_deadline = "600s"

  retry_config {
    retry_count = 2
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.collector_yfinance.name}:run"

    oauth_token {
      service_account_email = google_service_account.collector.email
    }
  }
}
