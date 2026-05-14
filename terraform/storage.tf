resource "google_storage_bucket" "quant_data" {
  name                        = "${var.project_id}-quant-data"
  location                    = var.region
  force_destroy               = var.environment != "prod"
  uniform_bucket_level_access = true

  lifecycle_rule {
    condition { age = 90 }
    action { type = "SetStorageClass", storage_class = "NEARLINE" }
  }
}
