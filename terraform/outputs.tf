output "gcs_bucket_name" {
  value = google_storage_bucket.quant_data.name
}

output "collector_service_account_email" {
  value = google_service_account.collector.email
}

# BEGIN DEPRECATED — query_api service account (retired 2026-05-31)
# output "query_api_service_account_email" {
#   value = google_service_account.query_api.email
# }
# END DEPRECATED

output "artifact_registry_repository" {
  value = google_artifact_registry_repository.docker.repository_id
}

output "quality_service_account_email" {
  value = google_service_account.quality.email
}
