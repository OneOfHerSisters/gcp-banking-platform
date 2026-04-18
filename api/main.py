"""
Banking Platform API — Cloud Run service.

Endpoints:
  GET  /health                         health check
  POST /transactions                   publish transaction to Pub/Sub
  GET  /transactions?limit&user_id&type  query BigQuery
  GET  /anomalies?limit                query BigQuery
  GET  /stats                          aggregate stats from BigQuery
"""

import json
import os
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from google.cloud import bigquery, pubsub_v1
from pydantic import BaseModel, Field

PROJECT_ID   = os.environ["PROJECT_ID"]
PUBSUB_TOPIC = os.environ.get("PUBSUB_TOPIC", "banking-transactions")
BQ_DATASET   = os.environ.get("BQ_DATASET", "banking_platform")

publisher  = pubsub_v1.PublisherClient()
bq         = bigquery.Client(project=PROJECT_ID)
topic_path = publisher.topic_path(PROJECT_ID, PUBSUB_TOPIC)

app = FastAPI(title="Banking Platform API")


# ──────────────────────────────────────────
# Models
# ──────────────────────────────────────────

class TransactionRequest(BaseModel):
    user_id:             str
    amount:              float = Field(gt=0)
    currency:            str
    transaction_type:    str
    status:              str
    merchant_id:         str | None = None
    merchant_category:   str | None = None
    country:             str | None = None


# ──────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────

def _serialize(row: dict) -> dict:
    """Convert BigQuery row values to JSON-serializable types."""
    return {
        k: v.isoformat() if hasattr(v, "isoformat") else v
        for k, v in row.items()
    }


def _query(sql: str, params: list | None = None) -> list[dict]:
    config = bigquery.QueryJobConfig(query_parameters=params or [])
    return [_serialize(dict(row)) for row in bq.query(sql, job_config=config).result()]


# ──────────────────────────────────────────
# Routes
# ──────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/transactions", status_code=201)
def create_transaction(tx: TransactionRequest):
    transaction_id = str(uuid.uuid4())
    payload = {
        "transaction_id":        transaction_id,
        "user_id":               tx.user_id,
        "amount":                tx.amount,
        "currency":              tx.currency,
        "transaction_type":      tx.transaction_type,
        "status":                tx.status,
        "merchant_id":           tx.merchant_id,
        "merchant_category":     tx.merchant_category,
        "country":               tx.country,
        "transaction_timestamp": datetime.now(timezone.utc).isoformat(),
    }
    publisher.publish(topic_path, json.dumps(payload).encode())
    return {"transaction_id": transaction_id}


@app.get("/transactions")
def get_transactions(
    limit:            int = Query(default=50, ge=1, le=500),
    user_id:          str | None = None,
    transaction_type: str | None = None,
):
    conditions = []
    params: list[bigquery.ScalarQueryParameter] = [
        bigquery.ScalarQueryParameter("limit", "INT64", limit),
    ]

    if user_id:
        conditions.append("user_id = @user_id")
        params.append(bigquery.ScalarQueryParameter("user_id", "STRING", user_id))
    if transaction_type:
        conditions.append("transaction_type = @transaction_type")
        params.append(bigquery.ScalarQueryParameter("transaction_type", "STRING", transaction_type))

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"""
        SELECT *
        FROM `{PROJECT_ID}.{BQ_DATASET}.transactions`
        {where}
        ORDER BY transaction_timestamp DESC
        LIMIT @limit
    """
    return _query(sql, params)


@app.get("/anomalies")
def get_anomalies(limit: int = Query(default=50, ge=1, le=500)):
    sql = f"""
        SELECT *
        FROM `{PROJECT_ID}.{BQ_DATASET}.anomalies`
        ORDER BY detected_at DESC
        LIMIT @limit
    """
    return _query(sql, [bigquery.ScalarQueryParameter("limit", "INT64", limit)])


@app.get("/stats")
def get_stats():
    sql = f"""
        SELECT
            COUNT(*)                                          AS total_transactions,
            ROUND(SUM(amount), 2)                            AS total_amount,
            COUNTIF(is_anomaly)                              AS anomaly_count,
            ROUND(COUNTIF(is_anomaly) / COUNT(*) * 100, 2)  AS anomaly_rate_pct
        FROM `{PROJECT_ID}.{BQ_DATASET}.transactions`
    """
    rows = _query(sql)
    if not rows:
        raise HTTPException(status_code=503, detail="No data yet")
    return rows[0]
