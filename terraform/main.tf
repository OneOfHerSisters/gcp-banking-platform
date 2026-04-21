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
# Artifact Registry
# ──────────────────────────────────────────

resource "google_artifact_registry_repository" "api" {
  location      = var.region
  repository_id = "banking-platform"
  format        = "DOCKER"
}

# ──────────────────────────────────────────
# Service Account: cicd-deployer-sa
# Used by GitHub Actions to deploy Dataflow jobs and Cloud Run.
# ──────────────────────────────────────────

resource "google_service_account" "cicd" {
  account_id   = "cicd-deployer-sa"
  display_name = "CI/CD Deployer Service Account"
}

# Submit Dataflow jobs
resource "google_project_iam_member" "cicd_dataflow_developer" {
  project = var.project_id
  role    = "roles/dataflow.developer"
  member  = "serviceAccount:${google_service_account.cicd.email}"
}

# Deploy Cloud Run services
resource "google_project_iam_member" "cicd_run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.cicd.email}"
}

# Push Docker images — scoped to specific repository
resource "google_artifact_registry_repository_iam_member" "cicd_ar_writer" {
  location   = google_artifact_registry_repository.api.location
  repository = google_artifact_registry_repository.api.repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.cicd.email}"
}

# Write Dataflow staging files to temp bucket
resource "google_storage_bucket_iam_member" "cicd_storage_temp" {
  bucket = google_storage_bucket.dataflow_temp.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.cicd.email}"
}

# Move batch files after processing (gsutil mv on raw bucket)
resource "google_storage_bucket_iam_member" "cicd_storage_raw" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.cicd.email}"
}

# Impersonate Dataflow runtime SA when submitting jobs
resource "google_service_account_iam_member" "cicd_sa_user_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.cicd.email}"
}

# Impersonate API SA when deploying Cloud Run
resource "google_service_account_iam_member" "cicd_sa_user_api" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.cicd.email}"
}

# ──────────────────────────────────────────
# Service Account: dataflow-runtime-sa
# Runs Dataflow workers. No CI/CD or Cloud Run permissions.
# ──────────────────────────────────────────

resource "google_service_account" "runtime" {
  account_id   = "dataflow-runtime-sa"
  display_name = "Dataflow Runtime Service Account"
}

# Run Dataflow workers
resource "google_project_iam_member" "runtime_dataflow_worker" {
  project = var.project_id
  role    = "roles/dataflow.worker"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# Submit BigQuery load jobs from within the pipeline
resource "google_project_iam_member" "runtime_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# Write to BigQuery — scoped to dataset
resource "google_bigquery_dataset_iam_member" "runtime_bq_editor" {
  dataset_id = google_bigquery_dataset.banking.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runtime.email}"
}

# Read/write batch files — scoped to raw bucket
resource "google_storage_bucket_iam_member" "runtime_storage_raw" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

# Read/write processed files — scoped to processed bucket
resource "google_storage_bucket_iam_member" "runtime_storage_processed" {
  bucket = google_storage_bucket.processed.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

# Read/write temp files — scoped to temp bucket
resource "google_storage_bucket_iam_member" "runtime_storage_temp" {
  bucket = google_storage_bucket.dataflow_temp.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

# Read messages from Pub/Sub — scoped to subscription
resource "google_pubsub_subscription_iam_member" "runtime_subscriber" {
  subscription = google_pubsub_subscription.dataflow.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.runtime.email}"
}

# Read subscription configuration — required by Dataflow to check ack deadline
resource "google_pubsub_subscription_iam_member" "runtime_subscriber_viewer" {
  subscription = google_pubsub_subscription.dataflow.name
  role         = "roles/pubsub.viewer"
  member       = "serviceAccount:${google_service_account.runtime.email}"
}

# ──────────────────────────────────────────
# Service Account: finpipe-api-sa
# Runs the Cloud Run API. Read-only BigQuery, publish to Pub/Sub only.
# ──────────────────────────────────────────

resource "google_service_account" "api" {
  account_id   = "finpipe-api-sa"
  display_name = "FinPipe API Service Account"
}

# Read transactions and anomalies — scoped to dataset
resource "google_bigquery_dataset_iam_member" "api_bq_viewer" {
  dataset_id = google_bigquery_dataset.banking.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.api.email}"
}

# Submit BigQuery queries
resource "google_project_iam_member" "api_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.api.email}"
}

# Publish transactions via POST /transactions — scoped to topic
resource "google_pubsub_topic_iam_member" "api_publisher" {
  topic  = google_pubsub_topic.transactions.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.api.email}"
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

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
