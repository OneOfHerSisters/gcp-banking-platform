output "raw_bucket" {
  value = google_storage_bucket.raw.name
}

output "processed_bucket" {
  value = google_storage_bucket.processed.name
}

output "dataflow_temp_bucket" {
  value = google_storage_bucket.dataflow_temp.name
}

output "pubsub_topic" {
  value = google_pubsub_topic.transactions.id
}

output "bigquery_dataset" {
  value = google_bigquery_dataset.banking.dataset_id
}

output "cicd_sa_email" {
  value = google_service_account.cicd.email
}

output "dataflow_runtime_sa_email" {
  value = google_service_account.runtime.email
}

output "api_sa_email" {
  value = google_service_account.api.email
}
