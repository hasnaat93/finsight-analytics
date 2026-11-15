-- is_reconciled is true exactly when the payout equals the expected payout to the cent.
select settlement_batch_id
from dwh.fact_settlement
where is_reconciled <> (net_payout_original = expected_payout_original)
