output "license_server_url" {
  value = google_cloud_run_v2_service.license_server.uri
}

output "transcoder_trigger_url" {
  value = google_cloud_run_v2_service.transcoder_trigger.uri
}

output "packager_url" {
  value = google_cloud_run_v2_service.packager.uri
}

output "source_bucket" {
  value = google_storage_bucket.source.name
}

output "egress_bucket" {
  value = google_storage_bucket.egress.name
}

output "cloud_sql_instance" {
  value = google_sql_database_instance.license_db.connection_name
}

output "artifact_registry_repository" {
  value = google_artifact_registry_repository.clearkey.name
}
