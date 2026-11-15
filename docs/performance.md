# Performance

Measured, not estimated. The figures come from [`etl/benchmark.py`](../etl/benchmark.py) (`make benchmark`), which times each query with `EXPLAIN (ANALYZE)`, once as the warehouse is and once with the optimisation removed, and reports the median of five executions.

## Results

Run on a GitHub Actions hosted runner (Ubuntu, PostgreSQL 16 in a container, default configuration) on 2026-11-15, with 587,078 rows in `fact_transaction` and 138,837 in `fact_settlement`.

| Query | Optimisation | Before | After | Speed-up | Access before | Access after |
|---|---|---|---|---|---|---|
| Daily history of one merchant | index `fact_transaction_merchant (merchant_key, date_key)` | 18.8 ms | 1.55 ms | 12x | Seq Scan on `fact_transaction` | Bitmap Index Scan on `fact_transaction_merchant` |
| Recent unreconciled settlement batches | partial index `fact_settlement_unreconciled` | 1.1 ms | 0.07 ms | 17x | Index Scan on `fact_settlement_date` | Index Scan on `fact_settlement_unreconciled` |
| Monthly value, fees and refunds | materialized view `mart.daily_merchant_summary` | 366.7 ms | 30.43 ms | 12x | Seq Scan on `fact_transaction` | Seq Scan on `daily_merchant_summary` |

These are single measurements on shared hardware with a warm cache. They show the direction and rough size of each effect at this data volume, not a guarantee. Re-run `make benchmark` on your own data for your own numbers.

## What each one is for

**Composite index on `(merchant_key, date_key)`.** The Merchant Detail drill-through asks for one merchant's history. Without the index PostgreSQL reads the whole fact table; with it, only that merchant's rows. The index leads with `merchant_key` because that is the equality filter; `date_key` second lets a date range narrow further.

**Partial index on unreconciled settlements.** Fewer than 1 in 200 settlement batches is unreconciled, and the reconciliation page only ever asks for those. An index restricted to `WHERE NOT is_reconciled` holds only the interesting rows, so it is tiny and the lookup touches almost nothing.

**Materialized view `mart.daily_merchant_summary`.** Most report visuals need day-and-merchant totals, not individual payments. Pre-aggregating once per pipeline run replaces a scan of three fact tables with a scan of one small table. It has a unique index on `(date_key, merchant_key)` and is refreshed at the end of every run. A SQL assertion (`13_daily_summary_matches_the_facts`) proves it always equals the facts.

## A case where an index did not help

The first version of the benchmark tested "one merchant, one month" against the same composite index and measured **no difference** (1.7 ms either way). PostgreSQL was already using the `date_key` index to find the month and filtering by merchant afterwards, which is just as fast when a month is a small part of the table. The index earns its place on queries that are selective on the merchant and wide on dates, which is why the benchmark now measures that. It is a useful reminder to measure before assuming an index helps.

## Load performance

In the same CI run, the 36 monthly batches (about 16,000 payments each, plus fees, refunds, chargebacks and settlements) loaded in roughly 7 seconds per batch, including validation and reconciliation.

Choices that keep the load set-based:

- Files are landed with `COPY`, not row-by-row inserts.
- Validation rules run as SQL `UPDATE` statements over the staged batch, not as a Python loop.
- Currency conversion is an as-of join (`LATERAL ... ORDER BY rate_date DESC LIMIT 1`) served by the primary key of `dwh.fx_rate`.
- Staging tables are `UNLOGGED`: they are rebuilt on every run, so there is nothing to recover.

## Partitioning

`fact_transaction` is not partitioned. At this size every query above is already fast, and partitioning has costs: the unique constraint on `transaction_id` would have to include the partition key, and every foreign key from the refund, chargeback and fee facts would have to follow.

Monthly range partitioning on `date_key` would start to pay off when the table is large enough that maintenance on the whole table hurts (hundreds of millions of rows), or when old months need to be detached and archived cheaply. The load is already organised by month, so each batch would map to one partition.

## Not measured

- The full 1.5-million-payment scale.
- Concurrent report queries.
- Power BI refresh time and DAX query performance.
