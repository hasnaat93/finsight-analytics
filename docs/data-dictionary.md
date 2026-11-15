# Data dictionary

Definitions: [`sql/ddl`](../sql/ddl) for the warehouse, [`sql/mart`](../sql/mart) for the reporting views.

## Conventions

- Surrogate keys are integers named `<thing>_key`. Key **−1** is the Unknown member of every dimension.
- Money is `numeric(18,2)`. Columns ending `_original` are in the settlement currency; the others are in **USD**.
- `batch_id` on a fact is the monthly batch (`yyyy-mm`) that delivered the row.
- Timestamps are UTC.

## Source files

One folder per month under `data/raw/<yyyy-mm>/`, with a `manifest.json` of row counts and SHA-256 checksums.

| File | Content |
|---|---|
| `merchants.csv` | Full merchant master as of the month: id, name, MCC, category, country, onboarding date, risk tier, pricing plan, status |
| `customers.csv` | Customers first seen in the month: hashed id, country, segment |
| `transactions.csv` | Payment attempts of the month: amount, currency, method, card brand and type, channel, status, decline reason, timestamp |
| `refunds.csv` | Refunds dated in the month, each referring to a payment |
| `chargebacks.csv` | Disputes dated in the month, with reason and outcome |
| `fees.csv` | One row per captured payment: merchant fee, interchange cost, scheme cost |
| `settlements.csv` | One payout per merchant per day: gross, fees, refunds, chargebacks, net payout |
| `fx_rates.csv` | Daily rate to USD for each currency |

## Dimensions (`dwh`)

### `dim_date`
Grain: one row per calendar day.

| Column | Meaning |
|---|---|
| `date_key` | `yyyymmdd` as an integer. Primary key |
| `full_date` | The date (`date` in the mart) |
| `year`, `quarter_number`, `quarter_label`, `month_number`, `month_name`, `year_month` | Calendar attributes |
| `week_of_year`, `day_of_month`, `day_of_week`, `day_name` | ISO week; day of week 1 = Monday |
| `fiscal_year`, `fiscal_quarter`, `fiscal_period` | Fiscal year starting 1 July; period 1 = July |
| `is_weekend`, `is_holiday` | Holiday covers 1 January and 25–26 December |

### `dim_merchant` (slowly changing, type 2)
Grain: one row per **version** of a merchant.

| Column | Meaning |
|---|---|
| `merchant_key` | Surrogate key of the version. Facts store this |
| `merchant_id` | Business key, the same across versions |
| `merchant_name` | Type 1: a change overwrites every version |
| `category`, `mcc`, `country_code`, `risk_tier`, `pricing_plan`, `status` | Type 2: a change opens a new version |
| `onboarding_date` | When the merchant joined |
| `valid_from`, `valid_to` | Validity of the version; from inclusive, to exclusive. The open version ends 9999-12-31; the first starts 1900-01-01 |
| `is_current` | True for the latest version. Exactly one per merchant |
| `row_hash` | MD5 of the type 2 attributes, used to detect change |

### `dim_customer`
Grain: one row per customer. `customer_hash` (business key, not exposed in the mart), `country_code`, `segment`. Type 1.

### `dim_payment_method`
Grain: one row per combination that occurs. `payment_method` (Card, Wallet, Bank Transfer), `card_brand`, `card_type`; "Not Applicable" where a brand or type does not apply.

### `dim_channel`
`channel_code`, `channel_name`: E-commerce, Point of sale, Mobile app, Recurring.

### `dim_geography`
`country_code`, `country_name`, `region`, `continent`. A payment's geography is its merchant's country.

### `dim_currency`
`currency_code`, `currency_name`: USD, EUR, GBP, PKR. Also the list of currencies the pipeline accepts.

### `dim_transaction_status`
`status` (Captured, Declined, Voided), `decline_reason` ("Not declined" unless declined), `is_success`.

### `fx_rate`
Not a dimension: `rate_date`, `currency_key`, `rate_to_usd`. A conversion uses the latest rate on or before the date.

## Facts (`dwh`)

### `fact_transaction`
Grain: one row per payment attempt. Business key `transaction_id`.

| Column | Meaning |
|---|---|
| `date_key`, `merchant_key`, `customer_key`, `method_key`, `channel_key`, `geography_key`, `currency_key`, `status_key` | Dimension keys. `merchant_key` is the version in force on the payment date |
| `transaction_ts`, `hour_of_day` | When the payment was attempted |
| `amount_original` | Amount in the payment currency |
| `amount_reporting` | Amount in USD at the rate of the payment date |
| `is_approved` | The issuer approved it (Captured or Voided) |
| `is_captured` | The payment was captured, so money moved |

### `fact_refund`
Grain: one row per refund. Business key `refund_id`. `transaction_key` links to the payment; merchant, geography and currency are inherited from it. `refund_original`, `refund_amount` (USD at the refund date's rate), `refund_reason`, `is_partial`.

### `fact_chargeback`
Grain: one row per dispute. Business key `chargeback_id`. `reason_code`, `outcome` (WON, LOST, PENDING), `chargeback_original`, `chargeback_amount` (USD at the dispute date's rate).

### `fact_fee`
Grain: one row per captured payment (unique on `transaction_key`).

| Column | Meaning |
|---|---|
| `fee_amount` | Fee charged to the merchant: the processor's revenue. USD |
| `interchange_cost` | Paid to the card issuer. USD |
| `scheme_cost` | Paid to the card network. USD |
| `fee_original` | The merchant fee in the settlement currency; used to reconcile settlements |

### `fact_settlement`
Grain: one row per merchant per payout. Business key `settlement_batch_id`.

| Column | Meaning |
|---|---|
| `gross_amount`, `fees`, `refunds`, `chargebacks`, `net_payout` | What the source system says it paid on and paid out. USD |
| `expected_payout` | What the warehouse calculates from its own facts for the day the payout covers. USD |
| `variance` | `net_payout − expected_payout` |
| `is_reconciled` | True when the payout equals the expectation to the cent in the settlement currency |
| `*_original` | The same amounts in the settlement currency |

A payout on day D covers day D − 1: payments captured that day, minus their fees, minus refunds and chargebacks dated that day.

## Mart (`mart`)

One view per dimension and fact with the same name, exposing only reporting columns, plus:

| Object | Grain | Purpose |
|---|---|---|
| `daily_merchant_summary` (materialized view) | day × merchant version | Counts and USD totals for payments, fees, refunds and chargebacks |
| `etl_rejections` | batch × file × rule | Quarantined rows |
| `etl_batches` | loaded batch | Rows delivered and rejected, controls run and passed, load time |
| `reconciliation_controls` | batch × file × control | Source value, warehouse value, difference, status |
| `region_access` | user × region | Row-level security mapping (placeholder addresses) |

## Audit (`audit`)

| Table | Content |
|---|---|
| `etl_run` | One row per load attempt: batch, status, timing, error. At most one SUCCESS per batch |
| `etl_file` | Per run and file: row count and checksum |
| `rejected_rows` | Quarantined source rows as JSON, with entity, rule, reason and row number |
| `reconciliation` | The controls of every run, including failed runs |
