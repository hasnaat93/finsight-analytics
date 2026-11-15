-- Headline totals straight from the warehouse. These are the numbers a report must show:
-- docs/validation.md compares them with the Power BI model, measure by measure.
select
    (select sum(amount_reporting) from dwh.fact_transaction where is_captured)             as total_transaction_value,
    (select count(*) from dwh.fact_transaction)                                            as transaction_count,
    (select count(*) from dwh.fact_transaction where is_approved)                          as approved_transactions,
    (select round(100.0 * count(*) filter (where is_approved) / count(*), 4)
       from dwh.fact_transaction)                                                          as approval_rate_pct,
    (select sum(fee_amount) from dwh.fact_fee)                                             as fee_revenue,
    (select sum(interchange_cost + scheme_cost) from dwh.fact_fee)                         as processing_cost,
    (select coalesce(sum(chargeback_amount), 0) from dwh.fact_chargeback where outcome = 'LOST') as chargeback_losses,
    (select sum(fee_amount) - sum(interchange_cost + scheme_cost) from dwh.fact_fee)
      - (select coalesce(sum(chargeback_amount), 0) from dwh.fact_chargeback where outcome = 'LOST') as net_revenue,
    (select sum(refund_amount) from dwh.fact_refund)                                       as total_refunds,
    (select count(*) from dwh.fact_chargeback)                                             as chargeback_count,
    (select count(distinct m.merchant_id) from dwh.fact_transaction f
       join dwh.dim_merchant m using (merchant_key)
      where f.is_captured and m.merchant_key <> -1)                                        as active_merchants,
    (select sum(expected_payout) from dwh.fact_settlement)                                 as expected_payout,
    (select sum(net_payout) from dwh.fact_settlement)                                      as actual_payout,
    (select count(*) from dwh.fact_settlement)                                             as settlement_batches,
    (select count(*) from dwh.fact_settlement where not is_reconciled)                     as unreconciled_batches,
    (select count(*) from audit.rejected_rows)                                             as rows_rejected;
