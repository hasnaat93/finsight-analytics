# Validation

How the numbers are shown to be right, layer by layer.

## 1. Source to warehouse: automated, every load

For every file of every batch the pipeline records three controls in `audit.reconciliation`. A failed control rolls the batch back.

| Control | Source | Target |
|---|---|---|
| `rows_delivered_vs_staged` | Row count in the manifest | Rows landed in staging |
| `rows_staged_vs_loaded_plus_rejected` | Rows landed | Rows in the warehouse for the batch + rows quarantined |
| `amount_control_total` | Sum of amounts in the validated source rows | Sum of amounts in the warehouse for the batch |

In the CI run of 2026-11-15 (587,078 payments, 36 batches) every control of every batch passed.

## 2. Inside the warehouse: 15 SQL assertions

`make test-sql` runs every query in [`sql/tests`](../sql/tests). Each returns the rows that violate a rule, so a passing assertion returns none. They cover the type 2 dimension (one current version, contiguous validity), that each payment points at the merchant version of its own date, currency conversion recomputed from the rates, refund and fee integrity, the settlement expectation recomputed from the facts, and that the pre-aggregated mart equals the facts.

All 15 passed in the same CI run, and passed again after one batch was deliberately reloaded.

## 3. Quarantine against the generator: exact

The generator reports how many of each defect it injected. In that run the quarantine held exactly those rows:

| File | Rule | Injected | Quarantined |
|---|---|---|---|
| transactions | duplicate_transaction_id | 881 | 881 |
| transactions | non_positive_amount | 235 | 235 |
| transactions | unknown_currency | 176 | 176 |
| transactions | future_timestamp | 118 | 118 |
| transactions | invalid_status | 62 | 62 |
| transactions | invalid_timestamp | 56 | 56 |

The same equality is asserted for every rule and every file by the test `test_the_quarantine_holds_exactly_what_the_generator_broke`, and the 558 settlement batches reported as unreconciled are exactly the ones the generator altered.

## 4. Web preview against SQL

The web preview reads the same mart views and warehouse tables as the SQL below, so its totals are these totals. Figures from the CI run of 2026-11-15 ([`sql/analysis/00_headline_totals.sql`](../sql/analysis/00_headline_totals.sql)):

| Measure | SQL result |
|---|---|
| Total transaction value | 58,172,473.09 |
| Transaction count | 587,078 |
| Approved transactions | 547,080 |
| Approval rate | 93.1869% |
| Fee revenue | 1,539,027.72 |
| Processing cost | 726,920.88 |
| Chargeback losses | 60,218.15 |
| Net revenue | 751,888.69 |
| Total refunds | 1,823,338.46 |
| Chargeback count | 1,228 |
| Active merchants | 243 |
| Expected payout | 54,622,116.62 |
| Actual payout | 54,605,591.69 |
| Settlement batches | 138,837 |
| Unreconciled batches | 558 |
| Rows rejected | 1,944 |

These figures change with the scale and seed. They are reproducible: the same seed and scale give the same data.

## 5. Power BI against SQL: not done yet

This is the comparison the project is meant to end with, and it has **not** been performed, because the model has not been opened in Power BI Desktop. To complete it:

1. Load the warehouse (`make generate etl`) and run `sql/analysis/00_headline_totals.sql`.
2. Open `powerbi/FinSight.pbip`, refresh, and put each measure below on a card with no filters.
3. Fill in the table. Every row should match to the cent or the unit.

| DAX measure | SQL column | SQL result | Power BI result | Match |
|---|---|---|---|---|
| `[Total Transaction Value]` | total_transaction_value | | | |
| `[Transaction Count]` | transaction_count | | | |
| `[Approved Transactions]` | approved_transactions | | | |
| `[Approval Rate %]` | approval_rate_pct | | | |
| `[Fee Revenue]` | fee_revenue | | | |
| `[Processing Cost]` | processing_cost | | | |
| `[Chargeback Losses]` | chargeback_losses | | | |
| `[Net Revenue]` | net_revenue | | | |
| `[Total Refunds]` | total_refunds | | | |
| `[Chargeback Count]` | chargeback_count | | | |
| `[Active Merchants]` | active_merchants | | | |
| `[Expected Payout]` | expected_payout | | | |
| `[Actual Payout]` | actual_payout | | | |
| `[Settlement Batches]` | settlement_batches | | | |
| `[Unreconciled Batches]` | unreconciled_batches | | | |
| `[Rows Rejected]` | rows_rejected | | | |

If a measure does not match, the first things to check are a relationship filtering in an unexpected direction and a visual-level filter left on the card.
