-- ─────────────────────────────────────────────────────────────
-- 1. Transaction volume by hour (last 24h)
-- ─────────────────────────────────────────────────────────────
SELECT
  TIMESTAMP_TRUNC(transaction_timestamp, HOUR) AS hour,
  COUNT(*)                                      AS total_transactions,
  ROUND(SUM(amount), 2)                         AS total_volume_usd,
  ROUND(AVG(amount), 2)                         AS avg_amount,
  COUNTIF(status = 'failed')                    AS failed_count
FROM `banking_platform.transactions`
WHERE transaction_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
GROUP BY hour
ORDER BY hour DESC;


-- ─────────────────────────────────────────────────────────────
-- 2. Top 10 most active users (last 7 days)
-- ─────────────────────────────────────────────────────────────
SELECT
  user_id,
  COUNT(*)              AS transaction_count,
  ROUND(SUM(amount), 2) AS total_spent,
  ROUND(AVG(amount), 2) AS avg_transaction,
  MAX(amount)           AS max_single_transaction,
  COUNTIF(is_anomaly)   AS anomaly_count
FROM `banking_platform.transactions`
WHERE transaction_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
GROUP BY user_id
ORDER BY total_spent DESC
LIMIT 10;


-- ─────────────────────────────────────────────────────────────
-- 3. Transaction breakdown by type and status
-- ─────────────────────────────────────────────────────────────
SELECT
  transaction_type,
  status,
  COUNT(*)              AS count,
  ROUND(SUM(amount), 2) AS volume,
  ROUND(AVG(amount), 2) AS avg_amount
FROM `banking_platform.transactions`
WHERE DATE(transaction_timestamp) = CURRENT_DATE()
GROUP BY transaction_type, status
ORDER BY transaction_type, count DESC;


-- ─────────────────────────────────────────────────────────────
-- 4. Anomaly detection - users with suspicious patterns
-- ─────────────────────────────────────────────────────────────
WITH user_stats AS (
  SELECT
    user_id,
    AVG(amount)    AS avg_amount,
    STDDEV(amount) AS stddev_amount,
    COUNT(*)       AS tx_count
  FROM `banking_platform.transactions`
  WHERE transaction_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
  GROUP BY user_id
  HAVING tx_count >= 5
),
scored AS (
  SELECT
    t.transaction_id,
    t.user_id,
    t.amount,
    t.transaction_timestamp,
    t.country,
    ROUND((t.amount - s.avg_amount) / NULLIF(s.stddev_amount, 0), 2) AS z_score
  FROM `banking_platform.transactions` t
  JOIN user_stats s USING (user_id)
  WHERE t.transaction_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
)
SELECT *
FROM scored
WHERE ABS(z_score) > 3
ORDER BY ABS(z_score) DESC
LIMIT 50;


-- ─────────────────────────────────────────────────────────────
-- 5. Geographic distribution of transactions
-- ─────────────────────────────────────────────────────────────
SELECT
  country,
  COUNT(*)              AS transaction_count,
  ROUND(SUM(amount), 2) AS total_volume,
  COUNTIF(is_anomaly)   AS anomalies
FROM `banking_platform.transactions`
WHERE transaction_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
GROUP BY country
ORDER BY total_volume DESC;


-- ─────────────────────────────────────────────────────────────
-- 6. Pipeline health check - processing lag
-- ─────────────────────────────────────────────────────────────
SELECT
  MIN(TIMESTAMP_DIFF(processed_at, transaction_timestamp, SECOND)) AS min_lag_sec,
  AVG(TIMESTAMP_DIFF(processed_at, transaction_timestamp, SECOND)) AS avg_lag_sec,
  MAX(TIMESTAMP_DIFF(processed_at, transaction_timestamp, SECOND)) AS max_lag_sec,
  COUNT(*) AS total_processed
FROM `banking_platform.transactions`
WHERE processed_at IS NOT NULL
  AND transaction_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR);
