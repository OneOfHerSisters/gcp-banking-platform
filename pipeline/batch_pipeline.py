"""
Dataflow batch pipeline.
Reads JSON transaction files from GCS → validates → flags anomalies → writes to BigQuery.
Completes after processing all files (non-streaming).

Run locally (DirectRunner, for testing):
    python batch_pipeline.py \
        --runner DirectRunner \
        --project YOUR_PROJECT_ID \
        --input_path gs://YOUR_PROJECT_ID-transactions-raw/batch/*.json

Deploy to Dataflow:
    python batch_pipeline.py \
        --runner DataflowRunner \
        --project YOUR_PROJECT_ID \
        --region europe-west1 \
        --temp_location gs://YOUR_PROJECT_ID-dataflow-temp/temp \
        --service_account_email dataflow-banking-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
        --input_path gs://YOUR_PROJECT_ID-transactions-raw/batch/*.json \
        --job_name banking-batch-pipeline
"""

import argparse
import json
import logging
import os

import apache_beam as beam
from apache_beam.io import ReadFromText
from apache_beam.io.filesystems import FileSystems
from apache_beam.io.gcp.bigquery import WriteToBigQuery, BigQueryDisposition
from apache_beam.metrics import Metrics
from apache_beam.options.pipeline_options import (
    GoogleCloudOptions,
    PipelineOptions,
    StandardOptions,
)

TRANSACTIONS_SCHEMA = {
    "fields": [
        {"name": "transaction_id",        "type": "STRING",    "mode": "REQUIRED"},
        {"name": "user_id",               "type": "STRING",    "mode": "REQUIRED"},
        {"name": "amount",                "type": "FLOAT",     "mode": "REQUIRED"},
        {"name": "currency",              "type": "STRING",    "mode": "REQUIRED"},
        {"name": "transaction_type",      "type": "STRING",    "mode": "REQUIRED"},
        {"name": "status",                "type": "STRING",    "mode": "REQUIRED"},
        {"name": "merchant_id",           "type": "STRING",    "mode": "NULLABLE"},
        {"name": "merchant_category",     "type": "STRING",    "mode": "NULLABLE"},
        {"name": "country",               "type": "STRING",    "mode": "NULLABLE"},
        {"name": "is_anomaly",            "type": "BOOLEAN",   "mode": "NULLABLE"},
        {"name": "transaction_timestamp", "type": "TIMESTAMP", "mode": "REQUIRED"},
        {"name": "processed_at",          "type": "TIMESTAMP", "mode": "NULLABLE"},
    ]
}

ANOMALIES_SCHEMA = {
    "fields": [
        {"name": "transaction_id",        "type": "STRING",    "mode": "REQUIRED"},
        {"name": "user_id",               "type": "STRING",    "mode": "REQUIRED"},
        {"name": "amount",                "type": "FLOAT",     "mode": "REQUIRED"},
        {"name": "anomaly_reason",        "type": "STRING",    "mode": "NULLABLE"},
        {"name": "transaction_timestamp", "type": "TIMESTAMP", "mode": "REQUIRED"},
        {"name": "detected_at",           "type": "TIMESTAMP", "mode": "REQUIRED"},
    ]
}


class ParseTransaction(beam.DoFn):
    """Parse JSON line from a GCS text file."""

    def __init__(self):
        self.parsed   = Metrics.counter(self.__class__, "parsed")
        self.failed   = Metrics.counter(self.__class__, "parse_failed")

    def process(self, element):
        try:
            tx = json.loads(element)
            self.parsed.inc()
            yield tx
        except json.JSONDecodeError as e:
            self.failed.inc()
            logging.warning(f"Failed to parse line: {e} — content: {element[:120]}")


class ValidateTransaction(beam.DoFn):
    """Validate required fields and basic business rules."""

    REQUIRED_FIELDS = {"transaction_id", "user_id", "amount", "currency",
                       "transaction_type", "status", "transaction_timestamp"}

    def __init__(self):
        self.valid          = Metrics.counter(self.__class__, "valid")
        self.invalid_fields = Metrics.counter(self.__class__, "invalid_missing_fields")
        self.invalid_amount = Metrics.counter(self.__class__, "invalid_non_positive_amount")

    def process(self, element):
        missing = self.REQUIRED_FIELDS - set(element.keys())
        if missing:
            self.invalid_fields.inc()
            logging.warning(f"Transaction {element.get('transaction_id')} missing fields: {missing}")
            return

        if element["amount"] <= 0:
            self.invalid_amount.inc()
            logging.warning(f"Transaction {element['transaction_id']} has non-positive amount")
            return

        self.valid.inc()
        yield element


