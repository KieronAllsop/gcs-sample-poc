data "google_project" "current" {}
locals {
  project_number       = data.google_project.current.number
  transcoder_agent     = "service-${local.project_number}@gcp-sa-transcoder.iam.gserviceaccount.com"
  eventarc_agent       = "service-${local.project_number}@gcp-sa-eventarc.iam.gserviceaccount.com"
  storage_agent        = "service-${local.project_number}@gs-project-accounts.iam.gserviceaccount.com"
  license_server_email = "clearkey-license-server@${var.project_id}.iam.gserviceaccount.com"
}

resource "google_project_service" "required" {
  for_each = toset([
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "eventarc.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
    "transcoder.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_storage_bucket" "source" {
  name                        = var.source_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  depends_on                  = [google_project_service.required]
}

resource "google_storage_bucket" "egress" {
  name                        = var.egress_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  depends_on                  = [google_project_service.required]
}

resource "google_service_account" "trigger" {
  account_id   = "clearkey-trigger"
  display_name = "ClearKey Transcoding Trigger"
}

resource "google_service_account" "manifest_patcher" {
  account_id   = "clearkey-manifest-patcher"
  display_name = "ClearKey Manifest Patcher"
}

resource "google_service_account" "license_server" {
  account_id   = "clearkey-license-server"
  display_name = "ClearKey License Server"
}

resource "google_secret_manager_secret" "database_password" {
  secret_id = "database-password"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "database_password" {
  secret      = google_secret_manager_secret.database_password.id
  secret_data = var.db_password
}

resource "google_secret_manager_secret" "clear_key" {
  secret_id = "clear-key"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "clear_key" {
  secret      = google_secret_manager_secret.clear_key.id
  secret_data = var.clear_key_value
}

resource "google_storage_bucket_iam_member" "transcoder_source" {
  bucket = google_storage_bucket.source.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${local.transcoder_agent}"
}

resource "google_storage_bucket_iam_member" "transcoder_egress" {
  bucket = google_storage_bucket.egress.name
  role   = google_project_iam_custom_role.media_object_writer.name
  member = "serviceAccount:${local.transcoder_agent}"
}

resource "google_storage_bucket_iam_member" "trigger_source" {
  bucket = google_storage_bucket.source.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.trigger.email}"
}

resource "google_storage_bucket_iam_member" "license_egress" {
  bucket = google_storage_bucket.egress.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.license_server.email}"
}

resource "google_project_iam_member" "trigger_transcoder" {
  project = var.project_id
  role    = google_project_iam_custom_role.transcoder_job_submitter.name
  member  = "serviceAccount:${google_service_account.trigger.email}"
}

resource "google_project_iam_custom_role" "transcoder_job_submitter" {
  role_id     = "clearkeyTranscoderJobSubmitter"
  title       = "ClearKey Transcoder job submitter"
  description = "Submit and inspect Transcoder jobs for the ClearKey pipeline."
  permissions = [
    "transcoder.jobs.create",
    "transcoder.jobs.get",
    "transcoder.jobs.list",
  ]
}

resource "google_project_iam_custom_role" "media_object_writer" {
  role_id     = "clearkeyMediaObjectWriter"
  title       = "ClearKey media object writer"
  description = "Read, list, create, and replace media objects in the egress bucket."
  permissions = [
    "storage.objects.create",
    "storage.objects.delete",
    "storage.objects.get",
    "storage.objects.list",
  ]
}

resource "google_project_iam_member" "trigger_eventarc_receiver" {
  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = "serviceAccount:${google_service_account.trigger.email}"
}

resource "google_project_iam_member" "license_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.license_server.email}"
}

resource "google_secret_manager_secret_iam_member" "license_database_password" {
  secret_id = google_secret_manager_secret.database_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.license_server.email}"
}

resource "google_secret_manager_secret_iam_member" "license_clear_key" {
  secret_id = google_secret_manager_secret.clear_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.license_server.email}"
}

resource "google_storage_bucket_iam_member" "packager_egress" {
  bucket = google_storage_bucket.egress.name
  role   = google_project_iam_custom_role.media_object_writer.name
  member = "serviceAccount:${google_service_account.manifest_patcher.email}"
}

resource "google_secret_manager_secret_iam_member" "packager_clear_key" {
  secret_id = google_secret_manager_secret.clear_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.manifest_patcher.email}"
}

resource "google_artifact_registry_repository" "clearkey" {
  location      = var.region
  repository_id = "clearkey"
  format        = "DOCKER"
  description   = "ClearKey video containers"
}

resource "null_resource" "build_images" {
  triggers = {
    repository = google_artifact_registry_repository.clearkey.id
    license    = filesha256("${path.root}/../license_server.py")
    trigger    = filesha256("${path.root}/../transcoder_handler.py")
    packager   = filesha256("${path.root}/../packager_service.py")
    dockerfiles = join(":", [
      filesha256("${path.root}/../Dockerfile"),
      filesha256("${path.root}/../Dockerfile.trigger"),
      filesha256("${path.root}/../Dockerfile.packager"),
    ])
  }

  provisioner "local-exec" {
    working_dir = path.root
    command = join(" && ", [
      "gcloud builds submit --project=${var.project_id} --tag=${var.license_server_image} ..",
      "gcloud builds submit --project=${var.project_id} --config=../cloudbuild.trigger.yaml ..",
      "gcloud builds submit --project=${var.project_id} --config=../cloudbuild.packager.yaml ..",
    ])
  }
  depends_on = [google_project_service.required, google_artifact_registry_repository.clearkey]
}

