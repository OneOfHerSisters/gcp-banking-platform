"""
Banking transaction generator.
Sends fake transactions to Pub/Sub (streaming) and GCS (batch).

Usage:
    pip install -r requirements.txt

    # Stream to Pub/Sub
    python transaction_generator.py --mode stream --project YOUR_PROJECT_ID

    # Write batch file to GCS
    python transaction_generator.py --mode batch --project YOUR_PROJECT_ID --count 1000
"""

import argparse
import json
import os
import random
import uuid
from datetime import datetime, timezone
from typing import Any

from faker import Faker
from google.cloud import pubsub_v1, storage

fake = Faker()

TRANSACTION_TYPES = ["purchase", "transfer", "withdrawal", "deposit", "refund"]
STATUSES = ["completed", "pending", "failed", "reversed"]
CURRENCIES = ["USD", "EUR", "GBP", "PLN", "CHF"]
MERCHANT_CATEGORIES = ["grocery", "electronics", "restaurant", "travel", "fuel", "pharmacy"]
COUNTRIES = ["US", "DE", "PL", "GB", "FR", "NL", "UA"]

DEFAULT_ANOMALY_THRESHOLD = float(os.getenv("ANOMALY_AMOUNT_THRESHOLD", "1000.0"))


def generate_transaction(anomaly_threshold: float) -> dict[str, Any]:
    amount = round(random.lognormvariate(4.0, 1.5), 2)  # realistic distribution
    amount = min(amount, 50000.0)

    return {
        "transaction_id":        str(uuid.uuid4()),
        "user_id":               f"user_{random.randint(1000, 9999)}",
        "amount":                amount,
        "currency":              random.choice(CURRENCIES),
        "transaction_type":      random.choice(TRANSACTION_TYPES),
        "status":                random.choice(STATUSES),
        "merchant_id":           f"merchant_{random.randint(100, 999)}",
        "merchant_category":     random.choice(MERCHANT_CATEGORIES),
        "country":               random.choice(COUNTRIES),
        "is_anomaly":            amount > anomaly_threshold,
        "transaction_timestamp": datetime.now(timezone.utc).isoformat(),
    }


def stream_to_pubsub(
    project_id: str,
    topic_name: str,
    rate_per_second: int = 5,
    anomaly_threshold: float = DEFAULT_ANOMALY_THRESHOLD,
) -> None:
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(project_id, topic_name)

    import time
    print(f"Streaming to {topic_path} at {rate_per_second} tx/sec. Ctrl+C to stop.")

    while True:
        for _ in range(rate_per_second):
            tx = generate_transaction(anomaly_threshold)
            data = json.dumps(tx).encode("utf-8")
            future = publisher.publish(topic_path, data)
            print(f"Published {tx['transaction_id']} | {tx['amount']} {tx['currency']} | anomaly={tx['is_anomaly']}")
        time.sleep(1)


def batch_to_gcs(
    project_id: str,
    bucket_name: str,
    count: int = 1000,
    anomaly_threshold: float = DEFAULT_ANOMALY_THRESHOLD,
) -> None:
    transactions = [generate_transaction(anomaly_threshold) for _ in range(count)]

    client = storage.Client(project=project_id)
    bucket = client.bucket(bucket_name)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    blob_name = f"batch/transactions_{timestamp}.json"
    blob = bucket.blob(blob_name)

    content = "\n".join(json.dumps(tx) for tx in transactions)
    blob.upload_from_string(content, content_type="application/json")

    print(f"Uploaded {count} transactions to gs://{bucket_name}/{blob_name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Banking transaction generator")
    parser.add_argument("--mode",    choices=["stream", "batch"], required=True)
    parser.add_argument("--project", required=True, help="GCP project ID")
    parser.add_argument("--topic",   default="banking-transactions", help="Pub/Sub topic name")
    parser.add_argument("--bucket",  help="GCS bucket name for batch mode")
    parser.add_argument("--count",   type=int, default=1000, help="Number of transactions for batch mode")
    parser.add_argument("--rate",    type=int, default=5, help="Transactions per second for stream mode")
    parser.add_argument(
        "--anomaly_amount_threshold",
        type=float,
        default=DEFAULT_ANOMALY_THRESHOLD,
        help="Amount threshold above which a generated transaction is marked as anomaly",
    )
    args = parser.parse_args()

    if args.mode == "stream":
        stream_to_pubsub(args.project, args.topic, args.rate, args.anomaly_amount_threshold)
    else:
        bucket = args.bucket or f"{args.project}-transactions-raw"
        batch_to_gcs(args.project, bucket, args.count, args.anomaly_amount_threshold)
