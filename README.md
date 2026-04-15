# GCP Banking Data Platform

Cloud-native data platform for real-time and batch processing of banking transactions using Google Cloud Platform.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Data Sources                              │
│                                                                  │
│  Python Producer ──► Pub/Sub Topic ──► Dataflow (Beam)          │
│  Python Producer ──► GCS (raw/)    ──► (batch jobs)             │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
                    ┌──────────────────┐
                    │    BigQuery      │
                    │  ┌────────────┐  │
                    │  │transactions│  │
                    │  │ (partitioned│  │
                    │  │  by day)   │  │
                    │  └────────────┘  │
                    │  ┌────────────┐  │
                    │  │ anomalies  │  │
                    │  └────────────┘  │
                    └────────┬─────────┘
                             │
                             ▼
                    Looker Studio Dashboard
```

## Tech Stack

| Layer | Technology |
|---|---|
| Infrastructure | Terraform |
| Ingestion (stream) | Python → Pub/Sub |
| Ingestion (batch) | Python → GCS |
| Processing | Apache Beam on Dataflow |
| Storage | BigQuery (partitioned + clustered) |
| Analytics | BigQuery SQL + Looker Studio |
| CI/CD | GitHub Actions |

## Project Structure

```
.
├── terraform/                  # Infrastructure as Code
│   ├── main.tf                 # GCS, BigQuery, Pub/Sub, IAM
│   ├── variables.tf
│   └── outputs.tf
├── producer/                   # Transaction generator
│   ├── transaction_generator.py
│   └── requirements.txt
├── pipeline/                   # Apache Beam pipeline
│   ├── dataflow_pipeline.py
│   └── requirements.txt
├── sql/                        # Analytics queries
│   └── analytics.sql
└── .github/workflows/
    └── deploy.yml              # CI/CD
```

## Getting Started

### Prerequisites

- GCP account with billing enabled
- `gcloud` CLI installed and authenticated
- Terraform >= 1.5
- Python 3.11+

### 1. Enable GCP APIs

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
# Edit terraform.tfvars with your project_id

terraform init
terraform plan
terraform apply
```

### 3. Generate transactions

```bash
cd producer
pip install -r requirements.txt

# Stream mode (Pub/Sub)
python transaction_generator.py --mode stream --project YOUR_PROJECT_ID

# Batch mode (GCS)
python transaction_generator.py --mode batch --project YOUR_PROJECT_ID --count 5000
```

### 4. Run the Dataflow pipeline

```bash
cd pipeline
pip install -r requirements.txt

# Local test (DirectRunner)
python dataflow_pipeline.py \
  --project YOUR_PROJECT_ID \
  --runner DirectRunner \
  --input_subscription projects/YOUR_PROJECT_ID/subscriptions/banking-transactions-dataflow

# Deploy to Dataflow
python dataflow_pipeline.py \
  --project YOUR_PROJECT_ID \
  --runner DataflowRunner \
  --region europe-west1 \
  --temp_location gs://YOUR_PROJECT_ID-dataflow-temp/temp \
  --input_subscription projects/YOUR_PROJECT_ID/subscriptions/banking-transactions-dataflow \
  --streaming
```

### 5. Run analytics queries

Open `sql/analytics.sql` in BigQuery Console and run the queries against your dataset.

## Key Design Decisions

**Partitioned + clustered BigQuery tables** — transactions are partitioned by day and clustered by `transaction_type` and `status`. This reduces query cost significantly when filtering by date range (common in analytics).

**Separate anomalies table** — anomalies are written to a dedicated table in the same pipeline step, enabling fast alerting queries without scanning the full transactions table.

**Lognormal amount distribution** — the generator uses `lognormvariate` to simulate realistic transaction amounts (most transactions are small, a few are large), rather than uniform random values.

**DirectRunner for local testing** — the pipeline can be tested locally without deploying to Dataflow, which saves cost during development.

## Cost Management

> Always destroy resources when not in use.

```bash
# Destroy all GCP resources
cd terraform && terraform destroy

# Dataflow jobs must be stopped manually in GCP Console or via gcloud
gcloud dataflow jobs cancel JOB_ID --region=europe-west1
```

Estimated cost for active development session (~2h):
- Dataflow (n1-standard-2): ~$0.10
- BigQuery queries: ~$0.00 (under free tier)
- Pub/Sub: ~$0.00 (under free tier)
- GCS: ~$0.01

## CI/CD

GitHub Actions runs on every push:
- **PR**: `terraform fmt` check + `terraform validate`
- **main**: deploys/updates the Dataflow streaming job

Required GitHub secrets:
- `GCP_PROJECT_ID`
- `GCP_SA_KEY` (service account JSON key with Dataflow + BQ + GCS permissions)
