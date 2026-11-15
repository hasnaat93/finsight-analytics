-- No single refund is larger than the payment it refunds.
select r.refund_id
from dwh.fact_refund r
join dwh.fact_transaction f using (transaction_key)
where r.refund_original > f.amount_original
