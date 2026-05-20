variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod"
  }
}

variable "futu_login_account" {
  description = "Futu OpenD login account"
  type        = string
  sensitive   = true
  default     = ""
}

variable "futu_login_pwd_md5" {
  description = "Futu OpenD login password (MD5)"
  type        = string
  sensitive   = true
  default     = ""
}
