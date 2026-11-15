# DAX measures

All 50 measures live in the `_Measures` table of the semantic model ([`powerbi/FinSight.SemanticModel/definition/tables/_Measures.tmdl`](../powerbi/FinSight.SemanticModel/definition/tables/_Measures.tmdl)), grouped into display folders. Each has a description in the model, shown as a tooltip in Power BI Desktop.

This file is generated from the same definitions as the model, so the two cannot drift apart.

## Volume

### Total Transaction Value

Value of captured payments, in USD.

```dax
Total Transaction Value = CALCULATE ( SUM ( fact_transaction[amount_reporting] ), fact_transaction[is_captured] = TRUE () )
```

### Transaction Count

Number of payment attempts, approved or not.

```dax
Transaction Count = COUNTROWS ( fact_transaction )
```

### Approved Transactions

Payment attempts the issuer approved.

```dax
Approved Transactions = CALCULATE ( [Transaction Count], fact_transaction[is_approved] = TRUE () )
```

### Captured Transactions

Approved payments that were captured, so money moved.

```dax
Captured Transactions = CALCULATE ( [Transaction Count], fact_transaction[is_captured] = TRUE () )
```

### Declined Transactions

Payment attempts the issuer declined.

```dax
Declined Transactions = CALCULATE ( [Transaction Count], fact_transaction[is_approved] = FALSE () )
```

### Approval Rate %

Approved payment attempts as a share of all attempts.

```dax
Approval Rate % = DIVIDE ( [Approved Transactions], [Transaction Count] )
```

### Average Ticket Size

Average value of a captured payment, in USD.

```dax
Average Ticket Size = DIVIDE ( [Total Transaction Value], [Captured Transactions] )
```

## Revenue

### Fee Revenue

Fees charged to merchants on captured payments, in USD.

```dax
Fee Revenue = SUM ( fact_fee[fee_amount] )
```

### Processing Cost

Interchange paid to issuers plus scheme fees paid to card networks, in USD.

```dax
Processing Cost = SUM ( fact_fee[interchange_cost] ) + SUM ( fact_fee[scheme_cost] )
```

### Chargeback Losses

Value of disputes the merchant lost, in USD.

```dax
Chargeback Losses = CALCULATE ( SUM ( fact_chargeback[chargeback_amount] ), fact_chargeback[outcome] = "LOST" )
```

### Net Revenue

Fee revenue less processing cost and lost disputes.

```dax
Net Revenue = [Fee Revenue] - [Processing Cost] - [Chargeback Losses]
```

### Take Rate %

Fee revenue as a share of transaction value.

```dax
Take Rate % = DIVIDE ( [Fee Revenue], [Total Transaction Value] )
```

### Net Take Rate %

Net revenue as a share of transaction value.

```dax
Net Take Rate % = DIVIDE ( [Net Revenue], [Total Transaction Value] )
```

## Risk

### Total Refunds

Value refunded to customers, in USD.

```dax
Total Refunds = SUM ( fact_refund[refund_amount] )
```

### Refund Count

Number of refunds.

```dax
Refund Count = COUNTROWS ( fact_refund )
```

### Refund Rate %

Refunded value as a share of transaction value.

```dax
Refund Rate % = DIVIDE ( [Total Refunds], [Total Transaction Value] )
```

### Total Chargebacks

Value disputed by cardholders, in USD, whatever the outcome.

```dax
Total Chargebacks = SUM ( fact_chargeback[chargeback_amount] )
```

### Chargeback Count

Number of disputes.

```dax
Chargeback Count = COUNTROWS ( fact_chargeback )
```

### Chargeback Rate %

Disputes as a share of approved payments.

```dax
Chargeback Rate % = DIVIDE ( [Chargeback Count], [Approved Transactions] )
```

### Chargebacks Lost %

Share of decided disputes the merchant lost. Pending disputes are excluded.

```dax
Chargebacks Lost % =
DIVIDE (
    CALCULATE ( [Chargeback Count], fact_chargeback[outcome] = "LOST" ),
    CALCULATE ( [Chargeback Count], fact_chargeback[outcome] IN { "LOST", "WON" } )
)
```

