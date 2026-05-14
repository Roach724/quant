resource "google_cloud_run_v2_service" "query_api" {
  name     = "quant-query-api"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

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
          memory = "256Mi"
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

resource "google_cloud_run_v2_service_iam_member" "query_api_public" {
  name     = google_cloud_run_v2_service.query_api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
