# GCP Banking Data Platform

A cloud-native platform for processing and analysing banking transactions on GCP.
Supports two processing modes: real-time streaming (Pub/Sub → Dataflow) and batch file processing (GCS → Dataflow).

## Architecture

```
Python producer
    ├── stream → Pub/Sub → Dataflow (streaming_pipeline.py) → BigQuery
    └── batch  → GCS     → Dataflow (batch_pipeline.py)    → BigQuery
                                                                  ↓
                                                         Looker Studio
```

## Tech stack

| Layer | Technology |
|---|---|
| Infrastructure | Terraform |
| Ingestion (streaming) | Python → Pub/Sub |
| Ingestion (batch) | Python → GCS |
| Processing | Apache Beam on Dataflow |
| Storage | BigQuery (partitioning + clustering) |
| Analytics | BigQuery SQL + Looker Studio |
| CI/CD | GitHub Actions |

## Project structure

```
.
├── terraform/
│   ├── main.tf              # GCS, BigQuery, Pub/Sub, IAM
│   ├── variables.tf
│   ├── outputs.tf
│   └── terraform.tfvars.example
├── producer/
│   ├── transaction_generator.py
│   └── requirements.txt
├── pipeline/
│   ├── streaming_pipeline.py  # Pub/Sub → BigQuery (run manually)
│   ├── batch_pipeline.py      # GCS → BigQuery (deployed via CI/CD on push to main)
│   └── requirements.txt
├── sql/
│   └── analytics.sql
└── .github/workflows/
    └── deploy.yml
```

## Getting started

### Prerequisites

- GCP account with billing enabled
- `gcloud` CLI
- Terraform >= 1.5
- Python 3.11+

### 1. Enable APIs

```bash
gcloud services enable \
  dataflow.googleapis.com \
  bigquery.googleapis.com \
  pubsub.googleapis.com \
  storage.googleapis.com \
  --project YOUR_PROJECT_ID
```

### 2. Deploy infrastructure

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# fill in your project_id in terraform.tfvars

terraform init
terraform plan
terraform apply
```

### 3. Generate transactions

```bash
cd producer
pip install -r requirements.txt

# stream to Pub/Sub
python transaction_generator.py --mode stream --project YOUR_PROJECT_ID

# write batch file to GCS
python transaction_generator.py --mode batch --project YOUR_PROJECT_ID --count 5000
```

### 4. Run the batch pipeline

```bash
cd pipeline
pip install -r requirements.txt

# local test with DirectRunner (free)
python batch_pipeline.py \
  --project YOUR_PROJECT_ID \
  --runner DirectRunner \
  --input_path "gs://YOUR_PROJECT_ID-transactions-raw/batch/*.json"

# deploy to Dataflow
python batch_pipeline.py \
  --project YOUR_PROJECT_ID \
  --runner DataflowRunner \
  --region europe-west1 \
  --temp_location gs://YOUR_PROJECT_ID-dataflow-temp/temp \
  --service_account_email dataflow-banking-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --input_path "gs://YOUR_PROJECT_ID-transactions-raw/batch/*.json" \
  --job_name banking-batch-pipeline
```

### 5. Run the streaming pipeline (manual)

The streaming pipeline runs indefinitely — start it only when you need to test real-time processing.

```bash
python streaming_pipeline.py \
  --project YOUR_PROJECT_ID \
  --runner DataflowRunner \
  --region europe-west1 \
  --temp_location gs://YOUR_PROJECT_ID-dataflow-temp/temp \
  --service_account_email dataflow-banking-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --input_subscription projects/YOUR_PROJECT_ID/subscriptions/banking-transactions-dataflow \
  --job_name banking-streaming-pipeline \
  --streaming \
  --no_wait_until_finish
```

Stop a running job:
```bash
gcloud dataflow jobs cancel JOB_ID --region europe-west1
```

### 6. Analytics

Open `sql/analytics.sql` in BigQuery Console. Six queries included:
transaction volume by hour, top users, breakdown by type and status, z-score anomaly detection, geographic distribution, pipeline processing lag.

Looker Studio dashboard: https://datastudio.google.com/reporting/c1efa3fb-a4a0-47a5-9476-99161ac605c7

## IAM — service account permissions

Dataflow jobs run under a dedicated service account `dataflow-banking-sa`, not under a personal account. This follows the principle of least privilege — the account only has the permissions it actually needs.

| Role | Why | Scope |
|---|---|---|
| `roles/dataflow.worker` | Run Dataflow workers | Project |
| `roles/dataflow.developer` | Create and monitor jobs | Project |
| `roles/bigquery.dataEditor` | Write data to BigQuery tables | Project |
| `roles/storage.objectAdmin` | Read and write files in GCS | Project |
| `roles/pubsub.subscriber` | Read messages from the queue | Project + Subscription |
| `roles/iam.serviceAccountUser` | Deploy jobs as the SA via CI/CD | Project |

**What could be tightened in production:**

`storage.objectAdmin` grants the ability to delete objects, which the pipeline doesn't need. In production, `storage.objectCreator` + `storage.objectViewer` would be sufficient. The broader role was used here for development convenience.

`iam.serviceAccountUser` is granted at project level, meaning the SA can act as any other service account in the project. The correct approach is to grant it at the level of the specific service account. Acceptable for a learning project, not for production.

`pubsub.subscriber` is granted twice — at project level via Terraform and at subscription level via `gcloud` manually. This is redundant. Ideally it should only be granted at subscription level, which is the more precise scope.

## Key design decisions

**BigQuery partitioning by day** — the `transactions` table is partitioned on `transaction_timestamp`. A query with a date filter only scans the relevant partitions instead of the full table. At scale this reduces query cost significantly.

**Clustering on `transaction_type` and `status`** — within each partition, data is physically sorted by these columns. BigQuery skips irrelevant blocks when filtering. Free to set up, always worth doing on columns you filter by frequently.

**Separate `anomalies` table** — anomalous transactions are written to a dedicated table in the same pipeline step, in parallel. This makes alerting queries fast — no need to scan the full transactions table.

**Pub/Sub as a buffer** — if Dataflow goes down or restarts, messages are not lost. They are retained in the queue for 24 hours (`message_retention_duration = "86400s"`), which is what makes the streaming architecture reliable.

**Two separate pipelines** — streaming and batch are intentionally split. The batch pipeline has a clear start and end, which makes it suitable for CI/CD and straightforward to monitor. The streaming pipeline runs indefinitely and is started manually when real-time processing is needed.

## CI/CD

| Trigger | Job | What it does |
|---|---|---|
| Every PR | `terraform-validate` | `terraform fmt -check` + `terraform validate` |
| Push to `main` | `terraform-validate` + `deploy-batch-pipeline` | Validates Terraform, deploys the batch Dataflow job, waits for it to finish |

The streaming pipeline is intentionally excluded from CI/CD.

Required GitHub secrets:
- `GCP_PROJECT_ID`
- `GCP_SA_KEY` — service account JSON key

## Cost management

Always destroy resources when you're done working:

```bash
# remove everything Terraform created
cd terraform && terraform destroy

# cancel a running Dataflow job
gcloud dataflow jobs cancel JOB_ID --region europe-west1
```

Estimated cost for an active development session (~2 hours):
- Dataflow (n1-standard-2): ~$0.10
- BigQuery: $0 (first 10 GB/month free)
- Pub/Sub: $0 (first 10 GB/month free)
- GCS: ~$0.01