### Decline Rate %

Declined payment attempts as a share of all attempts.

```dax
Decline Rate % = DIVIDE ( [Declined Transactions], [Transaction Count] )
```

## Time Intelligence

### TPV MTD

Transaction value, month to date.

```dax
TPV MTD = TOTALMTD ( [Total Transaction Value], dim_date[date] )
```

### TPV QTD

Transaction value, quarter to date.

```dax
TPV QTD = TOTALQTD ( [Total Transaction Value], dim_date[date] )
```

### TPV YTD

Transaction value, year to date.

```dax
TPV YTD = TOTALYTD ( [Total Transaction Value], dim_date[date] )
```

### TPV PY

Transaction value in the same period of the previous year.

```dax
TPV PY = CALCULATE ( [Total Transaction Value], SAMEPERIODLASTYEAR ( dim_date[date] ) )
```

### TPV YoY %

Change in transaction value against the same period of the previous year.

```dax
TPV YoY % = DIVIDE ( [Total Transaction Value] - [TPV PY], [TPV PY] )
```

### TPV Rolling 30 Days

Transaction value over the 30 days ending on the last visible date.

```dax
TPV Rolling 30 Days =
CALCULATE (
    [Total Transaction Value],
    DATESINPERIOD ( dim_date[date], MAX ( dim_date[date] ), -30, DAY )
)
```

### Net Revenue PY

Net revenue in the same period of the previous year.

```dax
Net Revenue PY = CALCULATE ( [Net Revenue], SAMEPERIODLASTYEAR ( dim_date[date] ) )
```

### Net Revenue YoY %

Change in net revenue against the same period of the previous year.

```dax
Net Revenue YoY % = DIVIDE ( [Net Revenue] - [Net Revenue PY], [Net Revenue PY] )
```

## Merchant

### Active Merchants

Merchants with at least one captured payment. Counts merchants, not their versions.

```dax
Active Merchants =
COUNTROWS (
    FILTER (
        VALUES ( dim_merchant[merchant_id] ),
        dim_merchant[merchant_id] <> "UNKNOWN" && [Captured Transactions] > 0
    )
)
```

### Merchant TPV Rank

Rank of the merchant by transaction value among the merchants in view.

```dax
Merchant TPV Rank =
IF (
    HASONEVALUE ( dim_merchant[merchant_name] ),
    RANKX ( ALLSELECTED ( dim_merchant[merchant_name] ), [Total Transaction Value], , DESC )
)
```

### At-Risk Merchants

Merchants whose transaction value fell more than 25% against the previous year.

```dax
At-Risk Merchants =
COUNTROWS (
    FILTER (
        VALUES ( dim_merchant[merchant_id] ),
        NOT ISBLANK ( [TPV PY] ) && [TPV YoY %] < -0.25
    )
)
```

### Top 10 Merchant Share %

Share of transaction value produced by the ten largest merchants in view.

```dax
Top 10 Merchant Share % =
VAR Top10 =
    TOPN ( 10, ALLSELECTED ( dim_merchant[merchant_name] ), [Total Transaction Value] )
RETURN
    DIVIDE (
        CALCULATE ( [Total Transaction Value], Top10 ),
        CALCULATE ( [Total Transaction Value], ALLSELECTED ( dim_merchant[merchant_name] ) )
    )
```

### Merchant Share of TPV %

Transaction value of the merchant as a share of all merchants in view.

```dax
Merchant Share of TPV % =
DIVIDE (
    [Total Transaction Value],
    CALCULATE ( [Total Transaction Value], ALLSELECTED ( dim_merchant ) )
)
```

## Settlement

### Expected Payout

What the warehouse calculates merchants should have been paid, in USD.

```dax
Expected Payout = SUM ( fact_settlement[expected_payout] )
```

### Actual Payout

What the source system says merchants were paid, in USD.

```dax
Actual Payout = SUM ( fact_settlement[net_payout] )
```

### Settlement Variance

Actual payout less expected payout. Negative means merchants were underpaid.

```dax
Settlement Variance = [Actual Payout] - [Expected Payout]
```

