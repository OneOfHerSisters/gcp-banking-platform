#!/bin/bash
set -e
cd "$(dirname "$0")"
source venv/bin/activate
python streaming_pipeline.py \
  --project banking-platform-2026 \
  --runner DataflowRunner \
  --region europe-west1 \
  --temp_location gs://banking-platform-2026-dataflow-temp/temp \
  --service_account_email dataflow-runtime-sa@banking-platform-2026.iam.gserviceaccount.com \
  --input_subscription projects/banking-platform-2026/subscriptions/banking-transactions-dataflow \
  --job_name banking-streaming-pipeline \
  --streaming \
  --no_wait_until_finish
