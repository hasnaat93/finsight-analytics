-- amount_reporting equals amount_original at the latest rate on or before the payment date.
select f.transaction_id
from dwh.fact_transaction f
join dwh.dim_date d on d.date_key = f.date_key
join lateral (
    select r.rate_to_usd from dwh.fx_rate r
    where r.currency_key = f.currency_key and r.rate_date <= d.full_date
    order by r.rate_date desc limit 1
) fx on true
where f.amount_reporting <> round(f.amount_original * fx.rate_to_usd, 2)
