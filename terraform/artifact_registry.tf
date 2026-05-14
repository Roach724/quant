resource "google_artifact_registry_repository" "docker" {
  repository_id = "quant"
  format        = "DOCKER"
  location      = var.region
}
