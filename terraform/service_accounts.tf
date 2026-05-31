resource "google_service_account" "collector" {
  account_id   = "quant-collector"
  display_name = "Quant Data Collector"
}

resource "google_storage_bucket_iam_member" "collector_write" {
  bucket = google_storage_bucket.quant_data.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.collector.email}"
}

# BEGIN DEPRECATED — query_api service account (retired 2026-05-31)
# resource "google_service_account" "query_api" {
#   account_id   = "quant-query-api"
#   display_name = "Quant Query API"
# }
#
# resource "google_storage_bucket_iam_member" "query_api_read" {
#   bucket = google_storage_bucket.quant_data.name
#   role   = "roles/storage.objectViewer"
#   member = "serviceAccount:${google_service_account.query_api.email}"
# }
# END DEPRECATED

resource "google_service_account" "quality" {
  account_id   = "quant-quality"
  display_name = "Quant Data Quality Checker"
}

resource "google_storage_bucket_iam_member" "quality_read" {
  bucket = google_storage_bucket.quant_data.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.quality.email}"
}