resource "google_sql_database_instance" "license_db" {
  name             = "clearkey-license-db"
  database_version = "POSTGRES_15"
  region           = var.region

  settings {
    tier              = "db-f1-micro"
    availability_type = "ZONAL"
    disk_type         = "PD_SSD"
    disk_size         = 10
    disk_autoresize   = true

    ip_configuration {
      ipv4_enabled = true
      authorized_networks {
        name  = "poc-public-access"
        value = "0.0.0.0/0"
      }
    }
  }

  deletion_protection = false
}

resource "google_sql_database" "license_db" {
  name     = "license_db"
  instance = google_sql_database_instance.license_db.name
}

resource "google_sql_user" "db_admin" {
  name     = "db_admin"
  instance = google_sql_database_instance.license_db.name
  password = var.db_password
}

resource "google_cloud_run_v2_service" "license_server" {
  name     = "license-server"
  location = var.region

  template {
    service_account = google_service_account.license_server.email
    containers {
      image = var.license_server_image
      ports { container_port = 8080 }
      env {
        name  = "STORAGE_BUCKET"
        value = google_storage_bucket.egress.name
      }
      env {
        name  = "DB_USER"
        value = google_sql_user.db_admin.name
      }
      env {
        name  = "DB_HOST"
        value = google_sql_database_instance.license_db.public_ip_address
      }
      env {
        name  = "DB_NAME"
        value = google_sql_database.license_db.name
      }
      env {
        name  = "ALLOWED_ORIGIN"
        value = var.allowed_origin
      }
      env {
        name = "DB_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_password.id
            version = "latest"
          }
        }
      }
      env {
        name = "CLEAR_KEY_TEST_VALUE"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.clear_key.id
            version = "latest"
          }
        }
      }
    }
  }
  depends_on = [null_resource.build_images, google_secret_manager_secret_version.database_password, google_secret_manager_secret_version.clear_key, google_secret_manager_secret_iam_member.license_database_password, google_secret_manager_secret_iam_member.license_clear_key]
}

resource "google_cloud_run_v2_service_iam_member" "license_public" {
  name     = google_cloud_run_v2_service.license_server.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service" "transcoder_trigger" {
  name     = "transcoder-trigger"
  location = var.region

  template {
    service_account = google_service_account.trigger.email
    containers {
      image = var.trigger_image
      ports { container_port = 8080 }
      env {
        name  = "SOURCE_BUCKET"
        value = google_storage_bucket.source.name
      }
      env {
        name  = "EGRESS_BUCKET"
        value = google_storage_bucket.egress.name
      }
      env {
        name  = "TRANSCODER_REGION"
        value = var.region
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
    }
  }
  depends_on = [null_resource.build_images]
}

resource "google_cloud_run_v2_service_iam_member" "transcoder_eventarc_invoker" {
  name     = google_cloud_run_v2_service.transcoder_trigger.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.trigger.email}"
}

resource "google_cloud_run_v2_service" "packager" {
  name     = "clear-key-packager"
  location = var.region

  template {
    service_account = google_service_account.manifest_patcher.email
    containers {
      image = var.packager_image
      ports { container_port = 8080 }
      env {
        name  = "STORAGE_BUCKET"
        value = google_storage_bucket.egress.name
      }
      env {
        name  = "CLEAR_KEY_ID"
        value = "00000000000000000000000000000001"
      }
      env {
        name = "CLEAR_KEY_TEST_VALUE"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.clear_key.id
            version = "latest"
          }
        }
      }
    }
  }
  depends_on = [null_resource.build_images, google_secret_manager_secret_version.clear_key, google_secret_manager_secret_iam_member.packager_clear_key]
}

resource "google_project_iam_member" "eventarc_service_agent" {
  project = var.project_id
  role    = "roles/eventarc.serviceAgent"
  member  = "serviceAccount:${local.eventarc_agent}"
}

resource "google_project_iam_member" "storage_eventarc_publisher" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${local.storage_agent}"
}

resource "google_storage_bucket_iam_member" "eventarc_bucket_reader" {
  bucket = google_storage_bucket.source.name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${local.eventarc_agent}"
}

resource "google_storage_bucket_iam_member" "eventarc_egress_bucket_reader" {
  bucket = google_storage_bucket.egress.name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${local.eventarc_agent}"
}

resource "google_cloud_run_v2_service_iam_member" "packager_eventarc_invoker" {
  name     = google_cloud_run_v2_service.packager.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.trigger.email}"
}

resource "google_eventarc_trigger" "source_mp4_finalized" {
  name     = "source-mp4-finalized"
  location = var.region

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.storage.object.v1.finalized"
  }
  matching_criteria {
    attribute = "bucket"
    value     = google_storage_bucket.source.name
  }

  destination {
    cloud_run_service {
      service = google_cloud_run_v2_service.transcoder_trigger.name
      region  = var.region
      path    = "/"
    }
  }

  service_account = google_service_account.trigger.email
  depends_on      = [google_project_iam_member.eventarc_service_agent, google_project_iam_member.storage_eventarc_publisher, google_cloud_run_v2_service_iam_member.transcoder_eventarc_invoker]
}

resource "google_eventarc_trigger" "egress_manifest_finalized" {
  name     = "egress-manifest-finalized"
  location = var.region

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.storage.object.v1.finalized"
  }
  matching_criteria {
    attribute = "bucket"
    value     = google_storage_bucket.egress.name
  }

  destination {
    cloud_run_service {
      service = google_cloud_run_v2_service.packager.name
      region  = var.region
      path    = "/"
    }
  }

  service_account = google_service_account.trigger.email
  depends_on = [
    google_project_iam_member.eventarc_service_agent,
    google_project_iam_member.storage_eventarc_publisher,
    google_storage_bucket_iam_member.eventarc_egress_bucket_reader,
    google_cloud_run_v2_service_iam_member.packager_eventarc_invoker,
  ]
}
