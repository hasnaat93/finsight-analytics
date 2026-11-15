-- Every captured payment has exactly one fee row, and no fee exists without one.
select f.transaction_id
from dwh.fact_transaction f
left join dwh.fact_fee fee using (transaction_key)
where f.is_captured is distinct from (fee.fee_key is not null)
