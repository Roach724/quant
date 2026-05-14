output "gcs_bucket_name" {
  value = google_storage_bucket.quant_data.name
}

output "collector_service_account_email" {
  value = google_service_account.collector.email
}

output "query_api_service_account_email" {
  value = google_service_account.query_api.email
}

output "artifact_registry_repository" {
  value = google_artifact_registry_repository.docker.repository_id
}
