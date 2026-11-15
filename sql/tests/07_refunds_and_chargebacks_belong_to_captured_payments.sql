-- Money only comes back on a payment that was captured.
select 'refund' as kind, r.refund_id as id
from dwh.fact_refund r join dwh.fact_transaction f using (transaction_key)
where not f.is_captured
union all
select 'chargeback', c.chargeback_id
from dwh.fact_chargeback c join dwh.fact_transaction f using (transaction_key)
where not f.is_captured
