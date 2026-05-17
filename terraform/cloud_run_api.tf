resource "google_cloud_run_v2_service" "query_api" {
  name                = "quant-query-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.query_api.email
    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}/query-api:latest"
      env {
        name  = "GCS_BUCKET"
        value = google_storage_bucket.quant_data.name
      }
      resources {
        limits = {
          memory = "512Mi"
          cpu    = "1"
        }
      }
    }
    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }
  }
}

# Public access blocked by org policy. Use authenticated access via gcloud auth.
# resource "google_cloud_run_v2_service_iam_member" "query_api_public" {
#   name     = google_cloud_run_v2_service.query_api.name
#   location = var.region
#   role     = "roles/run.invoker"
#   member   = "allUsers"
# }
