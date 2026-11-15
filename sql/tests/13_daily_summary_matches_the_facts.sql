-- The pre-aggregated mart adds up to the facts it was built from.
select 'tpv' as measure
where (select coalesce(sum(tpv), 0) from mart.daily_merchant_summary)
   <> (select coalesce(sum(amount_reporting), 0) from dwh.fact_transaction where is_captured)
union all
select 'transactions'
where (select coalesce(sum(transactions), 0) from mart.daily_merchant_summary)
   <> (select count(*) from dwh.fact_transaction)
union all
select 'fee_revenue'
where (select coalesce(sum(fee_revenue), 0) from mart.daily_merchant_summary)
   <> (select coalesce(sum(fee_amount), 0) from dwh.fact_fee)
union all
select 'refund_amount'
where (select coalesce(sum(refund_amount), 0) from mart.daily_merchant_summary)
   <> (select coalesce(sum(refund_amount), 0) from dwh.fact_refund)
union all
select 'chargeback_amount'
where (select coalesce(sum(chargeback_amount), 0) from mart.daily_merchant_summary)
   <> (select coalesce(sum(chargeback_amount), 0) from dwh.fact_chargeback)
