variable "project_id" {
  type    = string
  default = "clearkey-video-gcp"
}

variable "region" {
  type    = string
  default = "europe-west2"
}

variable "deployer_impersonator_principal" {
  type        = string
  default     = null
  description = "IAM principal allowed to impersonate the Terraform deployer service account, for example user:admin@example.com."
}

variable "source_bucket_name" {
  type    = string
  default = "clearkey-video-gcp-source"
}

variable "egress_bucket_name" {
  type    = string
  default = "clearkey-video-gcp-egress"
}

variable "license_server_image" {
  type    = string
  default = "europe-west2-docker.pkg.dev/clearkey-video-gcp/clearkey/license-server:latest"
}

variable "trigger_image" {
  type    = string
  default = "europe-west2-docker.pkg.dev/clearkey-video-gcp/clearkey/transcoder-trigger:latest"
}

variable "packager_image" {
  type    = string
  default = "europe-west2-docker.pkg.dev/clearkey-video-gcp/clearkey/clear-key-packager:latest"
}

variable "allowed_origin" {
  type    = string
  default = "http://localhost:8080"
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "clear_key_value" {
  type        = string
  sensitive   = true
  description = "32-character hexadecimal ClearKey value"

  validation {
    condition     = can(regex("^[0-9a-fA-F]{32}$", var.clear_key_value))
    error_message = "clear_key_value must contain exactly 32 hexadecimal characters."
  }
}
