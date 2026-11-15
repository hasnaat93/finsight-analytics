-- Settlement batches whose payout differs from what the warehouse expected, largest first.
select
    s.settlement_batch_id,
    d.full_date                 as settlement_date,
    m.merchant_name,
    c.currency_code,
    s.expected_payout_original  as expected_payout,
    s.net_payout_original       as actual_payout,
    s.net_payout_original - s.expected_payout_original as variance,
    case when s.net_payout_original < s.expected_payout_original then 'Underpaid' else 'Overpaid' end as direction
from dwh.fact_settlement s
join dwh.dim_merchant m on m.merchant_key = s.merchant_key
join dwh.dim_date d on d.date_key = s.date_key
join dwh.dim_currency c on c.currency_key = s.currency_key
where not s.is_reconciled
order by abs(s.variance) desc;