class EnrichTransaction(beam.DoFn):
    """Add processing metadata and anomaly flag."""

    def __init__(self, anomaly_amount_threshold):
        self.anomaly_amount_threshold = anomaly_amount_threshold
        self.anomalies = Metrics.counter(self.__class__, "anomalies_flagged")
        self.normal    = Metrics.counter(self.__class__, "normal")

    def process(self, element):
        from datetime import datetime, timezone
        element["processed_at"] = datetime.now(timezone.utc).isoformat()
        element["is_anomaly"] = element.get("amount", 0) > self.anomaly_amount_threshold
        if element["is_anomaly"]:
            self.anomalies.inc()
            logging.info(
                f"Anomaly detected: {element['transaction_id']} "
                f"amount={element['amount']} threshold={self.anomaly_amount_threshold}"
            )
        else:
            self.normal.inc()
        yield element


class ExtractAnomalies(beam.DoFn):
    """Extract anomalous transactions into a separate collection."""

    def __init__(self, anomaly_amount_threshold):
        self.anomaly_amount_threshold = anomaly_amount_threshold
        self.extracted = Metrics.counter(self.__class__, "anomalies_extracted")

    def process(self, element):
        from datetime import datetime, timezone
        if element.get("is_anomaly"):
            self.extracted.inc()
            yield {
                "transaction_id":        element["transaction_id"],
                "user_id":               element["user_id"],
                "amount":                element["amount"],
                "anomaly_reason":        f"Amount exceeds threshold of {self.anomaly_amount_threshold}",
                "transaction_timestamp": element["transaction_timestamp"],
                "detected_at":           datetime.now(timezone.utc).isoformat(),
            }


def run(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input_path",
        default=os.getenv(
            "INPUT_PATH",
            "gs://banking-platform-2026-transactions-raw/batch/*.json",
        ),
    )
    parser.add_argument("--bq_dataset",            default=os.getenv("BQ_DATASET", "banking_platform"))
    parser.add_argument("--bq_transactions_table", default=os.getenv("BQ_TRANSACTIONS_TABLE", "transactions"))
    parser.add_argument("--bq_anomalies_table",    default=os.getenv("BQ_ANOMALIES_TABLE", "anomalies"))
    parser.add_argument(
        "--anomaly_amount_threshold",
        type=float,
        default=float(os.getenv("ANOMALY_AMOUNT_THRESHOLD", "1000.0")),
    )

    known_args, pipeline_args = parser.parse_known_args(argv)

    match = FileSystems.match([known_args.input_path])
    if not match or not match[0].metadata_list:
        logging.info("No files to process at %s — exiting.", known_args.input_path)
        return

    logging.info("Found %d file(s) to process.", len(match[0].metadata_list))

    options = PipelineOptions(pipeline_args)
    # Batch mode — do NOT set streaming=True
    options.view_as(StandardOptions).streaming = False

    # project берём из стандартных Beam опций (--project флаг)
    project = options.view_as(GoogleCloudOptions).project

    transactions_table = f"{project}:{known_args.bq_dataset}.{known_args.bq_transactions_table}"
    anomalies_table    = f"{project}:{known_args.bq_dataset}.{known_args.bq_anomalies_table}"

    logging.info("=" * 60)
    logging.info("Banking batch pipeline starting")
    logging.info(f"  Input path : {known_args.input_path}")
    logging.info(f"  Transactions table : {transactions_table}")
    logging.info(f"  Anomalies table    : {anomalies_table}")
    logging.info(f"  Anomaly threshold  : {known_args.anomaly_amount_threshold}")
    logging.info("=" * 60)

    with beam.Pipeline(options=options) as p:
        transactions = (
            p
            | "Read from GCS"  >> ReadFromText(known_args.input_path, skip_header_lines=0)
            | "Parse JSON"     >> beam.ParDo(ParseTransaction())
            | "Validate"       >> beam.ParDo(ValidateTransaction())
            | "Enrich"         >> beam.ParDo(EnrichTransaction(known_args.anomaly_amount_threshold))
        )

        transactions | "Write transactions to BigQuery" >> WriteToBigQuery(
            table=transactions_table,
            schema=TRANSACTIONS_SCHEMA,
            write_disposition=BigQueryDisposition.WRITE_APPEND,
            create_disposition=BigQueryDisposition.CREATE_NEVER,
        )

        (
            transactions
            | "Extract anomalies"           >> beam.ParDo(ExtractAnomalies(known_args.anomaly_amount_threshold))
            | "Write anomalies to BigQuery" >> WriteToBigQuery(
                table=anomalies_table,
                schema=ANOMALIES_SCHEMA,
                write_disposition=BigQueryDisposition.WRITE_APPEND,
                create_disposition=BigQueryDisposition.CREATE_NEVER,
            )
        )

    logging.info("=" * 60)
    logging.info("Pipeline finished. Final metrics:")
    logging.info("  Check Dataflow UI → Job metrics → User metrics for counters:")
    logging.info("  ParseTransaction/parsed, ValidateTransaction/valid,")
    logging.info("  EnrichTransaction/anomalies_flagged, EnrichTransaction/normal")
    logging.info("=" * 60)


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    run()
