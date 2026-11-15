-- Payments: conform the staged rows, add any new low-cardinality dimension members, then
-- insert the fact with every key resolved.

drop table if exists staging.transactions_clean;
create unlogged table staging.transactions_clean as
select
    trim(transaction_id)                                                    as transaction_id,
    nullif(trim(merchant_id), '')                                           as merchant_id,
    nullif(trim(customer_id), '')                                           as customer_hash,
    staging.to_ts(transaction_ts)                                           as transaction_ts,
    staging.to_amount(amount)                                               as amount,
    upper(trim(currency))                                                   as currency_code,
    initcap(replace(coalesce(nullif(trim(payment_method), ''), 'Unknown'), '_', ' '))      as payment_method,
    initcap(replace(coalesce(nullif(trim(card_brand), ''), 'Not applicable'), '_', ' '))   as card_brand,
    initcap(replace(coalesce(nullif(trim(card_type), ''), 'Not applicable'), '_', ' '))    as card_type,
    upper(coalesce(nullif(trim(channel), ''), 'UNKNOWN'))                   as channel_code,
    initcap(trim(status))                                                   as status,
    case when upper(trim(status)) = 'DECLINED'
         then initcap(replace(coalesce(nullif(trim(decline_reason), ''), 'Unspecified'), '_', ' '))
         else 'Not declined' end                                            as decline_reason
from staging.transactions_work
where rule is null;

analyze staging.transactions_clean;

insert into dwh.dim_payment_method (payment_method, card_brand, card_type)
select distinct payment_method, card_brand, card_type from staging.transactions_clean
on conflict (payment_method, card_brand, card_type) do nothing;

insert into dwh.dim_transaction_status (status, decline_reason, is_success)
select distinct status, decline_reason, status <> 'Declined' from staging.transactions_clean
on conflict (status, decline_reason) do nothing;

-- * ON CONFLICT DO NOTHING on the business key makes a reload of the batch a no-op.
-- * The merchant key is the version in force on the payment date, not the current version.
-- * A missing or unrecognised merchant, customer or channel resolves to the Unknown member.
-- * The amount is converted at the latest rate on or before the payment date.
insert into dwh.fact_transaction (
    transaction_id, date_key, merchant_key, customer_key, method_key, channel_key, geography_key, currency_key,
    status_key, transaction_ts, hour_of_day, amount_original, amount_reporting, is_approved, is_captured, batch_id)
select
    t.transaction_id,
    to_char(t.transaction_ts, 'YYYYMMDD')::integer,
    coalesce(m.merchant_key, -1),
    coalesce(c.customer_key, -1),
    pm.method_key,
    coalesce(ch.channel_key, -1),
    coalesce(g.geography_key, -1),
    cur.currency_key,
    st.status_key,
    t.transaction_ts,
    extract(hour from t.transaction_ts)::smallint,
    t.amount,
    round(t.amount * fx.rate_to_usd, 2),
    t.status <> 'Declined',
    t.status = 'Captured',
    %(batch_id)s
from staging.transactions_clean t
join dwh.dim_currency cur on cur.currency_code = t.currency_code
join lateral (
    select r.rate_to_usd from dwh.fx_rate r
    where r.currency_key = cur.currency_key and r.rate_date <= t.transaction_ts::date
    order by r.rate_date desc limit 1
) fx on true
join dwh.dim_payment_method pm
  on pm.payment_method = t.payment_method and pm.card_brand = t.card_brand and pm.card_type = t.card_type
join dwh.dim_transaction_status st on st.status = t.status and st.decline_reason = t.decline_reason
left join dwh.dim_merchant m
  on m.merchant_id = t.merchant_id
 and t.transaction_ts::date >= m.valid_from and t.transaction_ts::date < m.valid_to
left join dwh.dim_geography g on g.country_code = m.country_code
left join dwh.dim_customer c on c.customer_hash = t.customer_hash
left join dwh.dim_channel ch on ch.channel_code = t.channel_code
on conflict (transaction_id) do nothing;