### Absolute Settlement Variance

Sum of the size of every variance, so over- and underpayments do not cancel.

```dax
Absolute Settlement Variance = SUMX ( fact_settlement, ABS ( fact_settlement[variance] ) )
```

### Settlement Batches

Number of merchant payouts.

```dax
Settlement Batches = COUNTROWS ( fact_settlement )
```

### Unreconciled Batches

Payouts that differ from the expected payout.

```dax
Unreconciled Batches = CALCULATE ( [Settlement Batches], fact_settlement[is_reconciled] = FALSE () )
```

### Reconciliation Rate %

Share of payouts that match the expected payout exactly.

```dax
Reconciliation Rate % = 1 - DIVIDE ( [Unreconciled Batches], [Settlement Batches] )
```

## Data Quality

### Rows Delivered

Rows in the source files of the loaded batches.

```dax
Rows Delivered = SUM ( etl_batches[rows_delivered] )
```

### Rows Rejected

Rows quarantined by a validation rule.

```dax
Rows Rejected = SUM ( etl_rejections[rejected_rows] )
```

### Rejection Rate %

Quarantined rows as a share of delivered rows.

```dax
Rejection Rate % = DIVIDE ( SUM ( etl_batches[rows_rejected] ), [Rows Delivered] )
```

### Batches Loaded

Monthly batches loaded into the warehouse.

```dax
Batches Loaded = COUNTROWS ( etl_batches )
```

### Controls Passed %

Reconciliation controls that balanced, as a share of controls run.

```dax
Controls Passed % = DIVIDE ( SUM ( etl_batches[controls_passed] ), SUM ( etl_batches[controls_run] ) )
```

### Last Refresh

When the most recent batch finished loading.

```dax
Last Refresh = MAX ( etl_batches[loaded_at] )
```

## Report Helpers

### Selected KPI

The measure chosen in the Metric Selector slicer.

```dax
Selected KPI =
SWITCH (
    SELECTEDVALUE ( 'Metric Selector'[Metric] ),
    "Transaction Value", [Total Transaction Value],
    "Net Revenue", [Net Revenue],
    "Transactions", [Transaction Count],
    [Total Transaction Value]
)
```

### KPI Colour

Green when transaction value is up on the previous year, red when down. For conditional formatting.

```dax
KPI Colour = IF ( [TPV YoY %] >= 0, "#1B8E5A", "#C0392B" )
```

### Dynamic Title

Chart title that names the selected year.

```dax
Dynamic Title = "Transaction value: " & SELECTEDVALUE ( dim_date[year], "all years" )
```

## Time intelligence calculation group

The `Time Intelligence` calculation group applies the same time logic to any measure. Put `Time Calculation` on the columns of a matrix and any measure in the values: one measure, seven views.

| Item | Expression |
|---|---|
| Current | `SELECTEDMEASURE ()` |
| MTD | `CALCULATE ( SELECTEDMEASURE (), DATESMTD ( dim_date[date] ) )` |
| QTD | `CALCULATE ( SELECTEDMEASURE (), DATESQTD ( dim_date[date] ) )` |
| YTD | `CALCULATE ( SELECTEDMEASURE (), DATESYTD ( dim_date[date] ) )` |
| PY | `CALCULATE ( SELECTEDMEASURE (), SAMEPERIODLASTYEAR ( dim_date[date] ) )` |
| YoY | `VAR Previous = CALCULATE ( SELECTEDMEASURE (), SAMEPERIODLASTYEAR ( dim_date[date] ) ) RETURN SELECTEDMEASURE () - Previous` |
| YoY % | `VAR Previous = CALCULATE ( SELECTEDMEASURE (), SAMEPERIODLASTYEAR ( dim_date[date] ) ) RETURN DIVIDE ( SELECTEDMEASURE () - Previous, Previous )` |

## Row-level security

Role `Regional Manager` filters `dim_geography` to the regions listed for the signed-in user in `region_access`:

```dax
dim_geography[region]
    IN CALCULATETABLE (
        VALUES ( region_access[region] ),
        region_access[user_email] = USERPRINCIPALNAME ()
    )
```
