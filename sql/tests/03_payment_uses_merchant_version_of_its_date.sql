-- A payment points at the merchant version that was in force on the payment date.
select f.transaction_id
from dwh.fact_transaction f
join dwh.dim_merchant m on m.merchant_key = f.merchant_key
join dwh.dim_date d on d.date_key = f.date_key
where f.merchant_key <> -1
  and not (d.full_date >= m.valid_from and d.full_date < m.valid_to)
