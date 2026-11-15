-- Recompute the expected payout of every batch from the facts and compare with what the
-- load stored. Catches a reload or late fact that left a stale expectation behind.
with activity as (
    select m.merchant_id, f.date_key, sum(f.amount_original) as amount
    from dwh.fact_transaction f join dwh.dim_merchant m using (merchant_key)
    where f.is_captured group by 1, 2
    union all
    select m.merchant_id, f.date_key, -sum(f.fee_original)
    from dwh.fact_fee f join dwh.dim_merchant m using (merchant_key) group by 1, 2
    union all
    select m.merchant_id, f.date_key, -sum(f.refund_original)
    from dwh.fact_refund f join dwh.dim_merchant m using (merchant_key) group by 1, 2
    union all
    select m.merchant_id, f.date_key, -sum(f.chargeback_original)
    from dwh.fact_chargeback f join dwh.dim_merchant m using (merchant_key) group by 1, 2
),
expected as (
    select merchant_id, date_key, sum(amount) as expected_payout from activity group by 1, 2
)
select s.settlement_batch_id
from dwh.fact_settlement s
join dwh.dim_merchant m using (merchant_key)
join dwh.dim_date d on d.date_key = s.date_key
left join expected e
  on e.merchant_id = m.merchant_id
 and e.date_key = to_char(d.full_date - 1, 'YYYYMMDD')::integer
where s.expected_payout_original <> coalesce(e.expected_payout, 0)
