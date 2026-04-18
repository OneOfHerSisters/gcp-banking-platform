# GCP Banking Data Platform

A cloud-native platform for processing and analysing banking transactions on GCP.
Supports real-time streaming, batch file processing, and a REST API for querying and ingesting transactions.

## Architecture

```
Python producer
    ├── stream → Pub/Sub → Dataflow (streaming_pipeline.py) → BigQuery
    └── batch  → GCS     → Dataflow (batch_pipeline.py)    → BigQuery
                                                                  ↓
HTTP clients → Cloud Run (banking-api)  ──────────────────► BigQuery
                    └── POST /transactions → Pub/Sub ──────► Dataflow
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
| API | FastAPI on Cloud Run |
| Storage | BigQuery (partitioning + clustering) |
| Container registry | Artifact Registry |
| Analytics | BigQuery SQL + Looker Studio |
| CI/CD | GitHub Actions |

## Project structure

```
.
├── terraform/
│   ├── main.tf              # GCS, BigQuery, Pub/Sub, Artifact Registry, Cloud Run, IAM
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
├── api/
│   ├── main.py                # FastAPI app
│   ├── requirements.txt
│   └── Dockerfile
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
- Docker

### 1. Enable APIs

```bash
gcloud services enable \
  dataflow.googleapis.com \
  bigquery.googleapis.com \
  pubsub.googleapis.com \
  storage.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
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

Runs automatically via CI/CD on every push to `main`. To run manually:

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

If there are no files in `batch/`, the pipeline exits successfully with a log message.
Processed files are moved to `batch/processed/` automatically after each run.

### 5. Run the streaming pipeline (manual)

The streaming pipeline runs indefinitely — start it only when you need real-time processing.

```bash
cd pipeline

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

### 6. API (Cloud Run)

Deployed automatically via CI/CD on every push to `main`.

Base URL: `https://banking-api-xxxx-ew.a.run.app` (shown in GCP Console → Cloud Run → banking-api)

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/transactions` | Publish a transaction to Pub/Sub |
| `GET` | `/transactions` | Query recent transactions from BigQuery |
| `GET` | `/anomalies` | Query recent anomalies from BigQuery |
| `GET` | `/stats` | Aggregate stats: count, total amount, anomaly rate |

Query parameters for `GET /transactions`: `limit` (default 50, max 500), `user_id`, `transaction_type`.

Example:

```bash
# publish a transaction
curl -X POST https://YOUR_API_URL/transactions \
  -H "Content-Type: application/json" \
  -d '{"user_id":"u1","amount":250.0,"currency":"USD","transaction_type":"purchase","status":"completed"}'

# get last 10 anomalies
curl "https://YOUR_API_URL/anomalies?limit=10"

# stats
curl https://YOUR_API_URL/stats
```

### 7. Analytics

Open `sql/analytics.sql` in BigQuery Console. Six queries included:
transaction volume by hour, top users, breakdown by type and status, z-score anomaly detection, geographic distribution, pipeline processing lag.

Looker Studio dashboard: https://datastudio.google.com/reporting/c1efa3fb-a4a0-47a5-9476-99161ac605c7

## IAM — service accounts

### `dataflow-banking-sa`

Runs Dataflow jobs and deploys from CI/CD.

| Role | Why |
|---|---|
| `roles/dataflow.worker` | Run Dataflow workers |
| `roles/dataflow.developer` | Create and monitor jobs |
| `roles/bigquery.dataEditor` | Write data to BigQuery |
| `roles/bigquery.jobUser` | Submit BigQuery jobs |
| `roles/storage.objectAdmin` | Read/write files in GCS |
| `roles/pubsub.subscriber` | Read messages from subscription |
| `roles/iam.serviceAccountUser` | Impersonate SA during job submission |
| `roles/run.developer` | Deploy Cloud Run services from CI/CD |
| `roles/artifactregistry.writer` | Push Docker images to Artifact Registry |

### `finpipe-api-sa`

Runs the Cloud Run API. Least-privilege — read-only access to BigQuery and publish to Pub/Sub.

| Role | Why |
|---|---|
| `roles/bigquery.dataViewer` | Read transactions and anomalies |
| `roles/bigquery.jobUser` | Submit BigQuery queries |
| `roles/pubsub.publisher` | Publish transactions via `POST /transactions` |

## Key design decisions

**BigQuery partitioning by day** — the `transactions` table is partitioned on `transaction_timestamp`. A query with a date filter only scans the relevant partitions instead of the full table. At scale this reduces query cost significantly.

**Clustering on `transaction_type` and `status`** — within each partition, data is physically sorted by these columns. BigQuery skips irrelevant blocks when filtering. Free to set up, always worth doing on columns you filter by frequently.

**Separate `anomalies` table** — anomalous transactions are written to a dedicated table in the same pipeline step, in parallel. This makes alerting queries fast — no need to scan the full transactions table.

**Pub/Sub as a buffer** — if Dataflow goes down or restarts, messages are not lost. They are retained in the queue for 24 hours (`message_retention_duration = "86400s"`), which is what makes the streaming architecture reliable.

**Two separate pipelines** — streaming and batch are intentionally split. The batch pipeline has a clear start and end, which makes it suitable for CI/CD and straightforward to monitor. The streaming pipeline runs indefinitely and is started manually when real-time processing is needed.

**Separate service accounts** — Dataflow and the API run under different service accounts with different permission sets. The API SA (`finpipe-api-sa`) has no write access to BigQuery and cannot manage infrastructure.

## CI/CD

| Trigger | Job | What it does |
|---|---|---|
| Every PR | `terraform-validate` | `terraform fmt -check` + `terraform validate` |
| Push to `main` | `deploy-batch-pipeline` | Runs batch Dataflow job, moves processed files to `batch/processed/` |
| Push to `main` | `deploy-cloud-run` | Builds Docker image, pushes to Artifact Registry, deploys to Cloud Run |

Both deploy jobs run in parallel after `terraform-validate` passes.

Required GitHub secrets:
- `GCP_PROJECT_ID`
- `GCP_SA_KEY` — JSON key for `dataflow-banking-sa`

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
- Cloud Run: ~$0.00 (first 2M requests/month free)
- BigQuery: $0 (first 10 GB/month free)
- Pub/Sub: $0 (first 10 GB/month free)
- GCS: ~$0.01
- Artifact Registry: ~$0.01
