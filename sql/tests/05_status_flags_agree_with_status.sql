-- is_approved and is_captured on a payment agree with its status dimension row.
select f.transaction_id
from dwh.fact_transaction f
join dwh.dim_transaction_status s on s.status_key = f.status_key
where f.is_approved <> s.is_success
   or f.is_captured <> (s.status = 'Captured')
   or (f.is_captured and not f.is_approved)
