terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ──────────────────────────────────────────
# Google Cloud Storage
# ──────────────────────────────────────────

resource "google_storage_bucket" "raw" {
  name          = "${var.project_id}-transactions-raw"
  location      = var.region
  force_destroy = true

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket" "processed" {
  name          = "${var.project_id}-transactions-processed"
  location      = var.region
  force_destroy = true
}

resource "google_storage_bucket" "dataflow_temp" {
  name          = "${var.project_id}-dataflow-temp"
  location      = var.region
  force_destroy = true
}

# ──────────────────────────────────────────
# BigQuery
# ──────────────────────────────────────────

resource "google_bigquery_dataset" "banking" {
  dataset_id  = "banking_platform"
  description = "Banking transactions analytics"
  location    = var.region
}

resource "google_bigquery_table" "transactions" {
  dataset_id          = google_bigquery_dataset.banking.dataset_id
  table_id            = "transactions"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "transaction_timestamp"
  }

  clustering = ["transaction_type", "status"]

  schema = jsonencode([
    { name = "transaction_id", type = "STRING", mode = "REQUIRED" },
    { name = "user_id", type = "STRING", mode = "REQUIRED" },
    { name = "amount", type = "FLOAT64", mode = "REQUIRED" },
    { name = "currency", type = "STRING", mode = "REQUIRED" },
    { name = "transaction_type", type = "STRING", mode = "REQUIRED" },
    { name = "status", type = "STRING", mode = "REQUIRED" },
    { name = "merchant_id", type = "STRING", mode = "NULLABLE" },
    { name = "merchant_category", type = "STRING", mode = "NULLABLE" },
    { name = "country", type = "STRING", mode = "NULLABLE" },
    { name = "is_anomaly", type = "BOOL", mode = "NULLABLE" },
    { name = "transaction_timestamp", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "processed_at", type = "TIMESTAMP", mode = "NULLABLE" }
  ])
}

resource "google_bigquery_table" "anomalies" {
  dataset_id          = google_bigquery_dataset.banking.dataset_id
  table_id            = "anomalies"
  deletion_protection = false

  schema = jsonencode([
    { name = "transaction_id", type = "STRING", mode = "REQUIRED" },
    { name = "user_id", type = "STRING", mode = "REQUIRED" },
    { name = "amount", type = "FLOAT64", mode = "REQUIRED" },
    { name = "anomaly_reason", type = "STRING", mode = "NULLABLE" },
    { name = "transaction_timestamp", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "detected_at", type = "TIMESTAMP", mode = "REQUIRED" }
  ])
}

# ──────────────────────────────────────────
# Pub/Sub
# ──────────────────────────────────────────

resource "google_pubsub_topic" "transactions" {
  name = "banking-transactions"
}

resource "google_pubsub_subscription" "dataflow" {
  name  = "banking-transactions-dataflow"
  topic = google_pubsub_topic.transactions.name

  ack_deadline_seconds       = 60
  message_retention_duration = "86400s" # 24h
}

# ──────────────────────────────────────────
# Service Account for Dataflow
# ──────────────────────────────────────────

resource "google_service_account" "dataflow" {
  account_id   = "dataflow-banking-sa"
  display_name = "Dataflow Banking Service Account"
}

resource "google_project_iam_member" "dataflow_worker" {
  project = var.project_id
  role    = "roles/dataflow.worker"
  member  = "serviceAccount:${google_service_account.dataflow.email}"
}

resource "google_project_iam_member" "dataflow_developer" {
  project = var.project_id
  role    = "roles/dataflow.developer"
  member  = "serviceAccount:${google_service_account.dataflow.email}"
}

resource "google_project_iam_member" "bigquery_editor" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.dataflow.email}"
}

resource "google_project_iam_member" "storage_admin" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.dataflow.email}"
}

resource "google_pubsub_subscription_iam_member" "dataflow_subscriber" {
  subscription = google_pubsub_subscription.dataflow.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.dataflow.email}"
}

resource "google_project_iam_member" "sa_user" {
  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = "serviceAccount:${google_service_account.dataflow.email}"
}

resource "google_project_iam_member" "bigquery_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.dataflow.email}"
}

resource "google_project_iam_member" "run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.dataflow.email}"
}

resource "google_project_iam_member" "artifact_registry_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.dataflow.email}"
}

# ──────────────────────────────────────────
# Artifact Registry
# ──────────────────────────────────────────

resource "google_artifact_registry_repository" "api" {
  location      = var.region
  repository_id = "banking-platform"
  format        = "DOCKER"
}

# ──────────────────────────────────────────
# Service Account for Cloud Run API
# ──────────────────────────────────────────

resource "google_service_account" "api" {
  account_id   = "finpipe-api-sa"
  display_name = "FinPipe API Service Account"
}

resource "google_project_iam_member" "api_bq_viewer" {
  project = var.project_id
  role    = "roles/bigquery.dataViewer"
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "api_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "api_pubsub_publisher" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.api.email}"
}

# ──────────────────────────────────────────
# Cloud Run
# ──────────────────────────────────────────

resource "google_cloud_run_v2_service" "api" {
  name     = "banking-api"
  location = var.region

  template {
    service_account = google_service_account.api.email

    containers {
      # Placeholder image — replaced by CI/CD on every push to main
      image = "gcr.io/cloudrun/placeholder"

      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "PUBSUB_TOPIC"
        value = google_pubsub_topic.transactions.name
      }
      env {
        name  = "BQ_DATASET"
        value = google_bigquery_dataset.banking.dataset_id
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}