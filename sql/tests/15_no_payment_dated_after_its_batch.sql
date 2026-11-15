-- A payment is dated inside the month of the batch that delivered it.
select transaction_id
from dwh.fact_transaction
where to_char(transaction_ts, 'YYYY-MM') <> batch_id
