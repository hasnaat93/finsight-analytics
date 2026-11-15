-- A refund, chargeback or fee carries the same merchant and currency as its payment.
select 'refund' as kind, r.transaction_key
from dwh.fact_refund r join dwh.fact_transaction f using (transaction_key)
where (r.merchant_key, r.currency_key) <> (f.merchant_key, f.currency_key)
union all
select 'chargeback', c.transaction_key
from dwh.fact_chargeback c join dwh.fact_transaction f using (transaction_key)
where (c.merchant_key, c.currency_key) <> (f.merchant_key, f.currency_key)
union all
select 'fee', e.transaction_key
from dwh.fact_fee e join dwh.fact_transaction f using (transaction_key)
where (e.merchant_key, e.currency_key, e.date_key) <> (f.merchant_key, f.currency_key, f.date_key)
