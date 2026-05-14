resource "google_project_service" "required" {
  for_each = toset([
    "storage.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
    "cloudscheduler.googleapis.com",
    "run.googleapis.com",
    "bigquery.googleapis.com",
  ])
  project = var.project_id
  service = each.value

  disable_on_destroy = false
}
