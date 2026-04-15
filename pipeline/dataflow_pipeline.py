"""
Dataflow streaming pipeline.
Reads transactions from Pub/Sub → validates → flags anomalies → writes to BigQuery.

Run locally (DirectRunner, for testing):
    python dataflow_pipeline.py \
        --runner DirectRunner \
        --project YOUR_PROJECT_ID \
        --input_subscription projects/YOUR_PROJECT_ID/subscriptions/banking-transactions-dataflow

Deploy to Dataflow:
    python dataflow_pipeline.py \
        --runner DataflowRunner \
        --project YOUR_PROJECT_ID \
        --region europe-west1 \
        --temp_location gs://YOUR_PROJECT_ID-dataflow-temp/temp \
        --service_account_email dataflow-banking-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
        --input_subscription projects/YOUR_PROJECT_ID/subscriptions/banking-transactions-dataflow \
        --job_name banking-pipeline \
        --streaming
"""

import argparse
import json
import logging
from datetime import datetime, timezone

import apache_beam as beam
from apache_beam.io.gcp.bigquery import WriteToBigQuery, BigQueryDisposition
from apache_beam.io.gcp.pubsub import ReadFromPubSub
from apache_beam.options.pipeline_options import (
    GoogleCloudOptions,
    PipelineOptions,
    StandardOptions,
)

ANOMALY_AMOUNT_THRESHOLD = 5000.0

class ParseTransaction(beam.DoFn):
    """Parse JSON message from Pub/Sub."""

    def process(self, element):
        try:
            tx = json.loads(element.decode("utf-8"))
            yield tx
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logging.warning(f"Failed to parse message: {e}")


class ValidateTransaction(beam.DoFn):
    """Validate required fields and basic business rules."""
    REQUIRED_FIELDS = {"transaction_id", "user_id", "amount", "currency",
                   "transaction_type", "status", "transaction_timestamp"}


    def process(self, element):
        missing = REQUIRED_FIELDS - set(element.keys())
        if missing:
            logging.warning(f"Transaction {element.get('transaction_id')} missing fields: {missing}")
            return

        if element["amount"] <= 0:
            logging.warning(f"Transaction {element['transaction_id']} has non-positive amount")
            return

        yield element


class EnrichTransaction(beam.DoFn):
    """Add processing metadata and anomaly flag."""

    def process(self, element):
        element["processed_at"] = datetime.now(timezone.utc).isoformat()
        element["is_anomaly"] = element.get("amount", 0) > ANOMALY_AMOUNT_THRESHOLD
        yield element


class ExtractAnomalies(beam.DoFn):
    """Extract anomalous transactions into a separate stream."""

    def process(self, element):
        if element.get("is_anomaly"):
            yield {
                "transaction_id":        element["transaction_id"],
                "user_id":               element["user_id"],
                "amount":                element["amount"],
                "anomaly_reason":        f"Amount exceeds threshold of {ANOMALY_AMOUNT_THRESHOLD}",
                "transaction_timestamp": element["transaction_timestamp"],
                "detected_at":           datetime.now(timezone.utc).isoformat(),
            }


def run(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_subscription",    required=True)
    parser.add_argument("--bq_dataset",            default="banking_platform")
    parser.add_argument("--bq_transactions_table", default="transactions")
    parser.add_argument("--bq_anomalies_table",    default="anomalies")

    known_args, pipeline_args = parser.parse_known_args(argv)
    options = PipelineOptions(pipeline_args)
    options.view_as(StandardOptions).streaming = True

    # project берём из стандартных Beam опций (--project флаг)
    project = options.view_as(GoogleCloudOptions).project

    transactions_table = f"{project}:{known_args.bq_dataset}.{known_args.bq_transactions_table}"
    anomalies_table    = f"{project}:{known_args.bq_dataset}.{known_args.bq_anomalies_table}"

    with beam.Pipeline(options=options) as p:
        transactions = (
            p
            | "Read from Pub/Sub" >> ReadFromPubSub(subscription=known_args.input_subscription)
            | "Parse JSON"        >> beam.ParDo(ParseTransaction())
            | "Validate"          >> beam.ParDo(ValidateTransaction())
            | "Enrich"            >> beam.ParDo(EnrichTransaction())
        )

        transactions | "Write transactions to BigQuery" >> WriteToBigQuery(
            table=transactions_table,
            write_disposition=BigQueryDisposition.WRITE_APPEND,
            create_disposition=BigQueryDisposition.CREATE_NEVER,
        )

        (
            transactions
            | "Extract anomalies"           >> beam.ParDo(ExtractAnomalies())
            | "Write anomalies to BigQuery" >> WriteToBigQuery(
                table=anomalies_table,
                write_disposition=BigQueryDisposition.WRITE_APPEND,
                create_disposition=BigQueryDisposition.CREATE_NEVER,
            )
        )


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    run()