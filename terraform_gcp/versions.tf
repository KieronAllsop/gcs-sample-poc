terraform {
  required_version = ">= 1.5.0"

  backend "gcs" {
    bucket = "clearkey-video-gcp-tfstate"
    prefix = "poc"
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
